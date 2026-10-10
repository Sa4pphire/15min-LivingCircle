# 步行路网引擎契约 v2

## 统一步行类型

普通行走边统一输出 `kind: "walkway"`；`turn`、`crossing` 不变。迁移只改变类型表达，不合并道路，不改变节点／边 ID、端点、折线、入口引用及等待时间。

- `accessMode: "separated"`：保留 `streetBlockId`、`side: "left" | "right"`；同一道路两侧不能共用节点，不能用 `turn` 免费换侧。沿用原人行道吸附和 POI 同侧接入规则，不设置共享宽度。
- `accessMode: "shared"`：保留 `streetBlockId`、正数 `widthMeters`、`sharedWayType`，不设置 `side`；沿用原共享通道的宽度和横向接入计时规则。
- 新 `walkway` 输入必须明确填写 `accessMode`。同一道路组不能混用两种接入方式，等待时间仍只能设置在 `crossing` 上。
- 旧 `sidewalk`／`shared_way` JSON 仍可读取，分别映射到 `separated`／`shared`。输出与现有数据文件只使用 `walkway`；下文旧名称也用于解释保留的建模语义。
- `--health` 新增 `walkway: true` 能力标识。Python 传输字段已包含 `accessMode`；更新后须重新构建引擎并重启后端。

例如：`{"id":"walk-left","kind":"walkway","accessMode":"separated","streetBlockId":"block-1","side":"left","from":"a","to":"b","pathMeters":[[0,0],[100,0]]}`。

完整迁移约定见 [统一 walkway 说明](../docs/unified-walkways.md)。

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

## 按需设施计算

### 设施耗时专用计算与性能诊断

v2 可选 `facilitiesOnly: true`：仍校验路网、吸附起点、拆分所有设施入口并运行 Dijkstra，
返回完整 `facilityTravelTimes`；不生成展示面、可达街段或类别灰区，其相关数组为空。
不能同时指定 `localExperiment`、`routeFacilityId` 或 `routeOnly`。这些空数组不代表没有可达范围，
Python 必须保留第一轮完整结果，并只合并设施耗时；未核齐类别继续返回“数据不足”。
已核查类别需要重新计算覆盖关系，不能使用该模式复用过时灰区。

`diagnostics.timingsMs` 记录原生阶段耗时，单位为毫秒；`inputParse` 包括 JSON 解析、类型转换与解析端校验。
`graphValidation` 是计算入口校验，`originSnap`、`graphBuild`、`dijkstra`、`facilityTimes` 各自独立计时；
完整模式另有 `reachableEdges`、`displayGeometry`、`coverage`，设施专用模式不含后三项。
`diagnostics.buildMode` 与 `--health` 的 `buildMode` 指示 Debug/Release，不用于判定结果精度。
Python 额外添加 `diagnostics.invocation`（编码、进程启动、通信、解码、总时长与字节数），总调用耗时包含原生各阶段。

Python 分阶段状态保持 `status: running` 时可带 `result`，以 `resultRevision` 标识新结果；
`poiStatus` 独立区分 `pending`、`ready`、`partial`、`unavailable` 和 `error`。
`GET /api/v1/analyses/{id}?afterRevision=N` 在版本未变化时返回 `result: null`，客户端必须保留之前结果，
完成状态仍正常轮询。默认不带参数的旧客户端继续获取完整结果。
`metadata.geometryRevision` 未变化时，设施增量不应触发地图重新适配或重复描绘。

### 单设施最短路径

v2 输入可选 `routeFacilityId`（必须引用本次 `facilities[*].id`）和 `routeOnly: true`。
起点与设施入口仍按现有规则拆入图；Dijkstra 在松弛时记录前驱边，随后回溯方向正确的折线。
等待时间参与选路，不能按几何距离替代。多个入口仍取耗时最短的一个，路外起点接入和已提供的设施接入路径也计时。

仅请求路径时跳过等时圈网格、面构建及类别灰区，不必重新分析整幅地图。
输出额外的 `result.facilityRoute`：`facilityId`、`entranceId`、`connected`、`withinThreshold`、
`travelTimeSeconds`、`lengthMeters`、`crossingWaitSeconds`、`pathMeters` 和有序 `segments`。
每段包含 `edgeId`、`kind`、`pathMeters`、`travelTimeSeconds`；接入段用 `origin_access`／`facility_access`，
其余沿用 `sidewalk`／`shared_way`／`turn`／`crossing`。零距离路径返回两个相同点及空段数组。
不连通时无路径且耗时为 `null`；连通但超过阈值仍返回路线，`withinThreshold: false`。
未请求路线时不增加该字段，已有等时圈调用保持兼容。

