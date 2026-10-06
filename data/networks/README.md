# 开发演示路网

当前 C++ 演示统一使用已经建模的 `synthetic-preview.json`，本轮不生成或补充其他路网数据。C++ `--demo`、Python 15 分钟合成演示和 3 分钟局部实验默认共用它；可用 `SYNTHETIC_NETWORK_PATH` 指定该文件位置。局部模式仅阈值和严格接入规则不同，不裁剪或添加节点、边、设施。`contracts/` 内的小图是字段示例及单元测试，不是默认运行数据。

该文件设施及类别为空，也没有核实过的裁剪出口；局部接口明确标记 `grayZoneStatus: "data_insufficient"`，只显示可达街段，边界核查状态保持 `incomplete`，不按节点度数猜出口。后续若显式提供局部标注文件，可用 `LOCAL_EXPERIMENT_NETWORK_PATH` 覆盖；当前无需这么做。

真实分析入口仍与合成演示隔离：`shanghai-new-jiangwan.json` 目前不存在，因此真实路网请求返回 `UNSUPPORTED_AREA`，不自动替换成合成数据。下方真实数据标注要求是未来接入的约束，本次并不要求采集它们。

## 临时合成路网：只用于三端联调

`synthetic-preview.json` 由 `python backend/scripts/export_synthetic_preview.py` 从前端的
`demoRoadGraph.local.json` 生成，并应用 `synthetic-preview.annotations.json` 中的人工校对记录。
当前导出文件有 7,175 个节点、8,071 条边：所有主干道源段由
`majorSidewalkPolicy` 统一指定外侧人行道。相向双车道按物理道路保留两条外侧 `sidewalk`，单幅道路保留左右两侧；旧 7 个局部遮罩已被全图规则取代。其余 `roadLocal`／`roadPath` 暂按 `shared_way` 处理，跨越主干道两侧的原中心连接改为带等待过街。主干道交点处的共享道路
分支使用独立端点，不直接从道路一侧免费通到另一侧；推断及人工标注的过街连接
均**未经实地核实**，默认等待时间为 20 秒。图的推断连接也未经核实；
文件中的 `synthetic: true` 不得移除。

### 统一校对入口

路网修正统一维护在 `synthetic-preview.annotations.json`：`junctions` 记录显式路口，
`crossings` 记录过街，`dividedRoadSections` 记录双车道的局部外侧人行道规则，
`connections` 记录已明确标注、仍未核实的共享步道补连。
`majorSidewalkPolicy` 覆盖所有主干道源边并记录各段保留侧、车道对应及层级；它是用户要求的合成简化模型，不代表现场核实。详见
[全图主干道两侧模型](major-sidewalk-model.md)。
`sourceTopology` 记录原始 OSM 权限、共享节点及短连接恢复证据；源图生成时过滤已知禁止步行边，恢复有共享源节点 ID 支持的连接。2026-10-05 国泓路周边试点见
[源拓扑校对记录](source-topology-review.md)；2026-10-06 已扩展到全部现有道路的源权限与共享节点核查，见
[全图十字路口校对](source-topology-global-review.md)。两轮均不是实地核实；复杂路口和未恢复的主干道接入仍列为待核查。
来源明确标为 `footway=crossing` 的步道转为 `crossing`，使用引擎默认等待，不能作为免费共享通道或 POI 接入边。
纯形状点合并为一条折线，避免重复等待；横道中间存在真实分支时保留分段等待并标明 `crossingReviewRequired`，不假定存在安全岛。
撤销的旧标注保留在 `retiredJunctions`／`retiredConnections`，被替换的引用保留在 `sourceTopology.edgeReferenceMigrations`。
`synthetic-preview.json` 和前端路网中的标注元数据是生成结果，不另行维护一套修正规则。
旧 `frontend/src/data/demoSidewalkSections.local.json` 仅保留为历史参考，不再由生成脚本读取。

2026-10-01 首批主干道校对新增 6 段明确范围，涉及国权北路、殷行路、殷高东路，
去除约 1.02 公里的框内中央伪人行道；原有两处用户标注的共享步道补连已迁入统一文件。
复杂路口、弯道及国帆路／江湾城路的车道配对继续待核查，不自动补线。
修正依据是本地道路几何和现有标注，不是现场通行证明。具体范围、验证结果及复现方式见
[主干道校对记录](main-road-review.md)。

