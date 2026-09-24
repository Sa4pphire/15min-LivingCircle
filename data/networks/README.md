# 实地步行路网

这里放经人工核对的演示区域路网 JSON。默认预期文件为 `shanghai-new-jiangwan.json`；该文件目前不存在，因此真实区域分析会返回 `UNSUPPORTED_AREA`。

文件采用 `contracts/engine-input.example.json` 的节点和边格式，并额外包含 `originBd09`、`supportedCenterBoundsMeters`、`synthetic:false`。`originBd09` 是整个路网局部米制坐标的原点，`supportedCenterBoundsMeters` 是允许发起分析的中心点范围。路网还必须至少覆盖从该范围出发步行 15 分钟可能达到的全部道路，否则边界会被数据边缘截断。

真实数据还必须提供 `publicWalkableAreasMeters`：若干闭合的局部米制多边形环 `[[[x,y],...], ...]`，仅覆盖已核实可作为起点的公共步行空间，不包含封闭小区或建筑内部。Python 在调用 C++ 前检查中心点是否落入其中，真实区域的道路吸附上限为 5 米；不能可靠接入路网时返回不支持，而不是假设可穿越围墙。`supportedCenterBoundsMeters` 只是粗筛范围，不替代该多边形。

当前 Python 坐标换算采用小区域近似：`x=(lng-lng0)×111320×cos(lat0)`、`y=(lat-lat0)×111320`，经纬度均为 BD-09，余弦中的纬度使用弧度。标注时应使用同一换算，并人工核对路口、过街设施和道路两侧的位置。

普通道路按两侧人行道分别标注；右转等同侧连接用 `turn`，只有经核实的人行横道、天桥等才添加过街连接。地面横穿普通道路用 `crossing`，计入 20 秒等待。

只有经人工确认整段路面可双向步行并可在任意位置换侧的步行街或共享小巷，才标注一条 `shared_way` 中心线，并估计 `widthMeters`。其路口连接仍需用 `turn` 明确标注；相交但不连通的线不要共用节点。设施在共享通道任一侧通过 `accessEdgeId` 和实际 `accessPointMeters` 标注，横向距离计步行时间，没有过街等待。中心线是演示版近似，不等同于路面内严格几何最短路。

在本地开发中可以显式设置 `WALKING_NETWORK_PATH=contracts/engine-input.example.json` 使用合成样例；结果会标记为合成数据。

设施和灰区数据也由 Python 协作者整理成同一 JSON。每个设施设置 `category` 和一个或多个 `entrances`；每个入口要绑定具体 `accessEdgeId` 与街边接入点，非街边入口只有在核实可通行的 `accessPathMeters` 后才纳入严格耗时。按类别声明 `serviceCategories` 的 `dataStatus`：在线核查过的类别用 `reviewed_online`，但报告仍称“疑似灰区”；清单或入口未核齐用 `incomplete`，此类不输出灰区。详见 `contracts/engine-v2.README.md` 和合成输入样例。路网来源可由 Python 自行选择，传给引擎的必须是规范化图而非第三方原始响应。
