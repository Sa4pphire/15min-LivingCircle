# Python ↔ C++ 对接清单

本页供负责 FastAPI／数据处理的协作者使用。当前统一使用已建模的 **`data/networks/synthetic-preview.json`**，无需再采集或补造路网、设施和裁剪出口。`data/networks/shanghai-new-jiangwan.json` 尚未提供，不能把合成结果当作真实生活圈报告。`contracts/` 的小图仅作字段说明和单元测试。

## 现成入口

- 输入：[engine-input.example.json](engine-input.example.json) 是完整混合路网样例；[engine-input.minimal.example.json](engine-input.minimal.example.json) 与 [engine-output.example.json](engine-output.example.json) 是可复现的最小往返样例。
- Python 已有 `backend/app/network.py`：读取标注文件、转换 BD-09 ↔ 局部米制、构造引擎输入并把结果转换为最终地图数据。
- Python 已有 `backend/app/engine.py`：以标准输入调用 C++ 可执行文件，25 秒超时，解析 v2 输出。调用顺序为 `load_engine_request(center) → run_engine(payload) → build_analysis_result(result, metadata)`。

## 在线 POI 与共用缓存

合成／局部请求可加 `includePois: true`。Python 通过共享 `BaiduClient → cache.py` 获取学校、医院、超市、公共服务候选，规范化后加入本次 C++ 输入，不改写静态路网。原有协作者 `poi.py.collect_pois` 与演示 `pois.py.PoiService` 共用客户端响应缓存；完全共用四类、500 米网格与分页策略时，请直接用 `PoiService` 或 `POST /api/v1/pois/search`。

双方统一 `ANALYSIS_CACHE_DIR` 和 `POI_CACHE_PATH`，不要各自启用独立缓存目录或默认强制刷新。默认 POI 缓存 7 天，旧演示 SQLite 中有效数据会无 API 请求迁移。输出增量见 [baidu-poi-result.example.json](baidu-poi-result.example.json)，配置、坐标与未核实入口的限制见 [共享缓存说明](../docs/baidu-poi-integration.md)。`poiFacilities` 原始坐标是 BD-09，不能直接画在 WGS-84 图层；`insideDisplayPolygon` 不代表路网可达，模型耗时仍以 C++ 返回为准。

## 路网对接约定

2026-10-07 起，沿路行走边统一为 `kind: "walkway"`，必须带 `accessMode: "separated" | "shared"`。下述左右侧、共享宽度和入口规则全部保留，旧 sidewalk/shared_way 输入仅作兼容。新的 C++ 可达街段及设施路线输出统一使用 walkway，Python 不能因此取消同侧接入检查；详见 [统一类型说明](../docs/unified-walkways.md)。

1. 路网节点、边和设施只向 C++ 传 `schemaVersion: 2` 的规范化 JSON；不要传百度原始响应。`originBd09`、`supportedCenterBoundsMeters`、`synthetic` 是 Python 的网络文件元数据，引擎忽略它们。
2. 普通道路两侧分别用 `sidewalk` 标注；经人工核实可自由穿行的步行街／小巷用一条 `shared_way`，填 `sharedWayType` 和 `widthMeters`。路口连接显式标注；普通道路过街只能用 `crossing`。同一普通道路两侧不要共用节点。
3. 新设施数据使用 `id`、`category`、`entrances`；每个入口有独立 ID、`accessEdgeId`、`streetAccessPointMeters`，若入口离街边还要提供经核实的 `accessPathMeters`。旧单入口字段只用于兼容既有样例。过街边可单独传 `waitSeconds`。统计 15 分钟覆盖只看 `facilityTravelTimes[*].reachable`／`travelTimeSeconds`，并使用 `bestEntranceId` 标示最短入口。
4. 画线使用 `reachableEdges`。画面优先使用 `displayGeometryMeters` 的 `MultiPolygon.coordinates`，逐点从局部米制转换为路网声明的坐标系，再构造最终 GeoJSON；外环和洞环都要转换。`displayPolygonMeters` 是同一数组的兼容字段。展示面不得用于设施可达性判断。合成模式可读取 `snappedOriginMeters`、`snapDistanceMeters`、`originAccessSeconds` 展示未核实的直线接入及耗时；不要把它当成已核实的真实步行路径。
5. 真实路网 JSON 必须给出 `publicWalkableAreasMeters`，使 Python 拒绝封闭地块内部选点；演示区内公共步行空间可任意选点。遇到道路侧不明确，API 请求可提供 `originEdgeId`。`serviceCategories` 逐类声明 `reviewed_online` 或 `incomplete`；后一状态不得声称灰区。C++ 的 `grayZones[*].uncoveredEdges` 是精确街段，`displayGeometryMeters` 仅近似画面。
5. 请求可能返回 `INVALID_INPUT`、`ORIGIN_NOT_ON_WALKWAY`、`AMBIGUOUS_ORIGIN_SIDE` 或 `ENGINE_ERROR`；文件缺失／超出已标注区域由 Python 返回 `UNSUPPORTED_AREA`。不要在道路侧不明确时自动选择另一侧。