网页调用 `POST /api/v1/analyses/{analysisId}/poi-route`，请求体为 `{"poiId":"报告中的 POI ID"}`。
Python 保留最近 16 次完成的合成分析上下文，使用那次起点、路网及绑定入口，不相信客户端提供的坐标或边 ID，
也不调用百度。返回地图坐标 `LineString` 和分段路线，前端再按同一标定映射到 SVG。
状态为 `ready`／`unmapped`／`unreachable`。合成模式没有绑定入口时，在最近可步行边接入距离加 10 米的局部距离带内，
最多生成 16 个候选投影点到 POI 的直线接入，去重共享端点。把候选作为同一设施的多个 `entrances`，
一次 C++ Dijkstra 按“到候选的路网耗时＋最后接入耗时”选最优，不再仅按 POI 到道路的直线距离选路。
最近边是普通人行道时，其他候选只限同一 `streetBlockId`、同一 `side`；最近边是共享通道时只扩展共享通道候选。
接入直线不得横穿其他已建模的普通人行道，以免借候选跳过道路侧保护和过街等待。
这只是有界局部候选的合成估算，不保证所有现实入口或几何最短路径，仍未核查建筑、围墙、水体和通行权限。
已有绑定入口（包括百度导航点的未核实绑定）保持原规则，不被候选替换；多个已绑定入口仍由 C++ 比较总耗时。
候选仅加入这一次路径请求，不改原图、已保存设施或正式覆盖统计；直线段长度 ÷ 步速计入总耗时。
返回 `destinationAccessMode: "estimated_straight_line"`、`destinationAccessDistanceMeters` 和
`UNVERIFIED_STRAIGHT_LINE_POI_ACCESS` 警告，前端必须用虚线标记“估算穿越地块，未核实”。
`destinationAccessDistanceMeters` 是 C++ 实际选中入口的接入长度，不是几何最近候选的长度；
合成估算另外返回 `destinationAccessCandidateCount` 和 `destinationAccessSelection: "minimum_total_time_local_candidates"`。
绑定入口的路线仍为 `destinationAccessMode: "bound_entrance"`。没有有效局部坐标或可步行边才返回 `unmapped`；
图上过街仍须走显式连接并计等待，不通过该估算规则新增图上换侧连接。
上下文过期返回 HTTP 409 `ROUTE_CONTEXT_EXPIRED`，须重新计算等时圈。
路线终点是绑定入口，不保证与百度 POI 的中心标记点重合；现有合成路网和入口仍标为未核实。

## 独立的局部路网实验（可选）

`topologyStatus` 或某类 `localInventoryStatus` 缺失时默认 `incomplete`，未覆盖部分只列为未知；`boundaryNodeIds` 列表仍必须显式提供。局部模式不允许 `allowOffNetworkOrigin: true`，不能使用未核实的路外直线接入。

当前统一合成文件没有 `localExperiment`、设施或类别。Python 及 C++ 文件演示的局部适配将计算模式设为 180 秒，显式传入 `boundaryNodeIds: []`、`topologyStatus: "incomplete"`，不声称出口已核齐；类别和设施保持空数组。结果 `localGrayZones` 为空，Python 输出 `metadata.grayZoneStatus: "data_insufficient"`、`LOCAL_FACILITY_DATA_NOT_PROVIDED` 和 `LOCAL_BOUNDARY_NOT_MARKED`，页面只画可达街段。下面的小路口配对样例用于展示将来数据齐全时的三态字段，并非默认数据源。

局部实验与完整 15 分钟报告是两种不同用途。仅当输入带 `localExperiment` 对象时启用；未带此字段的 v2 主接口和结果保持兼容。局部模式未显式提供 `thresholdSeconds` 时默认 **180 秒**，界面必须标为“3 分钟局部路网实验”；若显式指定其他正数阈值，界面应显示实际时长。不要将其命名为 15 分钟等时圈或纳入百度 API 主报告。可运行的合成输入及其配对输出分别见 [engine-local-experiment.input.example.json](engine-local-experiment.input.example.json)、[engine-local-experiment.output.example.json](engine-local-experiment.output.example.json)。该样例中购物设施覆盖 `x=0..234` 米，候选未覆盖为 `x=234..366` 米，裁剪边界影响下的未知为 `x=366..434` 米；医疗设施清单未核齐，因此全部起点可达街段均为未知。

`localExperiment` 格式为 `{"boundaryNodeIds":["cut_1",...],"topologyStatus":"verified"}`。`boundaryNodeIds` 只能列出现有 `nodes[*].id`，表示**人为裁剪路网时被切断的所有出口**；真实道路尽头即使度数为 1，也不能放入此列表。`topologyStatus` 只接受 `verified` 或 `incomplete`。仅当所有裁剪出口及局部连接关系已核查时才填 `verified`；漏标出口时必须填 `incomplete`。每个 `serviceCategories[*]` 可另加 `localInventoryStatus: "verified" | "incomplete"`，表示该类设施在局部实验所需范围内的入口清单核查状态；未填时按 `incomplete` 处理。它不等同于主报告的 `dataStatus: "reviewed_online"`。设施入口、步速和过街等待仍沿用普通 v2 格式与计时规则。

局部结果单独放在 `result.localGrayZones`；每类包含 `category`、`coveredEdges`、`candidateUncoveredEdges`、`unknownEdges`、对应的 `coveredLengthMeters`／`candidateUncoveredLengthMeters`／`unknownLengthMeters` 和 `warnings`。各 `*Edges` 均采用 `reachableEdges` 相同的 `{edgeId,kind,pathMeters,widthMeters?}` 街段格式，且只含人行道或共享通道，不含 `turn`／`crossing`。计算只在**当前起点于局部图上可达**的街段内进行：已录设施在服务阈值内可达的是“已覆盖”；未被已录设施覆盖但在阈值内可到裁剪边界的，归“未知”；余下街段只有在局部拓扑和该类设施清单均为 `verified` 时，才是“候选未覆盖”，否则也归“未知”。这些集合须在边的内部按耗时切分，不能仅比较端点；灰区面及完整 15 分钟未覆盖比例均不输出。局部模式的原有 `result.grayZones` 应为空，避免调用方把局部结论误作正式灰区。

若起点可在局部阈值内到达任一裁剪边界，`result.diagnostics.warnings` 包含 `LOCAL_REACHABILITY_MAY_BE_TRUNCATED`。即使未触发此警告，小图也不能据此外推整个街道的设施供给；只有已明确核查的局部街段才可被称为“候选未覆盖”。前端局部模式应绘制三色精确街段，不用 `displayGeometryMeters` 的近似缓冲面判断或绘制局部灰区。
