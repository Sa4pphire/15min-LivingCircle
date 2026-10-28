# 合成模拟：C++ 步行路网与近似等时圈

[返回项目主页](../README.md) · [在线计算算法](../backend/README.md) · [引擎 v2 契约](../contracts/engine-v2.README.md)

本文对应页面中的“合成模拟”。Python 加载本地区域路网，C++ 在图上计算最短步行耗时、可达街段和设施路径，再将结果转成前端可绘制的近似等时圈面。C++ 不调用百度 API，不读取 AK。

默认路网位于 [data/regions/shanghai-new-jiangwan/network.json](../data/regions/shanghai-new-jiangwan/network.json)，随 `REGION_ID` 或所选区域包切换。它包含地理道路来源和人工编辑，但仍是**未实地核实的合成模型**；计算在图上正确，不意味着现实通行关系已经确认。

## 1. 整体流程

```text
地图选点
  → Python 校验区域、转换局部米制坐标、组装 v2 JSON
  → C++ 解析并校验图，投影起点及设施入口、拆分步行边
  → Dijkstra 计算最短耗时，按 900 秒截取街段
  → 生成近似 MultiPolygon、设施耗时及可选类别灰段
  → Python 转换地图坐标并适配报告
  → 前端显示等时圈和 POI，点击 POI 时按需计算路径
```

默认 CLI 通过标准输入接收一个 JSON 对象，标准输出返回一个 JSON 对象；日志只写标准错误。Python 单次引擎子进程调用上限为 25 秒，这不等于包含 POI 补充的完整任务保证在 25 秒内结束。可选的 Python RPC 接入见第14节，仍沿用 v2 JSON。

## 2. 路网模型

坐标使用局部米制平面，X 向东、Y 向北。节点 ID、边的 `from/to` 和显式连接决定拓扑：几何相交、坐标重合或道路名称相同，不会自动连通。当前边均可双向步行。

所有步行通道统一为 `walkway`，以 `accessMode` 保留接入规则：

| 类型 | 含义与主要字段 | 耗时 |
| --- | --- | --- |
| `walkway` + `separated` | 普通道路的分侧通道；保留 `streetBlockId` 和 `side: left/right` | 折线长度 ÷ 步速 |
| `walkway` + `shared` | 共享步行通道的中心线；保留 `streetBlockId`、`sharedWayType` 和 `widthMeters` | 折线长度 ÷ 步速；横向接入另计距离 |
| `turn` | 同侧转弯等显式连接，不代替横穿普通道路 | 折线长度 ÷ 步速 |
| `crossing` | 显式过街连接，可设独立 `waitSeconds` | 折线长度 ÷ 步速 + 等待时间 |

普通道路的两侧不能通过共用节点或 `turn` 免费换侧，必须经过 `crossing`。统一类型没有删除节点、边或连接关系，也不意味着取消分侧约束。旧输入的 `sidewalk` 和 `shared_way` 仍兼容读取，新输出统一使用 `walkway`；详见 [walkway 迁移说明](../docs/unified-walkways.md)。

### 当前 Python 演示参数

| 参数 | 默认值 |
| --- | --- |
| `thresholdSeconds` | 900 秒 |
| `walkingSpeedMetersPerSecond` | 1.3 米／秒 |
| `crossingWaitSeconds` | 20 秒；单条 crossing 的 `waitSeconds` 优先 |
| `allowOffNetworkOrigin` | 合成模型为 `true`，严格真实模型为 `false` |
| `maxOriginSnapMeters` | 合成模型 1170 米，严格真实模型 5 米 |
| `displayAreaRadiusMeters` | 80 米 |
| `displayBufferMeters` | 15 米，用于转弯／过街连接的较窄展示 |
| `displayGridStepMeters` | 10 米 |
| `displayMinHoleAreaSquareMeters` | 2500 平方米 |

这些是 [Python 调用适配](../backend/app/network.py)中的值，不是所有参数都能通过网页请求任意设置。引擎独立 v2 输入的默认吸附上限为 30 米且不允许路外起点；共享通道严格吸附另按估计路面半宽外 3 米判断。

## 3. 起点接入与临时拆边

起点先投影到最近的可接入步行边。不同且不共享端点的候选接近到无法明确区分时，返回歧义错误，要求指定 `originEdgeId`；指定边仍要满足距离和所在侧检查，不能任选远处道路绕过过街。

合成模式允许估算一条起点到投影点的直线接入路径，横向距离计入时间：

```text
起点接入时间 = 起点到路的距离 / 1.3
路网剩余预算 = 900 - 起点接入时间
```

例如到路需要 300 秒，图上的剩余预算就是 600 秒。接入本身耗尽时间预算时返回错误。这只是无阻隔的合成假设，结果带 `UNVERIFIED_STRAIGHT_LINE_ORIGIN_ACCESS`；不能据此声称能穿过围墙、建筑或水体。

起点和所有设施入口统一按边内投影位置插入临时节点。按沿线距离排序后拆分边，相同位置复用同一个临时节点；原始道路 ID 与几何保持来源关系，不修改保存的路网文件。这样不必先走到原始端点再折返到入口。

## 4. Dijkstra、可达街段与设施耗时

边权均非负：步行边和转弯边为长度／步速，过街边再加等待。Dijkstra 的起点初始距离不是固定 0，而是上一步的接入时间。由此得到每个节点从用户选点出发的最短耗时。

对于步行／转弯边，按两端节点的到达时间沿边推算连续可达区间，在 900 秒处截断；两端都能进入的区间会合并。过街边只有剩余时间足以完整走完并支付等待时才作为可达边输出，不把马路中央当成有效终点。边内截断点及恰好达到时间阈值的节点写入 `frontierMeters`。

设施可以提供多个 `entrances`，每个入口绑定 `accessEdgeId`、`streetAccessPointMeters`，并可提供 `accessPathMeters`。离街接入折线长度必须计时；引擎不会把多个入口互相连接成穿过设施地块的捷径。

```text
设施耗时 = min(各入口的路网到达耗时 + 该入口接入路径耗时)
```

900 秒以内为 `reachable: true`；连通但超时仍返回实际秒数，不连通才返回 `null`。结果包含最佳入口 ID。可达性统计使用这个路网结果，不能用近似面是否包住设施来代替。

流程组织见 [engine.cpp](src/engine.cpp)，Dijkstra 见 [shortest_path.cpp](src/shortest_path.cpp)，设施与路线见 [facility_analysis.cpp](src/facility_analysis.cpp)；类型由 [engine.hpp](include/isochrone/engine.hpp)统一导出，模块索引见第13节。优先队列 Dijkstra 的常规复杂度为 `O((V + E) log V)`，其中包含本轮临时拆边节点；整次计算还包括几何生成，不能只用寻路复杂度估计总耗时。

## 5. 近似等时圈面如何生成

当前输出不是固定半径圆，也不只是给道路加一圈边框，而是从可达道路生成网格时间场：

