# 百度 API 协作交接：当前前端／C++ 工作所需的 Python 部分

> 给负责 Python 后端的协作者。我的工作是地图页面、交互／路线动画和 C++ 步行算法；请你接手其中**必须依赖百度 Web API 的调用与数据处理**。这不是让你重做前端、C++ 算法，或独自完成整片真实路网的人工标注。

## 我们目前的接缝

缓存与 POI 新增对接：已定向整合 GitHub `5be3f6a` 的 `cache.py`、`poi.py` 和缓存测试。演示不再写独立的规范化响应缓存，统一通过 `BaiduClient` 共享 JSON 响应；保留并发合并、错误冷却及旧缓存兼容。请优先复用 [POI 接入与共享缓存说明](baidu-poi-integration.md) 中的 `PoiService`／HTTP 入口，双方统一缓存路径、关键词和查询策略。下文的人工入口核实要求仍适用；在线候选不是已核实覆盖。

- 前端已能显示百度底图，并在四路围合的演示区内选择起点。起点范围只限制“从哪里出发”；15 分钟路线和设施查询必须允许延伸到围合区外。
- 目前“真实区域”页面点击分析仍调用 frontend/src/analysisClient.js 的本地固定圆／预览路网逻辑；frontend/src/cppAnalysisClient.js 调用的是独立的合成 C++ 演示接口。两者都不能被称为真实计算。
- FastAPI 已有 POST /api/v1/analyses、GET /api/v1/analyses/{analysisId}，也有 backend/app/baidu/client.py（坐标转换、步行 RouteMatrix、地点检索的基础实现）。真实路网文件尚未就位，正式分析现在应返回 UNSUPPORTED_AREA。
- C++ 引擎只接受 schemaVersion: 2 的规范化步行图并输出局部米制结果；百度原始 JSON 不进入 C++。现有字段参看 contracts/python-handoff.md 与 contracts/engine-v2.README.md。

## 请你负责的百度 API 工作

### 1. 服务端 AK 与调用可靠性（先做）

你负责 BAIDU_SERVER_AK 的后端配置、服务权限和实际连通性检查。浏览器底图使用的 VITE_BAIDU_BROWSER_AK 与服务端 AK 分开；浏览器 AK 和地图视觉效果由我维护，你**不需要修改前端地图组件**。不要在 Git、前端构建产物或日志中暴露服务端 AK。

在现有 BaiduClient 上补齐需要的分页／批量拆分、限流、明确超时、有限重试和错误分类。鉴权或参数错误不重复请求；配额不足应返回可识别原因，不假装分析成功。缓存是否以及如何保存百度返回内容，先核对服务条款与配额要求。已有的 mock 测试继续扩充，不让 CI 依赖真实 AK。

交付：可复现的本地配置说明、无密钥烟测记录、mock 测试。backend/app/settings.py 已有 BAIDU_SERVER_AK 等配置入口；docs/baidu-api-smoke-test.md 有之前的连通记录。

### 2. 坐标统一（前端与 C++ 的关键接缝）

百度底图准备好时，我的前端选点会给后端 **BD-09 经纬度**，经纬度顺序在 JSON 中明确为 lng、lat。请让 Python 用该点调用现有路网加载／转换链路，生成 C++ 所需的局部米制 originMeters；引擎返回的街段与多边形再转回 BD-09 GeoJSON 给前端。

