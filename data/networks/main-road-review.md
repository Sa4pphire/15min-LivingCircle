# 主干道校对记录 · 2026-10-01

本批使用 `road-network-refiner` 的局部显式校对流程，以本地道路几何、已有道路／节点 SVG 和既有标注为依据。**全部仍为未实地核实的合成模型**；本轮没有在线百度底图对齐验收，不证明人行道、斑马线或地块入口真实可通行。

## 单一修正来源

只在 [`synthetic-preview.annotations.json`](synthetic-preview.annotations.json) 维护修正规则，再重新生成路网。不要手改导出的 `synthetic-preview.json`。

| 字段 | 用途与坐标 |
| --- | --- |
| `junctions` | 显式街角、转弯和过街端口；引擎米制坐标，Y 向北 |
| `crossings` | 既有人工标注的过街连接；引擎米制坐标，Y 向北 |
| `dividedRoadSections` | 双车道配对、外侧方向、限定修正框；`preview-local-v1`，Y 向南 |
| `connections` | 指定共享步道端点、目标边及接入点的既有补连；引擎米制坐标，Y 向北 |
| `mainRoadReview` | 本批来源、范围和待核查事项；不直接改变通行关系 |

根级 `coordinateSystem` 是 `engine-local-meters`，但双车道范围沿用前端坐标；由 `dividedRoadSectionsCoordinateSystem: "preview-local-v1"` 单独声明。不要混用两个 Y 方向。左右侧相对于该边 `from → to` 方向，不是地图屏幕的左右。

前端生成脚本和 Python 导出／审计脚本现在直接读取统一文件。前端 `demoRoadGraph.local.json` 中的标注元数据只是生成副本，旧 `demoSidewalkSections.local.json` 不再被这些脚本读取，暂保留为历史资料。恢复了此前缺失的路口展开及双车道应用链路，避免“改了标注但导出未生效”。

## 本批修改

保留原有 `maoting-divided-road-pilot`；新增以下 6 个**直线路段的局部范围**，移除范围内的中央纵向人行道，保留外侧人行道、原有转弯及过街连接：

| 道路 | 标注 ID | 新移除中央伪步道长度 |
| --- | --- | ---: |
| 国权北路北段 | `main-guoquan-north-straight` | 100.127 米 |
| 国权北路南段 | `main-guoquan-south-straight` | 70.002 米 |
| 国权北路下段 | `main-guoquan-lower-straight` | 320.016 米 |
| 殷行路西段 | `main-yinxing-west-straight` | 180.001 米 |
| 殷行路东段 | `main-yinxing-east-straight` | 240.005 米 |
| 殷高东路东段 | `main-yingao-east-straight` | 110.034 米 |
| 合计 | 本批新增范围，不含旧试点 | **1,020.185 米** |

新规则使用 `geometry_inferred_unverified`，没有将推断标为人工核实。只裁去框内线段，不跨越已连接端点推测路口。框外中央残段继续保留并标为待核查；这可能增加图分量，不能为了降低分量数而把它们自动连回道路另一侧。

两处已有用户授权的红点修正从导出图迁入 `connections`，仍是 `user_marked_unverified`：

- `marked-west-path-gap`：共享步道间约 0.165 米补连。
- `marked-east-path-gap`：共享步道间约 8.144 米补连。

这两处保持原 ID、几何及接入位置。补连仅允许指定 `shared_way` 之间的连接，目标边在明确的内部接入点拆分；不寻找最近道路，不允许用该规则直接连接普通道路两侧人行道。

## 验证与结果

- 导出图：10,640 个节点、11,314 条边；结构错误 0、孤立节点 0。
- 72 处现有显式路口的规则检查全部通过；未新增过街连接，等待规则保持原样。
- 本批基线与结果的连通分量数为 158 → 167。新增分量是局部切断后保留的框外中央残段，**不是确认新增了真实断头路**。
- 全图路口审计仍有 43 处需要人工判断，不把规则检查通过当成现场真实性验证。
- 当前 C++ 源码重新编译，Debug（`-O0 -g`）和 Release（`-O2 -DNDEBUG`）分别通过 315 项相关 Python／C++ 契约与路由回归，均无跳过项。新路段逐一验证：没有过街边时不能换侧；加入显式过街边后耗时为距离／1.3 + 20 秒，使用独立公式核对。
- 前端路网生成与局部规则测试 19 项通过；没有运行无关的 UI 测试或前端整站构建。

本机编译器使用 `g++ -std=c++2a`（其 C++20 模式名称）。本次测试二进制分别位于 `cpp-engine/build/main-road-debug/isochrone_engine.exe` 和 `cpp-engine/build/main-road-release/isochrone_engine.exe`。

本批规则作用于读取 `synthetic-preview.json` 的 C++ 合成／局部实验模式；**不会改变百度在线采样路线的结果**。前端源底图几何未被改写，勿将标注元数据更新理解为所有模式都应用了人行道修正。

## 复现与查看

从仓库根目录依次运行（Python 使用项目虚拟环境）：

```powershell
node frontend/scripts/build-demo-road-graph.mjs --write
.venv/Scripts/python.exe backend/scripts/export_synthetic_preview.py
.venv/Scripts/python.exe backend/scripts/audit_walking_network.py
.venv/Scripts/python.exe backend/scripts/audit_crossroads.py --sheets
.venv/Scripts/python.exe backend/scripts/render_network_audit.py
.venv/Scripts/python.exe backend/scripts/render_main_road_review.py
```

局部前后对比图为 `data/networks/main-road-review.svg`，同一内容复制到 `frontend/public/walking-main-road-review.svg`；统计与路口检查为 `synthetic-preview.main-road-report.json`。这些生成的 SVG／报告不参与计算，不作为修正源。对比图绿色为保留外侧，橙色为修改前中央线，灰色虚线为未修改的框外残段。

继续校对时，优先查看国权北路、殷行路的弯道／汇入口及框边残段，再核对国帆路、江湾城路是否存在真实车道配对。疑似辅路不能只凭距离近就当成另一条主路车道；复杂路口和合法过街位置需要截图或现场证据，再写明确端口规则。