1. 对道路附近的网格点，估算“到达道路投影点的路网耗时 + 横向距离／步速”。常规街段向外最多扩展 80 米，并受剩余时间限制；转弯／过街连接采用较窄的展示半径。
2. 对被不同方向可达街道夹住的未标路网内部，允许在常规半径 3 倍以内寻找相对方向的接近证据；仍须满足 900 秒预算，不作无条件填充。
3. 用 [road_enclosure.cpp](src/road_enclosure.cpp) 按显式图连接检查完整可达的闭合道路面；默认围合尺度上限为 480 米，内部补充仍受时间场限制。几何交叉但没有图连接不算闭环。
4. Marching Squares 提取轮廓、组环并区分外环与内洞。面积小于 2500 平方米的内洞可作为展示碎片填平；较大的有限尺度内洞只有内部网格也满足接近耗时条件才填补。并清理填洞后重复的内部岛面。

`displayMinHoleAreaSquareMeters=0` 关闭按面积直接填小洞，但不会关闭其它基于时间场的围合补充。

**这一面是展示估算，不是严格的地块可通行面。** 当前没有完整的围墙、建筑、水体和出入口阻隔模型；尤其填洞会改变展示面积，但不会改变 Dijkstra 路由或设施耗时。大型未知空隙不应靠任意填满来“修复”。

## 6. POI 点击寻路与性能处理

前端默认不绘制等时圈内全部道路，保留 POI；用户点击设施后才请求路线。Python 复用本次分析的图和起点，向引擎传 `routeFacilityId` 与 `routeOnly: true`。

C++ 在同一临时拆分图上运行 Dijkstra，根据前驱链还原从起点到最佳入口的路线，追加起点和设施接入路径，返回总耗时、总长度、等待秒数与逐段折线。`routeOnly` 跳过网格、等时圈和类别覆盖计算；`facilitiesOnly` 则只计算设施耗时，也跳过展示几何。

合成模式的 POI 缺少可用绑定入口时，Python 可估算到附近步行边的直线接入：候选限定在最近距离附近的 10 米距离带内，最多 16 个，保留普通道路侧约束并检查是否横穿已建模的分侧通道。C++ 比较“路网 + 接入”的总耗时，而不是只选择最容易到达的远处入口。此估算只接到目的地，不给原路网增加通道；未知地块阻隔仍是限制，路线标记 `estimated_straight_line` 和未核实警告。

设施导航点、展示点和实际入口是不同概念；没有可信接入的严格真实模型应返回不可用，不套用合成直线策略。实现见 [poi_routes.py](../backend/app/poi_routes.py)。

主分析先发布等时圈，再补充 POI。默认 POI 阶段优先读取所选区域包的 `pois.json` 地点快照，旧包没有快照时只读本机已有缓存；显式刷新才发起新的在线查询。前端在全部计算完成后才显示范围内地点，进入页面和计算过程中隐藏。未核实类别无需重新生成面，补充设施时使用 `facilitiesOnly`，避免重复栅格化。结果修订与阶段计时供前端增量更新，不应触发地图自动重置比例尺。

## 7. 类别灰段与数据不足

引擎支持按设施类别的入口运行多源 Dijkstra，入口初始成本包含接入路径。设起点 900 秒内可达步行街段为 `R`，某类别所有入口在 900 秒内可服务的街段为 `S`，灰段就是沿边连续区间的 `R \ S`，再生成单独的近似展示面。圈外设施只要能服务圈内街段，也可以参加计算。

只有 `serviceCategories[].dataStatus: "reviewed_online"` 的类别才计算疑似灰段，返回 `candidate`；未核齐的类别返回 `data_insufficient`，不能将空清单解释为匮乏。当前自动获取的 POI 清单与入口未经核实，默认走数据不足分支。

