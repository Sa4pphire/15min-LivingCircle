# C++ 15 分钟步行引擎

该目录只做路网计算，不调用百度 API、不读取凭据。Python 负责读取人工标注的路网、把 BD-09 坐标换成局部米制坐标，并通过标准输入发送 v2 JSON；C++ 向标准输出返回一个 JSON 对象，日志和调试信息只写标准错误。完整字段见 [输入样例](../contracts/engine-input.example.json)、[输出样例](../contracts/engine-output.example.json) 和 [v2 契约](../contracts/engine-v2.README.md)。

## 构建

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
ctest --test-dir build --output-on-failure
```

从仓库根目录可用合成数据手工联调：

```bash
./cpp-engine/build/isochrone_engine < contracts/engine-input.example.json
```

Windows 下可将可执行文件名改为 `isochrone_engine.exe`。上述样例明确是合成路网，不代表真实街区。

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
5. 对可达边做展示缓冲，在 10 米网格上使用 Marching Squares 和环拼接生成近似面。输出保留多面及内洞；它只用于画图，**设施覆盖统计必须依据路网耗时**。
6. 对每个在线核查完成的设施类别，以其所有入口为多源运行 Dijkstra，将类别服务街段从起点可达街段中精确扣除；输出剩余灰色街段及近似面。数据未核齐的类别输出“数据不足”，不推断设施匮乏。

## Python 应如何消费输出

先检查 `success`；为 `false` 时读取 `error.code`，不要解析 `result`。成功时：

- `reachableEdges`：实际路网可达的折线，适合单独画线；共享通道边另有 `widthMeters`。
- `facilityTravelTimes`：逐设施 `reachable` 与 `travelTimeSeconds`，用于覆盖统计。
- `grayZones`：逐类别精确未覆盖街段、长度比例及近似展示面；`status: "candidate"` 表示在线核查数据下的疑似缺口，`data_insufficient` 表示不应画灰区。
- `displayGeometryMeters`：便于处理的 Polygon/MultiPolygon 风格对象，当前固定为 `{"type":"MultiPolygon","coordinates":[[[[x,y],...],...],...]}`；每个 Polygon 的第一个环是外环，后续是洞。旧字段 `displayPolygonMeters` 是同一坐标数组，为兼容现有调用方暂时保留。
- `frontierMeters`：边界点；`diagnostics`：节点数、完整可达的过街边数和警告。

注意 `displayGeometryMeters` **不是 RFC 7946 GeoJSON**：坐标仍是局部米数。Python 必须将每个点转为地图使用的 BD-09 经纬度，再输出最终 GeoJSON `MultiPolygon`。不要把米制数组直接交给百度地图，也不要用近似面判定设施是否可达。

共享通道用中心线近似路面内部路径，同侧斜向步行可能被高估；原 IDW 模块保留供历史实验，不参与 v2 主流程。真实路网尚未提供，标注要求见 [数据说明](../data/networks/README.md)。
