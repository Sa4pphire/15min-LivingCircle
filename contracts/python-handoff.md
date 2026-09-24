# Python ↔ C++ 对接清单

本页供负责 FastAPI／数据处理的协作者使用。当前只有**合成路网**可联调；`data/networks/shanghai-new-jiangwan.json` 尚未提供，不能把合成结果当作真实生活圈报告。

## 现成入口

- 输入：[engine-input.example.json](engine-input.example.json) 是完整混合路网样例；[engine-input.minimal.example.json](engine-input.minimal.example.json) 与 [engine-output.example.json](engine-output.example.json) 是可复现的最小往返样例。
- Python 已有 `backend/app/network.py`：读取标注文件、转换 BD-09 ↔ 局部米制、构造引擎输入并把结果转换为最终地图数据。
- Python 已有 `backend/app/engine.py`：以标准输入调用 C++ 可执行文件，25 秒超时，解析 v2 输出。调用顺序为 `load_engine_request(center) → run_engine(payload) → build_analysis_result(result, metadata)`。

## 对接约定

1. 路网节点、边和设施只向 C++ 传 `schemaVersion: 2` 的规范化 JSON；不要传百度原始响应。`originBd09`、`supportedCenterBoundsMeters`、`synthetic` 是 Python 的网络文件元数据，引擎忽略它们。
2. 普通道路两侧分别用 `sidewalk` 标注；经人工核实可自由穿行的步行街／小巷用一条 `shared_way`，填 `sharedWayType` 和 `widthMeters`。路口连接显式标注；普通道路过街只能用 `crossing`。同一普通道路两侧不要共用节点。
3. 新设施数据使用 `id`、`category`、`entrances`；每个入口有独立 ID、`accessEdgeId`、`streetAccessPointMeters`，若入口离街边还要提供经核实的 `accessPathMeters`。旧单入口字段只用于兼容既有样例。过街边可单独传 `waitSeconds`。统计 15 分钟覆盖只看 `facilityTravelTimes[*].reachable`／`travelTimeSeconds`，并使用 `bestEntranceId` 标示最短入口。
4. 画线使用 `reachableEdges`。画面优先使用 `displayGeometryMeters` 的 `MultiPolygon.coordinates`，逐点从局部米制转换为 BD-09，再构造最终 GeoJSON；外环和洞环都要转换。`displayPolygonMeters` 是同一数组的兼容字段。展示面不得用于设施可达性判断。
5. 真实路网 JSON 必须给出 `publicWalkableAreasMeters`，使 Python 拒绝封闭地块内部选点；演示区内公共步行空间可任意选点。遇到道路侧不明确，API 请求可提供 `originEdgeId`。`serviceCategories` 逐类声明 `reviewed_online` 或 `incomplete`；后一状态不得声称灰区。C++ 的 `grayZones[*].uncoveredEdges` 是精确街段，`displayGeometryMeters` 仅近似画面。
5. 请求可能返回 `INVALID_INPUT`、`ORIGIN_NOT_ON_WALKWAY`、`AMBIGUOUS_ORIGIN_SIDE` 或 `ENGINE_ERROR`；文件缺失／超出已标注区域由 Python 返回 `UNSUPPORTED_AREA`。不要在道路侧不明确时自动选择另一侧。

## 建议的协作顺序

先用最小输入／输出样例固定解析和字段命名，再用完整合成图检查普通道路过街与共享通道。随后人工标注一小块真实区域，双方一起核对 `streetBlockId`、节点连接、设施接入边及 900 秒边界；数据核实后才把 `synthetic` 设为 `false`。每次修改 JSON 契约，同时更新样例和 `backend/tests/test_network_contract.py`、`cpp-engine/tests/test_engine.cpp`。

可运行 `pytest backend/tests` 做 Python↔C++ 往返测试（需先构建引擎）。CI 会分别构建 Debug、Release C++ 并运行测试。真实路网到位后，最后确认引擎计算加进程启动不超过 Python 现有的 25 秒超时。
