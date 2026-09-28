# 步行路网引擎契约 v2

Python 向引擎标准输入写入一个 JSON 对象；引擎向标准输出写入一个 JSON 对象。`schemaVersion` 必须为 `2`。演示统一读取现有 `data/networks/synthetic-preview.json`，由 Python 补入本次起点及计算参数后发送，不补充新的路网数据。C++ `--demo`／`--network` 可直接读取同一合成文件，`--input` 读取完整请求，二者复用同一图解析。字段说明见 `engine-input.example.json`；最小请求／输出见 `engine-input.minimal.example.json`、`engine-output.example.json`，这些小图仅供契约说明和单元测试。

- 坐标是相对于文件原点的局部米制坐标，X 向东、Y 向北。当前合成文件使用 `originWgs84`，请求声明 `wgs84ll`；BD-09 文件使用 `originBd09`，声明 `bd09ll`。两者只供 Python 转换地图坐标，C++ 计算只用局部米数；不得混贴坐标类型。
- 普通道路按左右两侧分别标注 `sidewalk` 边；每条边默认双向通行。若某侧没有可步行通道，则不创建该边。同一个 `streetBlockId` 的左右侧不能共用节点，否则会形成不计等待的隐式过街。
- 经人工核实整段可自由穿行的步行街／共享小巷只标一条双向中心线边：`kind: "shared_way"`、`streetBlockId`、`sharedWayType: "pedestrian_street" | "shared_alley"` 和正数 `widthMeters`；不写 `side`。同一个 `streetBlockId` 不得混用 `shared_way` 与 `sidewalk`。
- `turn` 连接同侧相邻人行道或共享通道，耗时仅为长度除以步速；不能直接连接同一普通道路街段的左右侧。`crossing` 仅用于人工确认的合法过街位置，额外增加全局 `crossingWaitSeconds`；单边可设置非负 `waitSeconds` 覆盖全局值（例如无信号等待的天桥设为 `0`）。坐标相交不会自动建连。
- `originMeters` 为查询点；可选 `originEdgeId` 指定人行道或共享通道。通常普通人行道最大吸附距离由 `maxOriginSnapMeters` 控制（默认 30 米，真实区域 5 米），共享通道仅在估计半宽外 3 米内吸附。**仅供合成演示**可显式设置 `allowOffNetworkOrigin: true` 和不超过本次步行预算的 `maxOriginSnapMeters`：起点投影至最近的可步行边，直线距离 ÷ 步速先计入耗时，剩余时间才沿图运行 Dijkstra；若抵达街边已耗尽预算则拒绝。此接入不保证穿越建筑／封闭地块可行，不得用于真实报告。普通道路两侧接近且未指定接入边时仍返回 `AMBIGUOUS_ORIGIN_SIDE`，不能免费换侧。
- `facilities` 可选。旧单入口 `{"id":"…","accessEdgeId":"…","accessPointMeters":[x,y]}` 仍可使用。新格式为 `{"id":"…","category":"shopping","entrances":[{"id":"gate","accessEdgeId":"…","streetAccessPointMeters":[x,y],"accessPathMeters":[[x,y],…]}]}`；路径可省略，此时入口即在街边。路径必须从街边接入点起，明确表示可通行路线，按折线长度计时；不能用穿越围墙的直线代替。多个入口取最短耗时。
- 灰区分析须声明 `serviceCategories`，如 `[{"id":"shopping","dataStatus":"reviewed_online"}]`。状态仅支持 `reviewed_online`（基于在线核查数据输出疑似灰区）和 `incomplete`（输出数据不足、不判断灰区）。设施的 `category` 必须引用已声明类别。每类从所有设施入口运行一次多源 Dijkstra，服务阈值与中心点等时圈均为 `thresholdSeconds`。
- 成功结果中的 `reachableEdges` 是扣除起点接入耗时后截断的图上可达街段，`frontierMeters` 是 15 分钟边界点，`originAccessSeconds` 是到吸附点的估算耗时。`displayGeometryMeters` 由路网到达时间场生成：常规横向外扩半径由可选的 `displayAreaRadiusMeters` 指定（默认 80 米）；两侧可达道路夹持的未标路径区域可在时间预算内额外填补，搜索距离最多为常规半径的 3 倍。可选 `displayMinHoleAreaSquareMeters` 控制封闭内洞的面积过滤阈值（默认 2500 平方米，小于阈值的内洞直接填平，设为 `0` 可关闭）；其他较小且时间上可达的内洞仍可能被填平。`displayBufferMeters`（默认 15 米）仍用于连接边和灰区的窄缓冲。结果是 `{"type":"MultiPolygon","coordinates":[[[[x,y],...],...],...]}` 的近似填充展示面，**不是固定圆或边界点的凸包**；每个 Polygon 第一环为外环，后续环为可能保留的大型内洞，数组可以为空或包含多个 Polygon。内部填补未校核建筑、围墙或水体阻隔，不得据此判定设施可达；空洞面积过滤不改变 `reachableEdges` 或设施覆盖统计。为兼容现有调用方，`displayPolygonMeters` 保留同一 `coordinates` 数组；Python 应优先读取对象，并校验两者一致。
- `facilityTravelTimes` 返回每个设施的 `id`、`category`、最佳 `accessEdgeId`／`bestEntranceId`、`reachable`、`travelTimeSeconds`；超出 900 秒但连通的设施仍有耗时，不连通时为 `null`。`grayZones` 逐类返回状态、当前中心点可达范围内的精确 `uncoveredEdges`、未覆盖街段长度／比例和近似 `displayGeometryMeters`。仅统计 `sidewalk`、`shared_way` 街段长度，不把过街边或转向边算作灰区。展示面不参与判定。`diagnostics` 返回可达节点数、完整可达的过街边数、`closedRoadFaceCount`（按显式连接且完整可达的道路所围成的面数）、`roadClosureFilledCellCount`（由这些面额外补入的展示网格数）及警告码。几何相交但节点不连接不形成闭合面；仅凭面积过滤不能消除与外部相通的视觉缺口。
- 错误结果为 `{"schemaVersion":2,"success":false,"error":{"code":"…","message":"…"}}`。

