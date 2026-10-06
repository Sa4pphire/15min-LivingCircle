# 国泓路周边源拓扑校对（2026-10-05）

本批修正共同源图的通行权限和简化丢失的连接；不修改地图 UI、C++ 算法或等时圈面逻辑，不宣称整个路网已经实地核实。

## 证据与范围

原始数据：[OSM map API 局部快照](https://api.openstreetmap.org/api/0.6/map?bbox=121.501,31.324,121.506,31.328)，79 条 highway way。许可 ODbL 1.0。源节点 ID、way 版本、标签、查询范围和核查状态保存在 `synthetic-preview.annotations.json` 的 `sourceTopology`；无需提交 SVG 或另存一份完整 OSM 下载文件。

恢复连接范围为 `preview-local-v1` 米制坐标 X `[-450, 200]`、向南 Y `[500, 1050]`。权限过滤作用于该快照覆盖的 way；其余区域仍沿用原来的未核实源图。

- `226154034` 是 `foot=yes` 人行道。
- `226161178`、`226161184` 等 22 条 way 是 `foot=no` 自行车道。它们与人行道相近不是步行连接证据；不能把此前使用这些边的短路线作为正确步行路线。
- `225964869`、`226000721` 是原始数据明确允许步行的短连接，被原 SVG 导出的 12 米过滤条件丢弃。本批按原始几何恢复。
- 68 个节点有至少两条允许步行 way 的共享源节点 ID。恢复其中 26 个简化丢失的连接，保留 26 个已有连接；16 个列为待核查，不自动接通。其中 14 个涉及主干道端口，2 个涉及已有人工标注的边。

这些是在线源数据证据，并非现场调查。`online_source_unverified_on_site` 状态继续保留。

## 生成链与规则

`demoContext.extended.wgs84.json` → `prepareTopologyFeatures` → `buildDemoRoadGraph` → `restoreSourceTopology` → 原有推定修复和双车道标注 → `demoRoadGraph.local.json` → Python 转换 → `synthetic-preview.json`。

- 优先遵守 `foot` / `access` 权限；自行车道必须有显式步行许可才保留。Python 转换器独立拒绝携带禁止步行标签的源边。
- 只有两条 way 的原始节点列表都含同一 node ID，才恢复连接。投影距离 2.1 米只是核对已简化几何的容差，不是最近邻补路半径。
- 新节点保留 `sourceNodeId`；拆边 ID 为原边 ID 加 `:osm:<序号>`，并保留 `originalEdgeId`，确保重生成确定。
- 主干道中心线不自动接入：仍需明确的 sidewalk 端口、turn 和 crossing。已被人工标注引用的边不静默拆分。
- 保留原有 294 条过街边及其等待规则。不新增免费跨街捷径。
- 快速与专家模式消费同一份修正源图；快速模式的展示算法不因此变成精确双侧人行道路由。

## 复现与验收

从仓库根目录执行：

```powershell
Push-Location frontend
node scripts/build-demo-road-graph.mjs --write
node --test tests/roadGraph.test.mjs tests/roadGraphTopology.test.mjs tests/roadGraphSections.test.mjs
Pop-Location
.\.venv\Scripts\python.exe backend/scripts/export_synthetic_preview.py
$env:CPP_ENGINE_PATH = "$PWD/cpp-engine/build/poi-route/Release/isochrone_engine.exe"
.\.venv\Scripts\python.exe -m pytest backend/tests/test_source_topology.py backend/tests/test_synthetic_converter.py backend/tests/test_poi_routes.py backend/tests/test_crossroad_audit.py backend/tests/test_reviewed_junctions.py backend/tests/test_divided_road_sections.py backend/tests/test_crossing_annotations.py backend/tests/test_junction_annotations.py backend/tests/test_network_audit.py -q
```

换为 `Debug/isochrone_engine.exe` 后重复后端回归。二进制必须实际存在，跳过不能视为算法验收通过。测试包括独立 Dijkstra 总耗时对照、禁止步行边过滤、共享源节点恢复，以及原有过街／转弯／双车道语义。

本批实际验收结果：Debug、Release 各 314 项后端回归通过、无跳过；24 项前端路网测试通过，`npm run build` 成功；全图 10,521 节点、11,187 边、无结构错误和孤立节点，72 处已有显式路口的语义检查均通过。重新生成的源图与保存数据一致，C++ 输入重生成后 SHA-256 一致。删除禁止步行的边后弱连通分量从 167 增至 170，这是禁止非法连通的结果，不能以减少分量数为优化目标。

前端全量测试另外有 13 项界面测试失败：引用了当前组件已经没有的 `setMapZoomTier`、`selectRegionCenter` 等状态／方法。本批不修改这些未提交的界面代码，不将全量前端回归标为通过。

固定回归起点 `[-560, -465]`、设施 `[-144.85437952229267, -815.352624681374]`（引擎向北 Y 正）：修正路网后约 1145 米、901 秒，含一次 20 秒等待。它略超过 900 秒，不应标为 15 分钟内可达；设施末段仍是既有的“未核实直线接入”演示规则，不能声称实际入口已核实。

待核查节点的 ID、关联 way 和原因可从生成数据的 `sourceTopology.junctions` 中筛选 `status == "pending"`。局部 SVG 前后图仅作本地校对附件，不属于运行所需数据。后端已有分析上下文可能保留旧图，修改后应重新启动后端并重新生成分析。
