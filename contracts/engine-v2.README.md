# 步行路网引擎契约 v2

Python 向引擎标准输入写入一个 JSON 对象；引擎向标准输出写入一个 JSON 对象。`schemaVersion` 必须为 `2`。路网输入见 `engine-input.example.json`；方便联调的最小输入和对应输出分别见 `engine-input.minimal.example.json`、`engine-output.example.json`。样例均为合成数据。

- 坐标是相对于 `originBd09` 的局部米制坐标，X 向东、Y 向北。`originBd09` 只供 Python 坐标转换，引擎忽略此字段。
- 普通道路按左右两侧分别标注 `sidewalk` 边；每条边默认双向通行。若某侧没有可步行通道，则不创建该边。同一个 `streetBlockId` 的左右侧不能共用节点，否则会形成不计等待的隐式过街。
- 经人工核实整段可自由穿行的步行街／共享小巷只标一条双向中心线边：`kind: "shared_way"`、`streetBlockId`、`sharedWayType: "pedestrian_street" | "shared_alley"` 和正数 `widthMeters`；不写 `side`。同一个 `streetBlockId` 不得混用 `shared_way` 与 `sidewalk`。
- `turn` 连接同侧相邻人行道或共享通道，耗时仅为长度除以步速；不能直接连接同一普通道路街段的左右侧。`crossing` 仅用于人工确认的合法过街位置，额外增加全局 `crossingWaitSeconds`；单边可设置非负 `waitSeconds` 覆盖全局值（例如无信号等待的天桥设为 `0`）。坐标相交不会自动建连。
- `originMeters` 为查询点；可选 `originEdgeId` 指定人行道或共享通道。通常普通人行道最大吸附距离由 `maxOriginSnapMeters` 控制（默认 30 米，真实区域 5 米），共享通道仅在估计半宽外 3 米内吸附。**仅供合成演示**可显式设置 `allowOffNetworkOrigin: true` 和不超过本次步行预算的 `maxOriginSnapMeters`：起点投影至最近的可步行边，直线距离 ÷ 步速先计入耗时，剩余时间才沿图运行 Dijkstra；若抵达街边已耗尽预算则拒绝。此接入不保证穿越建筑／封闭地块可行，不得用于真实报告。普通道路两侧接近且未指定接入边时仍返回 `AMBIGUOUS_ORIGIN_SIDE`，不能免费换侧。
- `facilities` 可选。旧单入口 `{"id":"…","accessEdgeId":"…","accessPointMeters":[x,y]}` 仍可使用。新格式为 `{"id":"…","category":"shopping","entrances":[{"id":"gate","accessEdgeId":"…","streetAccessPointMeters":[x,y],"accessPathMeters":[[x,y],…]}]}`；路径可省略，此时入口即在街边。路径必须从街边接入点起，明确表示可通行路线，按折线长度计时；不能用穿越围墙的直线代替。多个入口取最短耗时。
- 灰区分析须声明 `serviceCategories`，如 `[{"id":"shopping","dataStatus":"reviewed_online"}]`。状态仅支持 `reviewed_online`（基于在线核查数据输出疑似灰区）和 `incomplete`（输出数据不足、不判断灰区）。设施的 `category` 必须引用已声明类别。每类从所有设施入口运行一次多源 Dijkstra，服务阈值与中心点等时圈均为 `thresholdSeconds`。
- 成功结果中的 `reachableEdges` 是扣除起点接入耗时后截断的图上可达街段，`frontierMeters` 是 15 分钟边界点，`originAccessSeconds` 是到吸附点的估算耗时。`displayGeometryMeters` 由路网到达时间场生成：常规横向外扩半径由可选的 `displayAreaRadiusMeters` 指定（默认 80 米）；两侧可达道路夹持的未标路径区域可在时间预算内额外填补，搜索距离最多为常规半径的 3 倍。可选 `displayMinHoleAreaSquareMeters` 控制封闭内洞的面积过滤阈值（默认 2500 平方米，小于阈值的内洞直接填平，设为 `0` 可关闭）；其他较小且时间上可达的内洞仍可能被填平。`displayBufferMeters`（默认 15 米）仍用于连接边和灰区的窄缓冲。结果是 `{"type":"MultiPolygon","coordinates":[[[[x,y],...],...],...]}` 的近似填充展示面，**不是固定圆或边界点的凸包**；每个 Polygon 第一环为外环，后续环为可能保留的大型内洞，数组可以为空或包含多个 Polygon。内部填补未校核建筑、围墙或水体阻隔，不得据此判定设施可达；空洞面积过滤不改变 `reachableEdges` 或设施覆盖统计。为兼容现有调用方，`displayPolygonMeters` 保留同一 `coordinates` 数组；Python 应优先读取对象，并校验两者一致。
- `facilityTravelTimes` 返回每个设施的 `id`、`category`、最佳 `accessEdgeId`／`bestEntranceId`、`reachable`、`travelTimeSeconds`；超出 900 秒但连通的设施仍有耗时，不连通时为 `null`。`grayZones` 逐类返回状态、当前中心点可达范围内的精确 `uncoveredEdges`、未覆盖街段长度／比例和近似 `displayGeometryMeters`。仅统计 `sidewalk`、`shared_way` 街段长度，不把过街边或转向边算作灰区。展示面不参与判定。`diagnostics` 返回可达节点数、完整可达的过街边数及警告码。
- 错误结果为 `{"schemaVersion":2,"success":false,"error":{"code":"…","message":"…"}}`。

注意 `displayGeometryMeters` 只是 **GeoJSON 风格** 的局部米制几何，不是可直接上图的经纬度 GeoJSON。Python 必须根据 `originBd09` 将每个 `[x,y]` 转为 BD-09 `[lng,lat]`，然后再构造真正的 GeoJSON `MultiPolygon`。仓库中的输入与输出样例用于字段结构示意，并非同一轮计算的逐字段配对；新展示面应以当前引擎实际输出为准。所有样例仅供开发和联调，不代表任何真实街区。

Python 协作者可按 [对接清单](python-handoff.md) 逐项联调。