这与[在线模式](../backend/README.md#5-疑似服务盲区)的“1000 米直线服务圆”不是同一覆盖口径。报告里的“面内 POI 数量”和“图上 900 秒可达设施数量”也应分别展示。

## 8. 接口与结果

| 接口 | 用途 |
| --- | --- |
| `POST /api/v1/synthetic-analyses` | 提交 `center`、`regionId`，可带 `originEdgeId`、`includePois`、`refreshPois` |
| `GET /api/v1/analyses/{analysisId}` | 查询分析状态及分阶段结果，`afterRevision` 避免重复传完整地图结果 |
| `POST /api/v1/analyses/{analysisId}/poi-route` | 提交 `poiId`，查询选中设施的 C++ 路径 |

Python ↔ C++ 使用 `schemaVersion: 2`。先检查输出 `success`；失败读取 `error.code`，成功读取 `result`：

| 引擎字段 | 用途 |
| --- | --- |
| `reachableEdges` / `frontierMeters` | 图上可达折线与时间边界点 |
| `snappedOriginMeters` / `originAccessSeconds` | 起点吸附位置与接入成本 |
| `displayGeometryMeters` | 局部米制 MultiPolygon，含外环与内洞；旧 `displayPolygonMeters` 保留兼容 |
| `facilityTravelTimes` | 逐设施耗时、可达性和最佳入口 |
| `facilityRoute` | 按需路线、路径段、长度与过街等待 |
| `grayZones` | 类别灰段、长度比例、近似面及数据状态 |
| `diagnostics` | 构建模式、阶段计时、节点／过街／闭环统计和警告 |

输入输出样例：[engine-input.example.json](../contracts/engine-input.example.json)、[engine-output.example.json](../contracts/engine-output.example.json)。样例用于字段说明，不保证两份文件是同一轮计算的配对结果。

`displayGeometryMeters` **不是可直接传给百度地图的经纬度 GeoJSON**：其坐标仍为米数。Python 按区域包的原点和坐标类型转换，前端用该区域的校准模型对齐 BD-09 底图。本地 SVG 坐标 Y 向下，转换时也不能遗漏方向差异。

任务状态在进程内存中；服务重启或路线上下文过期后重新分析。区域包范围来自道路数据外接范围，不是公共步行空间边界；外围路网不足可能截断结果，保留 `NETWORK_EXTENT_LIMITS_RESULTS` 提示。区域格式见 [区域包说明](../docs/region-packages.md)。

## 9. 构建与独立运行

项目整体部署优先使用[主页启动方式](../README.md#部署与运行)。仅构建引擎时，从仓库根目录执行：

```bash
cmake -S cpp-engine -B cpp-engine/build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build cpp-engine/build --config Release --parallel
```

单配置构建的可执行文件通常在 `cpp-engine/build/isochrone_engine`；Windows 添加 `.exe`，Visual Studio 等多配置构建通常在 `cpp-engine/build/Release/isochrone_engine.exe`。手动启动后端时，`CPP_ENGINE_PATH` 应指向实际产物。

以单配置 Linux/macOS 构建为例：

```bash
./cpp-engine/build/isochrone_engine --health
./cpp-engine/build/isochrone_engine --network data/regions/shanghai-new-jiangwan/network.json
./cpp-engine/build/isochrone_engine --input contracts/engine-input.example.json
```

`--network` 只接受 `synthetic: true` 的图，默认起点为局部 `(0, 0)`；指定其它位置可加 `--origin-meters X Y` 和可选 `--origin-edge EDGE_ID`。不带文件参数时从标准输入读取完整 v2 请求，Python 使用的就是这条链路。

`--demo` 优先读取 `SYNTHETIC_NETWORK_PATH`，未设置时使用构建中记录的历史 `data/networks/synthetic-preview.json`，**不自动等同于当前网页区域包**。移动项目或切换区域时，推荐用显式 `--network` 或正确配置路径，避免误用另一份图。

另有独立 `--local` / `/api/v1/local-experiments` 的 180 秒严格局部实验，不是主页的第三种模式。它要求标注裁剪出口及局部核查状态，不生成完整 15 分钟展示面；缺少设施或核查信息时只能输出未知／数据不足。字段说明见 [v2 契约](../contracts/engine-v2.README.md)与[局部输入样例](../contracts/engine-local-experiment.input.example.json)。

发布仓库不包含测试文件，CMake 默认关闭本地测试构建；`BUILD_TESTING=ON` 仅用于本机另有测试目录的开发环境。编译、健康检查或合成演示通过，都不等于真实路网精度已经验收。旧 IDW 模块保留供历史实验，不参与本模式的 v2 路网主流程。

## 10. 第一阶段结构：路网、请求与临时计算状态

这次内部结构调整将可复用的基础路网与单次分析参数分开，为后续常驻计算服务明确数据归属。当前 Python 默认仍通过标准输入输出调用单次 C++ 子进程；v2 JSON 的字段、CLI 参数和结果封套继续沿用现有契约。第二阶段的独立可选 RPC 入口见下一节。

| 类型或状态 | 持有的数据 | 计算时的访问方式 |
| --- | --- | --- |
| [RoadGraph](include/isochrone/road_graph.hpp) | 基础节点、道路、几何与道路接入属性 | 通过 `const RoadGraph&` 只读借用 |
| [AnalysisRequest](include/isochrone/analysis_request.hpp) | 起点、阈值、步速、默认过街等待、展示参数、本轮设施和类别、路线目标及局部实验说明 | 通过 `const AnalysisRequest&` 只读借用 |
| [EngineInput](include/isochrone/engine.hpp) | 协议版本，以及上述两个拥有数据的对象 | 负责将外部 v2 请求交给计算入口 |
| 单次计算的局部变量 | 临时节点和拆边标记、入口绑定、邻接表、最短耗时、前驱和结果 | 由一次函数调用独立持有，返回后释放不再需要的中间状态 |

内部组合与核心入口如下：

```cpp
struct EngineInput {
  int schema_version{2};
  RoadGraph graph;
  AnalysisRequest request;
};

EngineResult compute_reachability(
    const RoadGraph& graph,
    const AnalysisRequest& request);
```

沿一次请求阅读代码：

1. [解析器](src/json_parser.cpp)读取原有 v2 JSON，将 `nodes/edges` 放入 `graph`，将计算参数、设施和局部实验字段放入 `request`。普通请求的必填字段、局部请求缺省 180 秒，以及独立合成文件的参数规则保持原有语义。
2. `validate_graph(const EngineInput&)` 保留为完整输入校验入口，先检查协议版本，再调用[内部联合校验](src/input_validation.cpp) `validate_input(graph, request, control)`。校验继续按“计算参数 → 节点 → 局部实验 → 道路与拓扑 → 类别和设施”的顺序执行；设施入口和局部边界都引用路网，不能只检查各自字段。兼容入口使用空控制探针。
3. 单参数 `compute_reachability(input)` 保留为兼容入口，检查协议版本后，以 `input.graph` 和 `input.request` 的常量引用转交双参数入口。包装层不复制整张图，也不额外再做一次完整校验；双参数入口仍在原 `graphValidation` 阶段执行联合校验。
4. 计算过程按本次起点和设施入口生成临时拆分图与边权。邻接表包含本轮拆边位置、步速及过街等待，不能整份作为所有请求共用的基础路网。距离、前驱与输出也保持请求私有。
5. [CLI 入口](src/main.cpp)继续记录解析阶段、追加合成／局部实验警告，并通过[序列化器](src/json_io.cpp)生成原有结果。`inputParse`、`graphValidation`、`graphBuild` 等计时名称与阶段含义保留；结构调整不提供新的运行耗时结论。

设施和类别可能来自本轮 POI 补充，仍属于请求；`local_experiment` 也整体留在请求中，携带本轮使用的边界节点和核查状态。此阶段不将它们随路网缓存，也不提前拆分或重解释局部实验协议。

`const` 表示计算函数通过这两个引用只读访问，并不自动保证其它持有者不会修改对象。调用方必须让路网和请求活到计算结束，期间不得并发修改；未来若跨线程共享，还需要另行设计持有方式和版本切换。

第一阶段只调整内部数据与函数边界，没有加入缓存、并发执行或常驻 RPC，也没有性能提升证据。第一阶段仅做源码和差异审阅，未运行测试、构建、服务或程序验证。本机旧测试中仍有 `EdgeKind::sidewalk/shared_way` 枚举引用，与当前枚举不匹配；该既有问题保留，不据此宣称旧测试已经通过。

后续仅在用户明确授权后执行回归，检查以下场景：

- stdin JSON、`--input`、`--network`、`--demo`、`--local`、起点覆盖和 `--health`；核对各模式的默认值、警告及退出码。
- 普通分析、`routeOnly`、`facilitiesOnly`、局部实验，以及历史道路字符串、单入口和多入口输入的兼容语义。
- 非法协议版本、参数、图、边界节点和设施入口；同一输入包含多处错误时，核对首先报告的错误。
- 单参数与双参数入口的业务结果一致；同一图连续服务不同起点、步速和设施请求，确认图与旧结果不被后续计算修改。计时字段与阶段名称保持，耗时数值不要求相同。

## 11. 第二阶段：独立的单任务 C++ RPC 服务

第二阶段新增可选的常驻 gRPC 服务与最小 C++ 调用示例，优先面向 Linux／WSL2。进程可以连续接收计算请求，用来学习网络调用、非阻塞准入和资源生命周期。Python、现有 CLI 与 Docker 演示保留原默认路径；第二阶段时服务没有接入网页，第四阶段的可选 Python 接入见第14节。没有增加路网缓存、计算线程池或数据库。第二阶段当时只检查计算前后的取消；第三阶段新增的计算链协作取消见第12节。本节保留单任务阶段的设计记录，当前可配置计算容量见第15节。

### 11.1 构建前提与独立入口

默认 `BUILD_RPC_SERVER=OFF`，原构建路径不查找 gRPC 或 Protobuf。开启后才要求系统已安装相互匹配的 gRPC、Protobuf、`protoc` 和 `grpc_cpp_plugin`，并且 CMake 能找到它们的包配置与工具目标；缺失依赖会明确报错，不自动下载或安装。依赖准备参考 [gRPC C++ 官方快速开始](https://grpc.io/docs/languages/cpp/quickstart/)，安装前缀通过 `CMAKE_PREFIX_PATH` 提供。

以下命令从仓库根目录执行，仅作为用户明确授权后的构建与调用说明；本轮没有执行安装、代码生成、编译、客户端或服务：

```bash
cmake -S cpp-engine -B cpp-engine/build/rpc \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=OFF \
  -DBUILD_RPC_SERVER=ON \
  -DCMAKE_PREFIX_PATH=/path/to/grpc-prefix
cmake --build cpp-engine/build/rpc \
  --target isochrone_rpc_server isochrone_rpc_client --parallel
```

将示例前缀替换为实际安装位置；若包已处于 CMake 默认搜索路径，可以省略该参数。[协议文件](rpc/engine_service.proto)属于源码，生成的 `.pb.*` 和 `.grpc.pb.*` 写入本次构建目录下的 `rpc-generated/`，不提交生成产物。

服务固定监听回环地址 `127.0.0.1`，使用本机明文连接，默认端口为 `50051`。在一个终端启动：

```bash
./cpp-engine/build/rpc/isochrone_rpc_server --port 50051
```

另一个终端调用健康检查或提交完整的 v2 输入：

```bash
./cpp-engine/build/rpc/isochrone_rpc_client health --port 50051
./cpp-engine/build/rpc/isochrone_rpc_client compute --port 50051 \
  < contracts/engine-input.example.json
```

省略 `--port` 时两端均使用 `50051`。`compute` 从标准输入有界读取至多 20,000,000 字节；示例客户端的计算 deadline 为 25 秒，健康检查为 3 秒。计算 deadline 在完整读取 stdin 后、发起 RPC 前设置，不包含等待标准输入的时间；stdin 有字节上限，但没有读取时间上限。这些值限制 RPC 等待，不是服务端 CPU 计算的强制终止时间。

### 11.2 接口与一次请求的流程

Protobuf 包为 `livingcircle.engine.v1`，服务为 `EngineService`：

| 方法 | 请求 | 响应 |
| --- | --- | --- |
| `Compute` | `ComputeRequest`，`bytes request_json = 1` | `JsonReply`，`bytes response_json = 1` |
| `Health` | 空的 `HealthRequest` | `JsonReply`，内容为原有 `health_json()` |

`Compute` 携带完整、平铺的 v2 JSON，每次都包含路网并重新解析；当前没有上传图、图 ID 或跨请求图复用接口。`Health` 不占计算名额、不执行算法，只反映 RPC 入口及其编译能力，不能当作算法正确性或容量验收。

第二阶段的请求流程为：先检查关闭与取消，再非阻塞争取唯一计算名额；取得后重新检查状态，调用现有 JSON 解析器和计算入口，追加 `inputParse` 计时，最后生成 v2 结果。第三阶段为该计算入口传入本次请求的 `ExecutionControl`，流程见第12节。第五阶段的[服务实现](rpc/engine_service.cpp)保留该流程，以可配置计算门替代单槽；原有 `graphValidation`、`graphBuild` 等阶段继续由计算核心记录。

### 11.3 名额、错误与消息大小

第二阶段的单计算名额使用原子状态和 RAII 管理，覆盖业务 JSON 解析、CPU 计算与应用响应构造；已有任务未真实结束时，后续 `Compute` 直接返回 `RESOURCE_EXHAUSTED`，不在应用中排队。第五阶段将可用名额扩展为配置容量，满额仍按相同方式拒绝。处理函数退出后，gRPC 的实际发送可能仍在继续，因此这个名额不代表传输资源的完整生命周期。

同步 gRPC 配置一个完成队列、两个最小和最大 poller，让计算期间仍有接待拒绝请求和健康检查的能力。这些是框架接待线程参数，不能解释为全局在途 RPC、连接数、传输积压或总内存的严格上限。

| 返回层 | 语义 |
| --- | --- |
| gRPC `OK`，JSON `success:true` | 业务计算返回成功结果 |
| gRPC `OK`，JSON `success:false` | 沿用 CLI 的业务错误封套；需检查 `error.code` |
| `RESOURCE_EXHAUSTED` | 计算名额繁忙或消息超过相应传输限制 |
| `UNAVAILABLE` | 服务已停止受理或不可达 |
| `CANCELLED`／`DEADLINE_EXCEEDED` | 调用已取消或等待期限已过 |
| `INTERNAL` | RPC 处理或错误响应构造失败，返回通用错误信息 |

业务错误保留 `ORIGIN_NOT_ON_WALKWAY`、`AMBIGUOUS_ORIGIN_SIDE`、`INVALID_INPUT`、`ENGINE_ERROR` 等现有分类。[示例客户端](rpc/client_main.cpp)返回零仅表示 RPC 返回 `OK`，仍可能打印 `success:false`；健康响应沿用自己的 `status` 字段，不含分析结果的 `success`。第四阶段为繁忙和应用响应超限增加 `engine-error-code` 尾部元数据，见第14节。RPC 失败返回非零，并在标准错误输出状态信息；服务日志记录方法、状态和耗时，不输出完整请求或结果。日志中的状态是应用处理函数选择的状态，不证明响应最终通过网络送达。

服务 gRPC 接收消息上限为 20 MiB，JSON 解析器仍限制输入为 20,000,000 字节；这两个单位不同，gRPC 消息还包含 Protobuf 封装。服务发送上限为 64 MiB，示例客户端对应设置发送 20 MiB、接收 64 MiB。消息限制用于约束单条消息，不能据此推导整个进程的内存上界。

### 11.4 取消与关闭的边界

第二阶段只在计算前后检查取消与 deadline，CPU 计算当时会继续执行到返回。第三阶段已在计算链加入协作检查：客户端超时、主动取消或断连产生的停止条件，需要由后续检查点读到，才会中断计算并展开清理；JSON 解析和结果序列化仍只在前后检查。名额一直保留到应用处理真实结束，不能因客户端已停止等待而提前归还。机制依据见 [gRPC 取消说明](https://grpc.io/docs/guides/cancellation/)，具体边界见第12节。

[服务启动入口](rpc/server_main.cpp)在启动 gRPC 线程前屏蔽 `SIGINT/SIGTERM`，主线程通过 `sigwait` 接收退出请求；接到后先停止受理，再以 5 秒 RPC 宽限调用关闭并等待退出。第三阶段没有把 `draining` 接入计算探针，因此已经受理的计算保留这段宽限；到期后的 RPC 取消可在后续计算检查点被观察到。库调用、解析、序列化或对象清理尚未结束时，退出仍可能继续等待，5 秒不是进程退出上限，也不能套用 Python 杀死单次引擎子进程的超时契约。第四阶段的可选 Python 接入见第14节，运行验收仍需明确要求。

### 11.5 待授权的验证清单

第二阶段目前只有源码与差异审阅，没有安装依赖、生成代码、编译、运行测试／服务或压测，没有运行兼容性、稳定性、吞吐或延迟结论。第三阶段也未改变这一验收状态。后续仅在用户明确要求后验证：

- 默认构建无需 gRPC；开启选项后，可选服务和客户端能构建、启动、健康检查和退出。
- 连续请求由同一服务进程处理；RPC 与 CLI 的业务字段和错误封套一致，耗时值不要求相同。
- 两个请求争用时只有一个计算，另一个收到繁忙；真实处理结束后名额恢复。
- 取消或超时后，实际计算结束前名额仍占用，结束后恢复；健康检查不争用计算名额。
- 非法 JSON、版本、参数、设施与路线请求，以及超限输入／输出和关闭中请求，分别符合业务层与 RPC 层约定。
- 第一阶段的普通／局部模式、默认值和图不变性一并回归；第10节记录的旧测试枚举失配作为既有问题保留，不混入本轮修复。

## 12. 第三阶段：计算链的协作取消

这一阶段把取消从 RPC 入口传到 C++ 的主要计算循环。原因是常驻服务里的计算可能仍占用唯一名额，而客户端已经停止等待；计算在观察到停止条件后退出，才能减少后续无用工作。实现采用请求私有的探针与检查点，不增加监视线程、计算线程池或新的共享状态。第三阶段当时没有接入 Python、缓存或数据库；第四阶段仅增加可选 Python 接入。

### 12.1 请求持有执行控制，计算函数只借用

[ExecutionControl](include/isochrone/execution_control.hpp)接收 `Probe`，类型为 `std::function<std::optional<StopReason>()>`。探针不依赖 gRPC，可返回 `Cancelled`、`DeadlineExceeded`，或者在继续工作时返回空值。

- `Checkpoint()` 立即调用探针；读到停止原因时抛出携带该原因的 `ExecutionInterrupted`。
- `Poll()` 按工作项累计，每256次检查探针一次。工作项可以是边、路径段、邻接项或栅格单元；它不是时间间隔。
- 默认空探针直接跳过检查与计数。现有单参数、双参数计算接口继续通过空探针兼容；新增入口为 `compute_reachability(graph, request, control)`。

每次 RPC 在自己的栈上创建控制对象，以常量引用借给一次同步计算。探针及轮询计数不由多个并发请求共用；这里的 `const` 接口不表示对象可以任意跨线程共享。

### 12.2 沿一次取消请求阅读代码

1. [RPC 处理函数](rpc/engine_service.cpp)取得计算名额后创建探针，读取当前请求的 deadline 和 `IsCancelled()`。读取顺序先判断 deadline，再判断其它取消；探针不包含服务的 `draining` 状态。
2. 解析前后执行立即检查，再把控制对象传给[受控计算入口](src/engine.cpp)：

   ```cpp
   const ExecutionControl control([context] { return ReadStopReason(*context); });
   EngineResult result = compute_reachability(input.graph, input.request, control);
   ```

3. 核心校验、起点接入、临时建图、最短路、路线回溯、设施与类别覆盖等计算循环，通过 `Poll()` 检查停止条件；阶段边界执行 `Checkpoint()`。栅格、轮廓、组环、包含关系、道路闭环及填洞等活跃几何链路同样接收该控制对象。
4. 检查点抛出的 `ExecutionInterrupted` 沿计算栈返回。内部业务异常处理先识别该类型并继续抛出，避免把取消误包装为 `ENGINE_ERROR`：

   ```cpp
   catch (const ExecutionInterrupted&) {
     throw;
   }
   ```

5. RPC 外层按停止原因返回 `CANCELLED` 或 `DEADLINE_EXCEEDED`，清空回复，不发布部分计算结果。临时容器随栈展开清理，计算名额仍由原 RAII 守卫持有，直到应用处理真实结束后归还；取消通知本身不直接释放名额。

这样把“是否停止”的决定留给请求，把检查位置留给计算函数；核心不需要知道 `ServerContext`，RPC 也不需要介入每个容器的清理。代价是计算循环多了探针检查，且只能在代码主动检查时响应停止；本轮没有测量这项开销或停止延迟。

### 12.3 本轮保留的边界

按本次收窄范围，JSON 解析、解析器内部调用的兼容校验，以及结果序列化没有传入执行控制，只在它们的前后检查。计算入口自身的受控校验循环可以观察停止条件，两者不能混为“全部输入解析都可中断”。

`std::sort`、`std::reverse`、`std::fill` 等标准库调用，以及内存分配、容器清理和析构，本身不会调用该探针；周围的检查点必须等这些操作返回后才能继续。每256个工作项检查也没有限定单个工作项的耗时。因此协作取消不是抢占终止，不承诺“取消后多少毫秒停止”，也不保证服务在5秒内退出。

服务关闭仍先停止受理，给已受理请求保留原有5秒 RPC 宽限。`draining` 不会立刻触发计算取消；宽限到期后，计算根据 gRPC 上下文中的取消状态在后续检查点停止。旧 CLI 和空探针调用保留原来的持续计算行为，JSON 协议与业务错误封套继续沿用。

### 12.4 待授权验证与交付状态

第三阶段目前只交付源码和说明，并做静态审阅；未安装依赖、生成代码、编译、运行测试／服务或压测。S1／S2 的运行兼容性及既有旧测试枚举失配仍待处理；本轮不据此宣称已解决原生崩溃、容量或性能问题。

后续仅在用户明确要求后验证：

- 核心使用合成探针，在校验、寻路及几何链路的不同工作阶段发出两种停止原因，检查受控调用退出及原因传播。
- RPC 将取消映射为 `CANCELLED`、将 deadline 映射为 `DEADLINE_EXCEEDED`，不返回 `ENGINE_ERROR` 或部分结果。
- 同一路网服务取消请求后图内容不变，先前已返回的结果不受影响；临时状态清理及应用响应处理真实结束后，计算名额恢复。
- 默认空探针入口与旧业务结果、错误语义和计时阶段名称兼容；不要求耗时数值完全相同。
- 关闭时已受理请求保留原宽限，宽限到期的取消由检查点观察；解析／序列化与标准库非抢占区间的边界按上述说明验收。

## 13. 结构优化收口：模块、所有权与构建层

S1–S3 分别建立了数据归属、独立 RPC 和协作取消。现在把原来1490行的计算实现拆成职责模块，151行的 [engine.cpp](src/engine.cpp)负责组织阶段。此次为结构迁移，没有改变算法模型，也没有测得运行或性能改善。

### 13.1 公共接口与实现接口

| 位置 | 职责 |
| --- | --- |
| [road_graph.hpp](include/isochrone/road_graph.hpp) | 基础节点、边、道路类型与接入属性 |
| [analysis_request.hpp](include/isochrone/analysis_request.hpp) | 单次请求的参数、设施、类别和局部实验 |
| [analysis_result.hpp](include/isochrone/analysis_result.hpp) | 结果、设施耗时、路线、灰段与展示面；保留原字段及默认值 |
| [engine_errors.hpp](include/isochrone/engine_errors.hpp) | 起点无法接入、侧向歧义两个原有错误类型 |
| [execution_control.hpp](include/isochrone/execution_control.hpp) | 请求私有的取消探针与类型异常 |
| [engine.hpp](include/isochrone/engine.hpp) | 继续包含上述公共类型，导出 EngineInput、校验和三个计算入口 |
| [src/detail/](src/detail/) | 仅供实现之间协作的临时图与 helper 声明，不加入公共 include 路径 |

已有 C++ 调用仍可包含 `isochrone/engine.hpp`。不需要直接包含 `src/detail`；内部临时图并非新的外部协议或可跨请求保存的图缓存。

### 13.2 沿一次计算阅读模块

1. [input_validation.cpp](src/input_validation.cpp)联合校验图和请求，保留原错误顺序；`facilitiesOnly` 组合及路线目标检查继续留在计算入口原位置。
2. [origin_snap.cpp](src/origin_snap.cpp)投影起点、选择道路侧；[path_geometry.cpp](src/path_geometry.cpp)提供折线长度、截取、插值与距离。原容差集中在私有头中，数值未变。
3. [graph_builder.cpp](src/graph_builder.cpp)按起点及设施入口位置临时拆边，构造设施绑定和邻接表。返回拥有数据的 [ComputationGraph](src/detail/computation_graph.hpp)：

   ```cpp
   const detail::ComputationGraph state =
       detail::build_computation_graph(graph, request, snap, control);
   ```

   `graph`、`request` 以 const 引用借用；返回对象拥有临时节点、道路、绑定和邻接表，未复制整个基础图，也不修改基础图。构造结束后将其保持为 const，后续阶段只读访问。
4. [shortest_path.cpp](src/shortest_path.cpp)执行多源 Dijkstra，距离数组返回给入口，按需填写调用私有的前驱数组；[facility_analysis.cpp](src/facility_analysis.cpp)比较入口、恢复路线并追加设施结果。`routeOnly/facilitiesOnly` 继续在此阶段后返回。
5. [reachability.cpp](src/reachability.cpp)计算连续区间、截取可达道路并收集边界点。`ReachabilitySummary` 中的 `TimedDisplayEdge` 指向本次临时道路；入口中的 `state` 先构造、后销毁，展示结束前不移动或修改 `state.edges`，避免借用失效。
6. [display_geometry.cpp](src/display_geometry.cpp)负责计算结果的时间场、近似面、道路围合及填洞，调用原有网格／轮廓／组环模块；[coverage_analysis.cpp](src/coverage_analysis.cpp)负责普通类别覆盖及局部实验的 covered／candidate／unknown 分支。

整个链路继续显式传递同一个 `const ExecutionControl&`。中途抛出取消异常时，helper 的局部对象和入口持有的临时图按栈顺序清理；RPC 的名额守卫仍在计算链外持有，按第12节规则回收。没有通过拆文件引入全局可变图、后台线程或共享执行控制。

### 13.3 CMake 依赖关系与链接迁移

```text
isochrone_geometry ← isochrone_core ← isochrone_json ← isochrone_engine
                                                └─ isochrone_rpc_server（可选）
isochrone_rpc_protocol ← server / client（可选，构建目录生成）
```

箭头表示右侧使用左侧。`isochrone_geometry` 包含通用网格／轮廓／组环／围合等；`isochrone_core` 包含路网和计算，不链接 JSON、gRPC 或 Protobuf；`isochrone_json` 包含现有解析与序列化，通过 PUBLIC 链接计算库。

**使用 `json_io.hpp` 的 CMake 调用方需要把链接目标改成 `isochrone_json`**；只调用图计算仍链接 `isochrone_core`。CLI、RPC 和4个涉及 JSON 的本地测试目标已机械迁移链接，业务断言与旧枚举问题未调整。本地测试继续按忽略规则保留，不新增发布产物。

RPC 构建代码移至 [cmake/EngineRpc.cmake](cmake/EngineRpc.cmake)，仍只在 `BUILD_RPC_SERVER=ON` 时包含；默认关闭、Linux 限制、依赖查找、生成位置及服务／客户端名称保持。第8、11节已有构建与调用命令不变，本轮没有执行这些命令。警告选项按目标统一设置，原 C++20 与 MSVC 编码／异常设置保留。

### 13.4 本次静态证据与限制

对改造前的工作区快照做源码文本核对：26个迁移 helper 的函数体 token 序列相同；设施循环、可达边循环、普通／局部覆盖分支相同；建图部分只做局部容器改为 `ComputationGraph` 成员等已说明的所有权替换。结果字段、设施绑定字段、异常文本、8个计时阶段名称与顺序保留；89处 `Poll()` 和32处 `Checkpoint()` 均迁移到对应计算链。数量相同本身不能证明取消行为，须结合各代码块的源码对应关系阅读。

对私有声明／定义、调用参数、头文件相对路径、CMake 源文件表与依赖方向完成静态审阅；差异空白检查无错误。阶段名称与业务划分保留，但临时建图辅助容器现在在 builder 返回时清理，计入建图阶段；函数拆分也可能改变代码生成，因此不能要求改造前后计时数值相同。

**本轮没有安装依赖、配置／生成构建、编译、运行测试、启动服务或压测。** 静态证据不证明链接成功、运行结果兼容、取消时延、稳定性或性能。运行验收继续使用第10–12节清单，并补查分层库的独立链接与 JSON 链接迁移。线程池、图缓存、Python RPC 接入和数据库仍属于后续功能专题；旧测试枚举失配与既有原生崩溃／偶发 INVALID_INPUT 没有在此次结构迁移中修复。

## 14. 第四阶段：Python 可选接入 C++ RPC

第四阶段将既有单任务 RPC 入口接到 Python 的统一引擎适配器，当时客户端计算许可固定为1。默认仍使用子进程；MySQL／Redis 保留为后续构想，没有加入线程池、图缓存或服务器部署。第10–13节的阶段记录保留，新增接入并不表示此前运行验收已经通过。第五阶段对服务端和客户端许可一起扩展为可配置容量，当前设置见第15节。

### 14.1 传输选择和默认行为

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `CPP_ENGINE_TRANSPORT` | `subprocess` | 只接受 `subprocess` 或 `grpc`，启动时选择；修改后需重启 Python |
| `CPP_ENGINE_RPC_PORT` | `50051` | 整数1–65535，RPC 固定连接 `127.0.0.1:<port>` |

子进程模式继续使用现有 C++ 路径和2个业务许可；不导入 gRPC，也不要求存在 Python 生成模块。第四阶段的 RPC 模式使用1个客户端业务许可，第五阶段改为 `CPP_ENGINE_RPC_CAPACITY` 配置，默认2；健康检查仍独立合并并缓存5秒。Python 调度器仍是2个执行协程和8个准备／排队预约，已受理任务等待引擎许可最多5秒，直接调用忙时快速失败；没有增加 RPC 应用等待队列。

所有现有 `run_engine()`／`check_engine()` 调用都经同一 `EngineRuntime` 选择传输：普通／合成分析、设施补充和覆盖、局部实验、按需 C++ 路线、编辑器引擎检查及健康探针。在线采样的百度计算和百度路线仍使用其原有路径。

RPC 模式每次发送完整 v2 图，使用本机明文连接。Python 和 C++ 必须在同一 Linux／WSL2 网络环境运行；本阶段不支持跨主机或独立容器间的 RPC 地址配置。服务暂未启动时，Python 可以启动，健康检查显示不可用，计算返回 `ENGINE_UNAVAILABLE`；缺少可选依赖／生成代码则在创建运行时时明确报配置错误。不自动安装、不自动启动服务、不回退子进程。

### 14.2 显式安装、生成与启动说明

**以下均为后续明确授权后使用的命令说明，本阶段没有执行。** 在 Linux／WSL2 仓库根目录，使用 Python 3.12 环境安装基础及可选依赖：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt -r backend/requirements-rpc.txt
python backend/scripts/generate_engine_rpc.py
```

[可选依赖](../backend/requirements-rpc.txt)固定为 `grpcio==1.84.0`、`grpcio-tools==1.84.0`、`protobuf==7.36.2`；默认基础依赖和 Docker 构建没有修改。[生成脚本](../backend/scripts/generate_engine_rpc.py)先核对这些版本，再从现有 `rpc/engine_service.proto` 生成到 `backend/app/engine_rpc_generated/`。用逻辑 Proto 路径映射生成正确的 `app.engine_rpc_generated` 导入，不改写生成文件。生成的 pb2／grpc 模块被 Git 忽略，包的 `__init__.py` 和脚本属于源码。[官方路径映射说明](https://grpc.io/docs/languages/python/basics/#generating-grpc-interfaces-with-custom-package-path)

按第11.1节准备已安装的 C++ gRPC／Protobuf 并构建服务；在一个终端启动：

```bash
./cpp-engine/build/rpc/isochrone_rpc_server --port 50051
```

另一个同环境终端，从仓库根目录激活同一 Python 环境并启动后端：

```bash
CPP_ENGINE_TRANSPORT=grpc CPP_ENGINE_RPC_PORT=50051 PYTHONPATH=backend \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端沿用已有启动方式。`/api/v1/health` 的外层结构不变，健康细节来自 RPC Health；RPC 模式不检查本机 CLI 文件。回到默认方式时设置 `CPP_ENGINE_TRANSPORT=subprocess` 并重启 Python。Windows 本地启动器和默认 Docker 镜像继续按原子进程方式使用；仅修改环境变量不会为镜像自动补齐 RPC 依赖或生成模块。

### 14.3 沿一次业务请求阅读

1. [统一运行时](../backend/app/engine.py)先取得本模式的许可，调用[传输接口](../backend/app/engine_transport.py)。默认传输委托原 `_run_engine/_check_engine`，保留进程启动、退出码及真实回收逻辑；RPC 传输仅在选择该模式时创建。
2. [公共编解码](../backend/app/engine_protocol.py)保留原 UTF-8、紧凑 JSON、米制精度和道路字段精简；两种传输共同校验封套、业务错误和结果对象。子进程仍优先保留有效业务错误，再处理无法解释的非零退出。
3. [GrpcTransport](../backend/app/engine_rpc.py)持有一个 `grpc.aio` 通道和 Stub，全部业务与 Health 复用。生成模块自己执行版本检查，加载失败作为启动配置错误；默认路径不依赖它们。
4. Compute 发起调用时设置25秒 deadline、Health 为3秒，均使用 `wait_for_ready=False`，禁用通道自动重试。JSON 编码发生在计算 deadline 之前，JSON 解码发生在 RPC 返回之后；它们不属于 RPC 等待期限。
5. JSON 成功返回后仍检查 `success`。RPC `OK` 不代表业务成功；健康响应使用自己的 `status/engine/schemaVersion`，单独校验后转换为既有 `EngineHealth`。

Python 通道发送20 MiB、接收64 MiB；计算 JSON 在 RPC 发出前检查20,000,000字节上限，超出返回 `INVALID_INPUT`。C++ 继续检查自身 JSON／Protobuf 限制。业务数据仍通过 bytes 字段承载，没有更换 Proto 业务模型。

### 14.4 错误、取消和关闭

| 观测 | Python 处理 |
| --- | --- |
| RPC OK，合法 `success:false` | 保留 v2 业务码和消息 |
| `RESOURCE_EXHAUSTED` + `engine-error-code=ENGINE_BUSY` | 现有 `EngineBusy`，沿用调用方的繁忙处理 |
| `RESOURCE_EXHAUSTED` + `ENGINE_RESPONSE_TOO_LARGE` | `ENGINE_RESPONSE_TOO_LARGE` |
| 其它 `RESOURCE_EXHAUSTED` | `ENGINE_RESOURCE_EXHAUSTED`，不猜测为繁忙 |
| `DEADLINE_EXCEEDED`／`UNAVAILABLE`／远端 `CANCELLED` | `ENGINE_TIMEOUT`／`ENGINE_UNAVAILABLE`／`ENGINE_CANCELLED` |
| 无效 JSON、封套／结果／健康对象，或 RPC 方法／参数／数据协议错误 | `ENGINE_PROTOCOL_ERROR`；健康调用转为错误状态 |
| 其它 RPC 故障 | `ENGINE_COMMUNICATION_ERROR` |
| 调用方 `asyncio.CancelledError` | 取消 RPC，完成本地清理后继续传播取消 |

C++ 只增加了繁忙和应用响应超限的尾部元数据；框架拒绝超限消息时可能没有该标记，Python 使用一般资源限制分类。外层 HTTP／任务状态模型及各业务接口原有错误包装保持；没有增加自动重试或传输降级。

调用取消时等待本地 Call 的终态；[公共清理辅助](../backend/app/engine_cleanup.py)在重复取消中仍等待同一清理任务。这不代表服务器 CPU 已退出。Python 归还客户端许可后，C++ 的 RAII 名额仍由独立服务管理，直到解析／计算／序列化及清理真实结束；这期间下一请求仍可能收到繁忙。Health 成功也不表示计算名额空闲。

应用保持共用5秒关闭宽限。结束后取消客户端调用和探针，等本地清理完成再关闭通道；不会停止独立 C++ 服务，也不会延长出另一个 RPC 关闭宽限。应用初始化进入调度器／Agent 启动阶段后发生异常，也通过同一 finally 清理运行时。C++ 仍按第11、12节独立接收信号并协作停止。解析／输出与标准库非抢占边界保留，不承诺强制计算超时。[gRPC 异步通道说明](https://grpc.github.io/grpc/python/grpc_asyncio.html)

RPC 日志仅记录 `transport/method/status/duration_ms`，状态表示 RPC／客户端处理状态，不输出完整输入、结果或上游错误详情。成功业务诊断包含 `transport=grpc`、编码／往返／解码／总耗时、字节数和许可等待；没有 `spawnMs`。往返包含连接、框架处理和服务执行，不是纯网络延迟；许可等待单独记录，不计入 invocation.totalMs。默认子进程保留原诊断字段。

### 14.5 静态交付及待授权验收

本轮完成了源码、调用点、取消／关闭所有权和差异审阅。文本核对显示默认路径的定位、启动、通信、终止回收、健康调用 helper 保留；公共编码／校验仅作函数提取与返回值改写；基础 requirements、Dockerfile 和 Proto 未变。未安装依赖、生成协议代码、编译、运行测试／服务或压测，尚无实际通道复用、结果兼容或取消回收验收结论。

后续明确要求后覆盖：默认模式无 gRPC 依赖；RPC 业务与 Health 共用通道且无需本地 CLI；业务结果与默认路径兼容；非法响应、超限和缺失／错误元数据分类；服务未启动、中断、deadline、主动／重复取消均无自动重跑或降级；C++ 名额延后恢复及 Health 独立；Python 关闭或初始化失败释放本地资源，独立 C++ 服务仍遵循自身生命周期。S1–S3 的运行验收、旧测试枚举失配及既有原生崩溃／偶发 INVALID_INPUT 继续单列。

## 15. 第五阶段：C++ 有界并发执行层

本阶段把单计算槽扩展为可配置计算容量，并同步衔接 Python 客户端许可。这样现有业务可以在明确的并发预算内执行多个请求；满额请求继续快速得到繁忙反馈。实现继续使用同步 gRPC handler，不增加应用等待队列、自建线程池、共享图缓存、SQL 或服务器部署。

### 15.1 两侧容量与启动方式

| 配置 | 默认值 | 作用及范围 |
| --- | --- | --- |
| C++ `--compute-capacity N` | `2` | 服务实例允许同时持有的应用计算名额，正整数 `1..INT_MAX`；当前 Linux 目标为 `1..2147483647` |
| Python `CPP_ENGINE_RPC_CAPACITY` | `2` | 一个事件循环的 RPC 客户端业务许可，正整数 `1..2147483647` |

容量在各自启动时固定，不做在线调节或自动协商。两侧应配置相同值；Python 小于 C++ 时只能使用部分服务容量，大于 C++ 时可能收到服务端繁忙，实际应用计算上限始终由 C++ 服务控制。不同客户端各自的本地许可不能相加后当作服务端预算。

**以下命令仅供后续明确授权后使用，本轮没有执行。** 在完成第11、14节依赖与构建准备后，同一 Linux／WSL2 环境的一个终端启动 C++：

```bash
./cpp-engine/build/rpc/isochrone_rpc_server --port 50051 --compute-capacity 2
```

另一个终端从仓库根目录启动 Python：

```bash
CPP_ENGINE_TRANSPORT=grpc CPP_ENGINE_RPC_PORT=50051 CPP_ENGINE_RPC_CAPACITY=2 \
  PYTHONPATH=backend python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`--port` 与 `--compute-capacity` 可按任意顺序提供，各只能出现一次；缺值、非法值或重复选项拒绝启动。两侧都设置为1即可保留单槽执行方式。默认 `CPP_ENGINE_TRANSPORT=subprocess` 及其2个业务许可不变；RPC 端口、回环明文连接、deadline、消息上限、JSON／Proto 封套和错误分类不变。默认2只是实现起始值，没有实际容量或最佳性能依据。

### 15.2 计算门与请求所有权

[计算门](rpc/compute_gate.hpp)以固定容量和原子活动计数管理名额。`TryAcquire` 通过比较交换争取一个可用名额，不等待其它计算完成；满额返回失败，由服务沿用 `RESOURCE_EXHAUSTED` 和 `engine-error-code=ENGINE_BUSY`。业务 JSON 解析之前争取名额，拒绝请求不进入计算流程，也不加入新的应用队列。

[服务处理函数](rpc/engine_service.cpp)中的 RAII 守卫在外层 try 之前构造，成功取得名额后持有门。它覆盖解析、校验、计算、序列化、应用回复构造以及异常栈展开，真实处理结束后才减少活动计数。成功、业务错误、取消和内部异常都沿同一所有权路径回收；不因客户端已停止等待而提前归还名额。gRPC 在 handler 返回后的发送资源仍不由该计数覆盖。

每个 handler 独立拥有完整的解析输入、本次 `ExecutionControl`、临时拆边图、距离／前驱、几何中间数据和结果。控制对象仍只借给该次同步计算；不跨请求共用轮询计数。当前每次 RPC 仍发送整张图，未引入图 ID 或跨请求图缓存。并发会使更多输入和中间数据同时存活，CPU／内存和耗时变化需要实测，计算容量不是整个进程的内存上限。

### 15.3 同步 handler、健康检查与关闭

同步 gRPC 已提供接待和 handler 执行线程，本阶段直接限制进入计算链的并发量。保持计算在 handler 生命周期内完成，使 `ServerContext`、请求、回复和取消探针的借用关系仍与第四阶段一致；没有把任务投递到 handler 返回后仍继续运行的后台执行器，也不新增 future、任务队列或线程 join 所有者。

原有 `NUM_CQS=1`、`MIN_POLLERS=2`、`MAX_POLLERS=2` 保留。这些参数描述框架轮询行为，不能当作 handler 总线程数或业务计算容量；也不能据此推出连接数、传输积压或总内存上限。Health 不获取计算门，继续使用独立处理路径；Python 健康探针仍合并并缓存5秒，不占客户端计算许可。

Python 超时／取消后会归还已完成本地清理的客户端许可；C++ 若尚未观察到取消或尚在清理，仍持有其名额，因此下一请求可能继续收到繁忙。C++ 每个请求仍通过自己的检查点协作停止，解析／输出和标准库非抢占边界不变，不增加强制计算终止能力。

服务关闭仍先进入 drain，拒绝新请求，给予原有5秒 RPC 宽限并调用 `Wait()` 等待 handler 实际退出。服务对象及其计算门必须存活至所有守卫归还名额之后；当前入口保持服务先构造、server 后构造，关闭／等待完成后才退出作用域。没有自建线程池需要额外停止，也不承诺进程5秒内退出。Python 只关闭自己的调用与通道，不接管独立 C++ 进程。

### 15.4 本轮交付与待授权验收

本阶段交付计算容量门、启动参数、Python 客户端许可衔接和说明，按源码、调用点及差异审阅。没有安装依赖、生成协议、编译、运行测试／服务或压测；S1–S4 的运行兼容、通道复用、取消回收和既有稳定性问题仍未验收。源码配置值和原子计数不能代替实际并发行为或吞吐结论。

后续只在用户明确要求后验证：

- 默认容量2、显式容量1与其它正值生效；两种 C++ 参数顺序均可用，缺值、重复、零、负数及溢出拒绝，Python 配置范围一致。
- 服务容量为 N 时，同时持有的应用计算名额不超过 N，额外请求快速繁忙；拒绝发生在业务解析前，名额释放后新请求可以继续执行。
- Python 许可与配置衔接，低于／高于服务容量时表现符合上述边界；默认子进程仍为2个业务许可。
- 并行请求使用不同起点、设施和模式，结果与各自串行结果兼容；输入、旧结果及各请求控制状态不互相修改，计时数值不要求相同。
- 非法输入、业务错误、deadline、主动取消和内部错误后名额只在实际处理清理完成时恢复；计算繁忙时健康路径保持独立。
- 多个在途请求关闭时先停止受理，原宽限和协作取消生效，`Wait()` 完成后再销毁服务和计算门；不出现任务继续借用已销毁上下文的情况。

实际并发容量、QPS、尾延迟、CPU／内存及复杂路段表现另以授权后的测量为准，压测工具和原始记录继续放在项目外临时目录。
