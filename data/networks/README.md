# 实地步行路网

这里放经人工核对的演示区域路网 JSON。默认预期文件为 `shanghai-new-jiangwan.json`；该文件目前不存在，因此真实区域分析会返回 `UNSUPPORTED_AREA`。

## 临时合成路网：只用于三端联调

`synthetic-preview.json` 由 `python backend/scripts/export_synthetic_preview.py` 从前端的
`demoRoadGraph.local.json` 生成。它有 10,432 个节点、10,902 条边：主干道中心线偏移成左右两条
`sidewalk`；其余 `roadLocal`／`roadPath` 暂按 `shared_way` 处理。主干道交点处的共享道路
分支使用独立端点，不直接从道路一侧免费通到另一侧；两侧都有分支的 41 处位置加了**未经核实**的
`crossing`，使用默认 20 秒等待。图的推断连接也未经核实；文件中的 `synthetic: true` 不得移除。

此图沿用 OSM SVG 预览的 WGS-84 来源和局部米制几何，但将预览的“Y 向南”转为引擎的“Y 向北”。
它使用 `originWgs84`，API 请求中心点必须带 `coordType: "wgs84ll"`；不要把它伪装成 BD-09
路网。前端“合成算法”模式调用独立的 `/api/v1/synthetic-analyses`，由 C++ 返回可达街段和
MultiPolygon 近似等时圈，再转回与“真实区域”模式共用的 SVG 底图坐标显示。设施和类别为空，
因此不生成设施覆盖或灰区报告。

从仓库根目录启动联调时，先构建当前 C++ 源码，设置
`CPP_ENGINE_PATH=cpp-engine/build/isochrone_engine`（Windows 为相应 `.exe`；本机也可指向独立编译的 `cpp-engine/build/isochrone_engine_synthetic.exe`），启动
`uvicorn app.main:app --app-dir backend --port 8000`，然后运行前端 `npm run dev`。
合成接口固定加载本文件，与默认真实分析接口的 `WALKING_NETWORK_PATH` 互不影响；
“真实区域”模式仍使用浏览器内固定圆示意，无需后端。
由于默认区域中心距可用街边较远，C++ 可能返回 `ORIGIN_NOT_ON_WALKWAY`；请选择靠近道路的点。
整个文件仅证明数据链路可用，**不能用于真实步行可达、过街或设施覆盖判定**。

合成模式向引擎发送 `allowOffNetworkOrigin: true`，将起点到最近可步行边的直线距离按步速计时并从 900 秒内扣除；最大搜索距离为 1,170 米。共享通道原有的 3 米吸附限制只在严格模式保留。接入虚线未核实，可能穿越不可通行地块；真实路网仍严格限制 5 米且要求起点落在已核实公共步行空间内。展示面由扣除接入时间后的可达街段生成 MultiPolygon，不是固定圆。

文件采用 `contracts/engine-input.example.json` 的节点和边格式，并额外包含 `originBd09`、`supportedCenterBoundsMeters`、`synthetic:false`。`originBd09` 是整个路网局部米制坐标的原点，`supportedCenterBoundsMeters` 是允许发起分析的中心点范围。路网还必须至少覆盖从该范围出发步行 15 分钟可能达到的全部道路，否则边界会被数据边缘截断。

真实数据还必须提供 `publicWalkableAreasMeters`：若干闭合的局部米制多边形环 `[[[x,y],...], ...]`，仅覆盖已核实可作为起点的公共步行空间，不包含封闭小区或建筑内部。Python 在调用 C++ 前检查中心点是否落入其中，真实区域的道路吸附上限为 5 米；不能可靠接入路网时返回不支持，而不是假设可穿越围墙。`supportedCenterBoundsMeters` 只是粗筛范围，不替代该多边形。

当前 Python 坐标换算采用小区域近似：`x=(lng-lng0)×111320×cos(lat0)`、`y=(lat-lat0)×111320`，经纬度均为 BD-09，余弦中的纬度使用弧度。标注时应使用同一换算，并人工核对路口、过街设施和道路两侧的位置。

普通道路按两侧人行道分别标注；右转等同侧连接用 `turn`，只有经核实的人行横道、天桥等才添加过街连接。地面横穿普通道路用 `crossing`，计入 20 秒等待。

只有经人工确认整段路面可双向步行并可在任意位置换侧的步行街或共享小巷，才标注一条 `shared_way` 中心线，并估计 `widthMeters`。其路口连接仍需用 `turn` 明确标注；相交但不连通的线不要共用节点。设施在共享通道任一侧通过 `accessEdgeId` 和实际 `accessPointMeters` 标注，横向距离计步行时间，没有过街等待。中心线是演示版近似，不等同于路面内严格几何最短路。

在本地开发中可以显式设置 `WALKING_NETWORK_PATH=contracts/engine-input.example.json` 使用合成样例；结果会标记为合成数据。

设施和灰区数据也由 Python 协作者整理成同一 JSON。每个设施设置 `category` 和一个或多个 `entrances`；每个入口要绑定具体 `accessEdgeId` 与街边接入点，非街边入口只有在核实可通行的 `accessPathMeters` 后才纳入严格耗时。按类别声明 `serviceCategories` 的 `dataStatus`：在线核查过的类别用 `reviewed_online`，但报告仍称“疑似灰区”；清单或入口未核齐用 `incomplete`，此类不输出灰区。详见 `contracts/engine-v2.README.md` 和合成输入样例。路网来源可由 Python 自行选择，传给引擎的必须是规范化图而非第三方原始响应。