2026-09-28 的两处蓝点校对位于小环路的节点 `shared:p:54.5:-501.1` 和
`shared:p:-10.6:-430.4`。这两个节点原本在小环路内部已相连，但未接到旁边的主路。
每处现在先以 `turn` 接入近侧人行道，再以 `crossing` 到达对侧；人行道在接入位置拆分，
不依靠几何相交自动连通。`manual-crossing:park-loop-north` 和
`manual-crossing:park-loop-south` 各自明确设置 `waitSeconds: 20`。
记录为 `user_marked_unverified`，只表示用户在 SVG 中确认了建模位置，不证明现场有合法人行横道。
重新运行导出脚本会保留这些修正；`python backend/scripts/render_network_audit.py`
可在本地生成 `synthetic-preview-audit.svg` 供校对，生成的 SVG 不参与运行，也不提交。

同日蓝色圈选的十字路口记为 `blue-crossroads-01`，局部中心为 `[-273.15,1347.85]`。
四个街角增加 4 条 `turn`；四个路口外侧增加 4 条 `crossing`，双向主路原有内侧人行道
增加 2 条带等待的直行过街连接，共 6 条，每条加 20 秒。12 个接入节点分别从原道路中心
端点沿各自人行道退开约 18 米，不共用路口中心节点；原中心短 `shared_way`
`w:226889561:0:0` 及其两条接入边被显式替换，防止免费穿过路口。
这些都是用户截图校对后的合成几何，不表示核实了具体斑马线或信号灯。
`python backend/scripts/render_junction_audit.py blue-crossroads-01` 会生成
本地生成的 `blue-crossroads-01.svg` 局部图中，蓝色实线为同侧转弯，蓝色虚线为需等待的过街连接。

此图沿用 OSM SVG 预览的 WGS-84 来源和局部米制几何，但将预览的“Y 向南”转为引擎的“Y 向北”。
它使用 `originWgs84`，API 请求中心点必须带 `coordType: "wgs84ll"`；不要把它伪装成 BD-09
路网。前端“合成算法”模式调用独立的 `/api/v1/synthetic-analyses`，由 C++ 返回可达街段和
MultiPolygon 近似等时圈，再转回与“真实区域”模式共用的 SVG 底图坐标显示。设施和类别为空，
因此不生成设施覆盖或灰区报告。

从仓库根目录启动联调时，先构建当前 C++ 源码，设置
`CPP_ENGINE_PATH=cpp-engine/build/isochrone_engine`（Windows 为相应 `.exe`；本机也可指向独立编译的 `cpp-engine/build/isochrone_engine_synthetic.exe`），启动
`uvicorn app.main:app --app-dir backend --port 8000`，然后运行前端 `npm run dev`。
合成与局部实验接口默认加载本文件，与真实分析接口的 `WALKING_NETWORK_PATH` 互不影响；
“真实区域”模式仍使用浏览器内固定圆示意，无需后端。
15 分钟合成模式允许计时的未核实直线接入；3 分钟局部模式仍会对离步行边过远的点返回 `ORIGIN_NOT_ON_WALKWAY`。可用已有边 `w:154811345:2:0` 的中点（局部米数 `[-54.25,-203.25]`，WGS-84 `121.504429458,31.331174183`）联调。
整个文件仅证明数据链路可用，**不能用于真实步行可达、过街或设施覆盖判定**。

合成模式向引擎发送 `allowOffNetworkOrigin: true`，将起点到最近可步行边的直线距离按步速计时并从 900 秒内扣除；最大搜索距离为 1,170 米。共享通道原有的 3 米吸附限制只在严格模式保留。接入虚线未核实，可能穿越不可通行地块；真实路网仍严格限制 5 米且要求起点落在已核实公共步行空间内。展示面由扣除接入时间后的可达街段生成 MultiPolygon，不是固定圆。

