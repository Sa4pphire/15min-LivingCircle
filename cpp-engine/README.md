# C++ 15 分钟步行引擎

该目录只做路网计算，不调用百度 API、不读取凭据。当前演示统一读取已建模的 [synthetic-preview.json](../data/networks/synthetic-preview.json)，不另补道路、设施或裁剪出口。Python 按文件声明将 WGS-84／BD-09 坐标换成局部米制坐标，通过标准输入发送 v2 JSON；C++ 也支持直接读取同一合成文件。标准输出返回计算 JSON，日志只写标准错误。完整字段见 [输入样例](../contracts/engine-input.example.json)、[输出样例](../contracts/engine-output.example.json) 和 [v2 契约](../contracts/engine-v2.README.md)。

## 构建

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=OFF
cmake --build build
```

测试目录不随仓库发布，本地已有测试仍可保留。仅当本机有 `tests/CMakeLists.txt` 时，可用 `-DBUILD_TESTING=ON` 另行编译和运行 CTest；缺少测试目录不会阻止引擎构建。

从仓库根目录可直接读取当前建模后的合成路网：

```bash
./cpp-engine/build/isochrone_engine --demo
# 等价的显式路径，默认起点为局部原点 (0,0)，阈值 900 秒：
./cpp-engine/build/isochrone_engine --network data/networks/synthetic-preview.json
# 同一文件的严格 180 秒实验，选择文件中已有共享通道的中点：
./cpp-engine/build/isochrone_engine --demo --local --origin-meters -54.25 -203.25 --origin-edge w:154811345:2:0
```

Windows 下可将可执行文件名改为 `isochrone_engine.exe`。`--demo` 优先读取 `SYNTHETIC_NETWORK_PATH`；未设置时，CMake 写入构建时的仓库数据路径，不依赖启动目录。复制到其他机器后请设置该变量或用 `--network` 指定实际位置。手工 g++ 构建未定义路径时，`--demo` 相对于当前目录寻找 `data/networks/synthetic-preview.json`。上述数据不代表经核实的真实街区。

`--origin-meters X Y` 只接受引擎局部米数，不接收经纬度。15 分钟合成演示保留未核实直线接入（上限 1,170 米且计时）；`--local` 严格限制到步行边 5 米以内，不允许路外直线接入。直接文件演示只接受 `synthetic:true`。路径错误返回 `INPUT_FILE_ERROR`，不会换用其他样例。

Python 子进程仍不带命令行参数，以标准输入发送完整请求；这条契约不变。已保存的完整请求可用 `--input request.json` 读取，走同一解析与计算链。`contracts/` 内的小图继续作为字段说明及算法单元测试，不是演示数据源。

现有合成文件没有设施、评价类别或已核实裁剪出口。局部模式只返回可达街段，`localGrayZones` 为空，并明确警告 `LOCAL_FACILITY_DATA_NOT_PROVIDED`、`LOCAL_BOUNDARY_NOT_MARKED`；Python 标记 `grayZoneStatus: "data_insufficient"`。不把缺失设施当作匮乏，也不根据节点度数猜测裁剪出口。

## 路网如何表示

坐标是相对于固定原点的局部米制偏移量，X 向东、Y 向北。节点 ID 决定拓扑；两条线即使几何相交，只要没有共享节点或显式连接边，就不能互通。所有边默认可双向行走。

| 边 `kind` | 标注方式 | 耗时 |
| --- | --- | --- |
| `sidewalk` | 普通道路左右两侧各一条；必须有 `streetBlockId` 和 `side` | 折线长度 ÷ 步速 |
| `shared_way` | 经核实可自由穿行的步行街／共享小巷只标一条中心线；必须有类型和宽度 | 沿线及横向接入距离 ÷ 步速 |
| `turn` | 同侧转向、进出共享通道等显式路口连接 | 折线长度 ÷ 步速 |
| `crossing` | 合法过街点；不能用 `turn` 代替 | 折线长度 ÷ 步速 + 20 秒等待 |

普通道路同一街段的左右侧不能共用节点，也不能通过 `turn` 直接连通。`shared_way` 允许两侧在任意位置接入，但只对人工确认可自由穿行的路段使用；不会按道路名称或宽度自动推断。

## 一次计算的流程

1. `json_parser.cpp` 解析输入，`engine.cpp` 校验节点 ID、边端点、路型和设施接入点。无效输入返回结构化错误，不输出半成品结果。
2. 起点投影到人行道或共享通道，并在投影位置拆边。普通道路两侧同样接近时必须给 `originEdgeId`；共享通道只能吸附在估计路面半宽外 3 米内。起点到投影线的横向距离也计入步行时间。
3. 起点和所有设施入口都在投影位置拆边，统一进入图。入口可有经核实的离街接入折线；从起点运行 Dijkstra，设施在多个入口中取最短耗时。连通但超过 900 秒仍返回实际耗时；不连通才返回 `null`。
4. 按 900 秒截取人行道、共享通道及转向边；过街边只有完整走完时才显示可达。边界可能落在边内部、节点或过街终点，输出在 `frontierMeters`。
5. 从已到达的街段向周边街区生成时间场：每个网格位置按路网到达时间加横向步行估算时间；常规外扩最多 80 米（`displayAreaRadiusMeters`），在 900 秒边界自动收窄。若一个未标路网的网格位置被不同方向的可达街道夹住，且从这些道路到该点仍在时间预算内，可额外填补街区内部；搜索距离最多为常规半径的 3 倍。`closed_road_faces` 另按**显式节点连接**检查完全可达的道路是否闭合：只有围合尺度不超过 480 米且内部网格仍符合时间预算，才补进展示面；坐标碰巧相交或接近不算连接，未走完整圈也不算闭合。Marching Squares 提取近似等时圈后，将面积小于 `displayMinHoleAreaSquareMeters` 的已有内洞直接填平（默认 2500 平方米，设为 `0` 可关闭）；它不能修复未形成内洞的视觉缺口。大型未知区域保留，过街／转向连接只用较窄的 `displayBufferMeters` 展示。这是画图近似，不是固定圆或街段外框；由于没有建筑、围墙、水体等阻隔数据，**设施覆盖统计必须依据路网耗时**。
6. 对每个在线核查完成的设施类别，以其所有入口为多源运行 Dijkstra，将类别服务街段从起点可达街段中精确扣除；输出剩余灰色街段及近似面。数据未核齐的类别输出“数据不足”，不推断设施匮乏。

## 3 分钟局部路网实验（独立模式）

小片实测路网不能支撑完整的 15 分钟灰区结论：图外可能有道路捷径或设施。本模式只用于展示精细建图后的局部计算，不进入百度 API 主报告。输入仍是 v2，在顶层加 `localExperiment`：

```json
"localExperiment": {
  "boundaryNodeIds": ["cut-north", "cut-east"],
  "topologyStatus": "verified"
}
```

`boundaryNodeIds` 必须列出**所有图被裁剪的出口**；真实尽头即使是度数为 1 的节点也不列入。`topologyStatus: "verified"` 表示内部路网连接和这份裁剪出口清单都已核查，否则填 `incomplete`。每个 `serviceCategories[]` 另填 `localInventoryStatus: "verified" | "incomplete"`，独立于线上 POI 的 `dataStatus`；未填写按 `incomplete` 处理。局部 JSON 省略 `thresholdSeconds` 时默认 180 秒，Python 独立入口固定使用 180 秒。

设起点在图内可达的街段为 R，已录设施入口在阈值内可服务的街段为 S，从任一裁剪边界节点在阈值内可走到的街段为 B。引擎按边内连续区间精确划分：已覆盖为 R∩S；两项核查均完成时，候选未覆盖为 R∖(S∪B)，未知为 (R∖S)∩B；核查未完成时，R∖S 全部为未知。通行耗时仍包含显式过街等待及设施入口接入路径。起点在阈值内到达任何裁剪边界时，`diagnostics.warnings` 包含 `LOCAL_REACHABILITY_MAY_BE_TRUNCATED`。

有设施类别时，结果在 `localGrayZones` 返回三种精确彩色街段 `coveredEdges`、`candidateUncoveredEdges`、`unknownEdges` 及各自长度和警告；`grayZones` 为空，不生成局部灰区缓冲面、完整 15 分钟灰区比例或可外推到街道的结论。起点可达街段本身也可能因图外捷径而不完整。[局部合成输入](../contracts/engine-local-experiment.input.example.json) 仅供字段说明和单元测试；运行演示默认使用上面的现有建模文件。

## Python 应如何消费输出

先检查 `success`；为 `false` 时读取 `error.code`，不要解析 `result`。成功时：

- `reachableEdges`：实际路网可达的折线，适合单独画线；共享通道边另有 `widthMeters`。
- `facilityTravelTimes`：逐设施 `reachable` 与 `travelTimeSeconds`，用于覆盖统计。
- `grayZones`：逐类别精确未覆盖街段、长度比例及近似展示面；`status: "candidate"` 表示在线核查数据下的疑似缺口，`data_insufficient` 表示不应画灰区。
- `displayGeometryMeters`：便于处理的 Polygon/MultiPolygon 风格对象，当前固定为 `{"type":"MultiPolygon","coordinates":[[[[x,y],...],...],...]}`；每个 Polygon 的第一个环是外环，后续是洞。旧字段 `displayPolygonMeters` 是同一坐标数组，为兼容现有调用方暂时保留。
- `frontierMeters`：边界点；`diagnostics`：节点数、完整可达的过街边数、闭合道路面数、闭合道路补充的网格数和警告。闭合面数大于零但补充网格数为零，表示原时间场已覆盖这些闭合面，或面内无剩余时间可用于横向步行。

注意 `displayGeometryMeters` **不是 RFC 7946 GeoJSON**：坐标仍是局部米数。Python 必须将每个点转回文件声明的 WGS-84 或 BD-09 经纬度，再输出最终 `MultiPolygon` 风格几何；百度地图图层还必须使用 BD-09。不要把米制数组直接交给百度地图，也不要用近似面判定设施是否可达。

共享通道用中心线近似路面内部路径，同侧斜向步行可能被高估；原 IDW 模块保留供历史实验，不参与 v2 主流程。真实路网尚未提供，标注要求见 [数据说明](../data/networks/README.md)。
