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

引擎通过标准输入接收一个 JSON 对象，标准输出返回一个 JSON 对象；日志只写标准错误。Python 单次引擎子进程调用上限为 25 秒，这不等于包含 POI 补充的完整任务保证在 25 秒内结束。

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

核心实现见 [engine.cpp](src/engine.cpp)；类型见 [engine.hpp](include/isochrone/engine.hpp)。优先队列 Dijkstra 的常规复杂度为 `O((V + E) log V)`，其中包含本轮临时拆边节点；整次计算还包括几何生成，不能只用寻路复杂度估计总耗时。

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