文件采用 `contracts/engine-input.example.json` 的节点和边格式，并额外包含 `originBd09`、`supportedCenterBoundsMeters`、`synthetic:false`。`originBd09` 是整个路网局部米制坐标的原点，`supportedCenterBoundsMeters` 是允许发起分析的中心点范围。路网还必须至少覆盖从该范围出发步行 15 分钟可能达到的全部道路，否则边界会被数据边缘截断。

真实数据还必须提供 `publicWalkableAreasMeters`：若干闭合的局部米制多边形环 `[[[x,y],...], ...]`，仅覆盖已核实可作为起点的公共步行空间，不包含封闭小区或建筑内部。Python 在调用 C++ 前检查中心点是否落入其中，真实区域的道路吸附上限为 5 米；不能可靠接入路网时返回不支持，而不是假设可穿越围墙。`supportedCenterBoundsMeters` 只是粗筛范围，不替代该多边形。

当前 Python 坐标换算采用小区域近似：`x=(lng-lng0)×111320×cos(lat0)`、`y=(lat-lat0)×111320`，经纬度均为 BD-09，余弦中的纬度使用弧度。标注时应使用同一换算，并人工核对路口、过街设施和道路两侧的位置。

普通道路按两侧人行道分别标注；右转等同侧连接用 `turn`，只有经核实的人行横道、天桥等才添加过街连接。地面横穿普通道路用 `crossing`，计入 20 秒等待。

只有经人工确认整段路面可双向步行并可在任意位置换侧的步行街或共享小巷，才标注一条 `shared_way` 中心线，并估计 `widthMeters`。其路口连接仍需用 `turn` 明确标注；相交但不连通的线不要共用节点。设施在共享通道任一侧通过 `accessEdgeId` 和实际 `accessPointMeters` 标注，横向距离计步行时间，没有过街等待。中心线是演示版近似，不等同于路面内严格几何最短路。

在本地开发中可以显式设置 `WALKING_NETWORK_PATH=contracts/engine-input.example.json` 使用合成样例；结果会标记为合成数据。

设施和灰区数据也由 Python 协作者整理成同一 JSON。每个设施设置 `category` 和一个或多个 `entrances`；每个入口要绑定具体 `accessEdgeId` 与街边接入点，非街边入口只有在核实可通行的 `accessPathMeters` 后才纳入严格耗时。按类别声明 `serviceCategories` 的 `dataStatus`：在线核查过的类别用 `reviewed_online`，但报告仍称“疑似灰区”；清单或入口未核齐用 `incomplete`，此类不输出灰区。详见 `contracts/engine-v2.README.md` 和合成输入样例。路网来源可由 Python 自行选择，传给引擎的必须是规范化图而非第三方原始响应。

## 小片真实路网：独立的局部实验

若只实地核实一小段路网，请放在独立文件，并加入 `localExperiment`，不要替换上面的完整 15 分钟路网或伪装为新江湾城整体结果。局部实验默认阈值为 180 秒；即使计算出候选未覆盖街段，也只反映核查范围内的图模型。`contracts/engine-local-experiment.input.example.json` 是**合成教学数据**，不是真实标注文件。

实地标注时，必须将每个因数据裁剪而在图边缘中断的出口节点 ID 写入 `localExperiment.boundaryNodeIds`；真正的道路尽头不要写入。两者不能按节点度数自动区分。只有确认没有漏标出口且路口连接关系已核对时，`topologyStatus` 才设为 `verified`，否则设为 `incomplete`。每类设施入口清单另用 `serviceCategories[*].localInventoryStatus` 标为 `verified` 或 `incomplete`，缺省是后者。未核齐时，已录设施可证实的“已覆盖”仍可显示，但其余街段只能显示“未知”，不能宣称匮乏。

Python 应为此提供与 900 秒主报告加载器相隔离的入口，只加载经指定的局部实验文件。它将起点换算到该文件的局部米制坐标并调用同一 C++ 引擎，但页面只画 `localGrayZones` 的彩色街段，显示裁剪警告，不展示正式灰区面或完整 15 分钟覆盖比例。路网及设施来源、核查范围、裁剪出口应能在实验页面查到；未经核实的局部 JSON 只能作为合成演示。