## 建议的协作顺序

本轮直接复用现有建模文件，先检查“读文件 → 构造起点参数 → C++ → Python 转换”的结果是否一致，不需要等待或补充真实数据。15 分钟合成演示和 3 分钟局部实验共用 `SYNTHETIC_NETWORK_PATH`；只有明确要换独立局部图时才设置 `LOCAL_EXPERIMENT_NETWORK_PATH`。以后数据经核实后才能移除合成标记。修改契约时同步样例及相关测试。

测试文件仅保留本地，不随仓库发布；若本机已有测试，可运行 `pytest backend/tests` 做 Python↔C++ 往返验证（需先构建引擎）。CI 只检查 Debug/Release C++ 编译及健康输出、Python 语法、前端与镜像构建，不代替算法验收。真实路网到位后，最后确认引擎计算加进程启动不超过 Python 现有的 25 秒超时。

## 局部路网实验的独立对接

现成调用链为 `load_local_experiment_request → run_engine → build_local_experiment_result`（`backend/app/local_experiment.py`）。默认读取现有合成文件，无需另设路径。向 `POST /api/v1/local-experiments` 发送 `{"center":{"lng":121.504429458,"lat":31.331174183,"coordType":"wgs84ll"},"originEdgeId":"w:154811345:2:0"}`，随后轮询 `GET /api/v1/local-experiments/{analysisId}`。Python 固定 180 秒和严格步行边接入，不允许路外直线连接。当前文件没有设施、评价类别及已核实裁剪出口，因此 `categorySegments` 为空，`metadata.grayZoneStatus` 为 `data_insufficient`，警告为 `LOCAL_FACILITY_DATA_NOT_PROVIDED`、`LOCAL_BOUNDARY_NOT_MARKED`；只画 `reachableWalkways`，不能解释为空间匮乏。有类别数据的显式文件才按分类画线。主 `load_engine_request` 会拒绝带 `localExperiment` 的文件。

局部实验输入样例是 [engine-local-experiment.input.example.json](engine-local-experiment.input.example.json)。其中 `demoCenterBd09` 是供 Python 联调的提示坐标，约等于静态路网的局部 `(200,0)`；它不是 C++ 输入字段，引擎会忽略。不要直接用 `originBd09`（局部 `(0,0)`）发起该样例查询，因为它位于样例声明的起点范围外。不要通过当前固定 900 秒的主分析 `load_engine_request` 加载它；用独立入口读取局部 JSON、校验起点在该图可步行空间内，再设置 `localExperiment`。未传 `thresholdSeconds` 时引擎采用 180 秒，若显式传入其他正数，页面标题应同步显示实际时长。静态文件的所有裁剪出口都必须列入 `boundaryNodeIds`；未核对完整时把 `topologyStatus` 设为 `incomplete`。每类设施还需独立设置 `localInventoryStatus`，未核齐不能解释为设施缺乏。

局部模式只画 `result.localGrayZones[*].coveredEdges`、`candidateUncoveredEdges`、`unknownEdges` 三类街段，并展示 `result.diagnostics.warnings`。`LOCAL_REACHABILITY_MAY_BE_TRUNCATED` 表示起点在实验时长内可到图的裁剪出口。`result.grayZones` 在局部模式为空；不要回退读取该字段、不要把 `displayGeometryMeters` 的近似面作局部灰区，也不要将局部结果混入百度 API 主报告。