注意 `displayGeometryMeters` 只是 **GeoJSON 风格** 的局部米制几何，不是可直接上图的经纬度 GeoJSON。Python 必须根据 `originBd09` 将每个 `[x,y]` 转为 BD-09 `[lng,lat]`，然后再构造真正的 GeoJSON `MultiPolygon`。仓库中的输入与输出样例用于字段结构示意，并非同一轮计算的逐字段配对；新展示面应以当前引擎实际输出为准。所有样例仅供开发和联调，不代表任何真实街区。

Python 协作者可按 [对接清单](python-handoff.md) 逐项联调。

## 独立的局部路网实验（可选）

`topologyStatus` 或某类 `localInventoryStatus` 缺失时默认 `incomplete`，未覆盖部分只列为未知；`boundaryNodeIds` 列表仍必须显式提供。局部模式不允许 `allowOffNetworkOrigin: true`，不能使用未核实的路外直线接入。

当前统一合成文件没有 `localExperiment`、设施或类别。Python 及 C++ 文件演示的局部适配将计算模式设为 180 秒，显式传入 `boundaryNodeIds: []`、`topologyStatus: "incomplete"`，不声称出口已核齐；类别和设施保持空数组。结果 `localGrayZones` 为空，Python 输出 `metadata.grayZoneStatus: "data_insufficient"`、`LOCAL_FACILITY_DATA_NOT_PROVIDED` 和 `LOCAL_BOUNDARY_NOT_MARKED`，页面只画可达街段。下面的小路口配对样例用于展示将来数据齐全时的三态字段，并非默认数据源。

局部实验与完整 15 分钟报告是两种不同用途。仅当输入带 `localExperiment` 对象时启用；未带此字段的 v2 主接口和结果保持兼容。局部模式未显式提供 `thresholdSeconds` 时默认 **180 秒**，界面必须标为“3 分钟局部路网实验”；若显式指定其他正数阈值，界面应显示实际时长。不要将其命名为 15 分钟等时圈或纳入百度 API 主报告。可运行的合成输入及其配对输出分别见 [engine-local-experiment.input.example.json](engine-local-experiment.input.example.json)、[engine-local-experiment.output.example.json](engine-local-experiment.output.example.json)。该样例中购物设施覆盖 `x=0..234` 米，候选未覆盖为 `x=234..366` 米，裁剪边界影响下的未知为 `x=366..434` 米；医疗设施清单未核齐，因此全部起点可达街段均为未知。

`localExperiment` 格式为 `{"boundaryNodeIds":["cut_1",...],"topologyStatus":"verified"}`。`boundaryNodeIds` 只能列出现有 `nodes[*].id`，表示**人为裁剪路网时被切断的所有出口**；真实道路尽头即使度数为 1，也不能放入此列表。`topologyStatus` 只接受 `verified` 或 `incomplete`。仅当所有裁剪出口及局部连接关系已核查时才填 `verified`；漏标出口时必须填 `incomplete`。每个 `serviceCategories[*]` 可另加 `localInventoryStatus: "verified" | "incomplete"`，表示该类设施在局部实验所需范围内的入口清单核查状态；未填时按 `incomplete` 处理。它不等同于主报告的 `dataStatus: "reviewed_online"`。设施入口、步速和过街等待仍沿用普通 v2 格式与计时规则。

局部结果单独放在 `result.localGrayZones`；每类包含 `category`、`coveredEdges`、`candidateUncoveredEdges`、`unknownEdges`、对应的 `coveredLengthMeters`／`candidateUncoveredLengthMeters`／`unknownLengthMeters` 和 `warnings`。各 `*Edges` 均采用 `reachableEdges` 相同的 `{edgeId,kind,pathMeters,widthMeters?}` 街段格式，且只含人行道或共享通道，不含 `turn`／`crossing`。计算只在**当前起点于局部图上可达**的街段内进行：已录设施在服务阈值内可达的是“已覆盖”；未被已录设施覆盖但在阈值内可到裁剪边界的，归“未知”；余下街段只有在局部拓扑和该类设施清单均为 `verified` 时，才是“候选未覆盖”，否则也归“未知”。这些集合须在边的内部按耗时切分，不能仅比较端点；灰区面及完整 15 分钟未覆盖比例均不输出。局部模式的原有 `result.grayZones` 应为空，避免调用方把局部结论误作正式灰区。

若起点可在局部阈值内到达任一裁剪边界，`result.diagnostics.warnings` 包含 `LOCAL_REACHABILITY_MAY_BE_TRUNCATED`。即使未触发此警告，小图也不能据此外推整个街道的设施供给；只有已明确核查的局部街段才可被称为“候选未覆盖”。前端局部模式应绘制三色精确街段，不用 `displayGeometryMeters` 的近似缓冲面判断或绘制局部灰区。
