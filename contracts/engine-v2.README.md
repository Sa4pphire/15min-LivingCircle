# 步行路网引擎契约 v2

Python 向引擎标准输入写入一个 JSON 对象；引擎向标准输出写入一个 JSON 对象。`schemaVersion` 必须为 `2`。路网输入见 `engine-input.example.json`；方便联调的最小输入和对应输出分别见 `engine-input.minimal.example.json`、`engine-output.example.json`。样例均为合成数据。

- 坐标是相对于 `originBd09` 的局部米制坐标，X 向东、Y 向北。`originBd09` 只供 Python 坐标转换，引擎忽略此字段。
- 普通道路按左右两侧分别标注 `sidewalk` 边；每条边默认双向通行。若某侧没有可步行通道，则不创建该边。同一个 `streetBlockId` 的左右侧不能共用节点，否则会形成不计等待的隐式过街。
- 经人工核实整段可自由穿行的步行街／共享小巷只标一条双向中心线边：`kind: "shared_way"`、`streetBlockId`、`sharedWayType: "pedestrian_street" | "shared_alley"` 和正数 `widthMeters`；不写 `side`。同一个 `streetBlockId` 不得混用 `shared_way` 与 `sidewalk`。
- `turn` 连接同侧相邻人行道或共享通道，耗时仅为长度除以步速；不能直接连接同一普通道路街段的左右侧。`crossing` 仅用于人工确认的合法过街位置，额外增加 `crossingWaitSeconds`。坐标相交不会自动建连。
- `originMeters` 为查询点；可选 `originEdgeId` 指定人行道或共享通道。普通人行道最多吸附 30 米；共享通道仅在估计半宽外 3 米内吸附。普通道路两侧接近且未指定接入边时返回 `AMBIGUOUS_ORIGIN_SIDE`，不能免费换侧。
- `facilities` 可选，元素为 `{"id":"…","accessEdgeId":"…","accessPointMeters":[x,y]}`。接入边只能是 `sidewalk` 或 `shared_way`；共享通道两侧的接入点可投影到中心线，横向距离计入步行时间但不加等待。
- 成功结果中的 `reachableEdges` 是图上的可达街段，`frontierMeters` 是 15 分钟边界点。`displayGeometryMeters` 是 `{"type":"MultiPolygon","coordinates":[[[[x,y],...],...],...]}` 的近似展示面；每个 Polygon 第一环为外环，后续环为洞，数组可以为空或包含多个 Polygon。为兼容现有调用方，`displayPolygonMeters` 保留同一 `coordinates` 数组；Python 应优先读取对象，并校验两者一致。
- `facilityTravelTimes` 返回每个设施的 `id`、`accessEdgeId`、`reachable`、`travelTimeSeconds`；超出 900 秒但连通的设施仍有耗时，不连通时为 `null`。覆盖判断只用设施耗时，不用展示面。`diagnostics` 返回可达节点数、完整可达的过街边数及警告码。
- 错误结果为 `{"schemaVersion":2,"success":false,"error":{"code":"…","message":"…"}}`。

注意 `displayGeometryMeters` 只是 **GeoJSON 风格** 的局部米制几何，不是可直接上图的经纬度 GeoJSON。Python 必须根据 `originBd09` 将每个 `[x,y]` 转为 BD-09 `[lng,lat]`，然后再构造真正的 GeoJSON `MultiPolygon`。最小输入与输出样例在当前编译环境下逐字段一致；不同平台的浮点尾数可能略有差异。所有样例仅供开发和联调，不代表任何真实街区。

Python 协作者可按 [对接清单](python-handoff.md) 逐项联调。