如果基础路网或设施来源是 WGS-84／GCJ-02，请在 Python 数据整理阶段统一转换并记录原始坐标系，禁止直接把不同坐标系的数值叠加。无百度底图时页面有 WGS-84 的 SVG 预览；它暂时仍是合成预览，不要静默当作 BD-09 的真实分析请求。[百度坐标转换文档](https://lbsyun.baidu.com/docs/webapi?title=geoconv%2Fguide%2Fchangeposition-base)

交付：一个可供我调用的 BD-09 分析请求样例，以及一个转换后的结果样例；测试至少覆盖经纬度顺序、不同坐标系拒绝混用和投影往返误差。

### 3. 民生设施候选数据（百度 API 的主要业务产出）

按双方约定的服务类别调用百度地点检索，覆盖演示区及可能被 15 分钟步行到达的外围；分页、UID 去重、过滤明显无关结果，输出统一的候选设施清单：ID／UID、名称、类别、BD-09 坐标、地址、来源与查询时间。[百度地点检索文档](https://lbsyun.baidu.com/docs/webapi?title=placev3%2Fguide%2Fwebservice-placeapiV3%2FinterfaceDocumentV2)

**POI 坐标不是出入口。** 对需要进入 C++ 设施耗时计算的设施，必须另外确定 entrances 中的 accessEdgeId、streetAccessPointMeters；非街边入口还需要可信的 accessPathMeters。百度检索不能证明这条路径可通行。入口或类别清单未核齐时，将对应 serviceCategories.dataStatus 保持为 incomplete，报告应显示“数据不足”，不能给出“该类设施匮乏”的真实结论。入口核实由我们共同确认，不要求你仅靠百度 API 自动推断。

交付：候选 POI 的规范化输出、去重／分类测试；核实过的设施可进入真实路网 JSON，未核实的保持候选状态。不要把候选结果直接伪装成可达设施。

### 4. 步行路线 API 只作校验参考（次优先）

针对我们挑出的争议路口、过街点和设施入口，可调用单条步行规划查看参考路径；用 RouteMatrix 对少量起终点比较距离／耗时，输出“百度参考值 vs C++ 结果”的差异记录，供我们排查路网连接。RouteMatrix 只返回两点间距离和时间，不提供完整双侧人行道拓扑；不能用它替换 C++ 的 Dijkstra，也不能根据路线折线自动断言整片区域可通行。[步行规划](https://lbsyun.baidu.com/docs/webapi?title=directionv2%2Fwebservice-direction%2Fwalking)、[步行批量算路](https://lbsyun.baidu.com/docs/webapi?title=routematrix%2Froutchtout-walk)

交付：少量可复现的对照案例和差异说明，而不是另起一套等时圈算法。调用量与批量上限按当前百度控制台和官方文档核对。

## 请保持的后端对接接口

优先复用现有接口，不另造一套前端专用格式：

~~~text
POST /api/v1/analyses
{
  "center": {"lng": 121.510000, "lat": 31.337000, "coordType": "bd09ll"},
  "minutes": 15
}
→ 202 {"analysisId": "...", "status": "queued"}
GET /api/v1/analyses/{analysisId}
→ status / progress / result / error
~~~

成功结果按 contracts/analysis-result.example.json 返回：reachableWalkways 用于我的路线描绘动画，isochrone 是近似展示面，facilities、blindZones／blindZoneWalkways、metrics、warnings、metadata 用于报告。设施可达与灰区只能依据 C++ 路网耗时，不依据展示面覆盖。若网络数据缺失、起点不在已核实公共步行空间或无法可靠接入，保持明确错误；不要返回固定圆作为“真实结果”。

请给我一份**不带 AK、可直接用于前端联调**的完整请求与响应样例，并在字段或错误码变化前告知我。前端把本地固定圆切换为正式接口、以及路线／地图动画，由我来实现；C++ 图类型、寻路与性能由我维护。

## 不在本次交接范围内

- 百度 JavaScript 底图加载、缩放、拖动、页面美术、图层和动画。
- C++ 寻路算法、过街等待、共享通道、灰区计算的改写。
- 仅凭百度接口自动生成完整且可信的双侧人行道图。真实路网的来源、授权与关键连接核实需要单独协作；如果基础图尚未交付，你的 API 部分仍可通过 mock 和候选数据先完成，但正式分析继续标为未支持。
- 批量抓取或持久重发布未经许可的百度地图数据；使用方式先核对[百度开放平台服务协议](https://lbsyun.baidu.com/docs/pcsa?title=law%2Fopen%2Flaw)。

## 我们的联调验收

1. 不配置真实 AK 时，后端百度客户端测试能通过，且不会泄漏密钥。
2. 配置服务端 AK 后，地点检索和必要的步行／坐标接口通过一次脱敏烟测；失败时能区分鉴权、配额、网络与参数问题。
3. 我提供一个 BD-09 选点，Python 能给出 C++ 输入；有已核实路网时能返回可解析的 BD-09 GeoJSON，前端可分别绘制可达街段和近似面。
4. 设施候选、已核实入口、数据不足类别严格区分；无真实路网时不出现“真实等时圈”或“真实灰区”。
5. 用一个靠近普通道路、一个靠近共享通道、一个不可接入地块的点共同验收，再核对围合区外的可达部分。

建议先交付**接口样例 + 一个小范围设施候选集 + API 错误处理测试**，我就能开始替换前端的本地固定圆适配层；完整真实路网和全部类别随后并行推进。
