# 十字路口专项校对

更新：2026-09-29。对象是专家模式的 `synthetic-preview.json`，不是快速模式原始路网，也不是现实横道合法性的验收。

## 第二轮：两处规则拒绝候选的结构修复（2026-09-29）

在 45 处待判断位置中修复 2 处，新增 3 条显式标注（`synthetic-preview.annotations.json`），均通过独立端口／拐角／旁路审计：

| 候选 | 问题 | 处理 |
| --- | --- | --- |
| `crossroad-33c7b590ab22` | 南向车道 `w:1272508511` 在覆盖边界内仅剩 0.2 米残段，接近边过短无法切端口 | `review-south-edge-three-arm`：东／西北／西三向端口模型；残段人行道随路口退役（界外不可达、无路由价值，退役依据写入标注 source） |
| `crossroad-37ec856feef0` | 相距 13.4 米的两个路口顶点被簇逻辑合并为五臂，接近方向分组互相矛盾 | 拆为 `review-east-offset-crossroad`（主体四向）与 `review-east-offset-alley`（干道×南侧巷道，共享步道单端口）；两路口共用路段两端各切端口，中间余段保留为人行道 |

2026-09-30 当前全图：**71 显式模型通过／36 共享通道通过／43 待人工判断**；10,614 节点／11,298 边／294 过街边／1434 转弯边；158 个连通分量（最大 9,497）；结构错误 0。相比 09-29，仅 `review-yinxing-guoquan` 试点路口关闭 2 个虚构内侧端口及其 2 条中线过街边；旧批次的验证记录仍保留在下方。[局部前后对比](divided-road-pilot.svg)。

验证与限制：

- 独立 Python Dijkstra（非项目转换代码、非 C++）13 个路由场景全部符合预期：同角转弯免费；换侧与穿路恰计一次 20 秒等待；移除全部过街边后上述场景不可达；巷口北侧人行道与南缘路口南侧人行道按连续侧免费直行；残段边已全部退役。
- 后端回归 409/410 通过。唯一失败 `test_engine_file.py::test_missing_file_and_bad_arguments_do_not_fall_back` 为**存量问题**：签入的 `cpp-engine/build/isochrone_engine.exe`（09-28 构建）把缺失的 `--network` 文件回退为读 stdin 并返回 `INVALID_INPUT`，而 `cpp-engine/src/main.cpp` 现行源码会返回 `INPUT_FILE_ERROR`（二进制落后于源码）。本环境无 CMake／MSVC 工具链，未重建；该用例不读取路网或标注，与本轮改动无关。引擎级过街等待用例（`test_divided_road_sections` 等）均以现有二进制通过。
- `test_divided_road_sections.py` 中硬编码的历史数量 `== 69` 改为与标注文件条目数一致（保留确定性断言意图）。
- 前端 135 项测试通过，`npm run build` 成功；既有大分块体积警告不变。
- 浏览器目视核对分图 04 面板 039（主体四向 + 东侧巷口双环）与分图 06 面板 071（三向紧凑模型）；13 页分图与全图审计 SVG 已重新生成并同步前端副本。
- 剩余 43 处待判断：38 处非标准四向（T 型／分叉）、3 处簇过大、2 处多条共享入口需重分组。需实地或更可信底图证据，未补任何连接。

### 离线发现的组件级碎片（只读核查，未连接）

源图（含历史 inferredJunction）共 72 个分量，其中 8 对不同分量端点间距 2.1–4.8 米，集中在两处：东南公园步道群（约 `x [854,962]、y [-968,-886]`，涉及 comp63/65/66/68/69/70 与主网）及主网边缘三个小碎片（约 `(205,-581)`、`(-1721,-57)`、`(-1984,1631)`）。OSM API 本轮不可达，无法核对原始节点共享关系；按规则不扩大吸附容差、不凭近距离补线。后续获得可信底图或 OSM 数据时优先核对这些位置。全部 160 个小引擎分量均对应单一源分量，转换层未引入断裂。

## 历史：首轮十字路口专项及双车道试点

后续局部改动见 [双车道外侧人行道试点](divided-road-pilot.md)：该轮当时为 10,614 节点／11,286 边，过街数量与下述路口状态不变。下方前后表保留本批路口校对的历史统计；SVG 和专项 JSON 报告已按最新图重新生成。

## 结果

完整扫描原图中 190 个度数为 4 的顶点，并整理成 150 处路口／疑似路口。双车道路口可能包含多个原始顶点，因此两种数量不能混用。

| 状态 | 数量 | 含义 |
| --- | ---: | --- |
| 显式路口规则通过 | 69 | 含此前 9 处及本轮新增 60 处；转弯、过街与端口连接通过检查，真实通行仍待核实 |
| 共享通道连接通过 | 36 | 四条共享通道在同一原图节点相接，不人为增加过街等待 |
| 需人工判断 | 45 | 复杂分叉、方向不清、较大路口、短支路或不匹配的车道；没有强行补线 |

本轮新增 60 处显式配置，其中包含 **240 条同角转弯、229 条过街连接**。同时扩展此前的南部斜交路口，从 8 端口／4 转弯／4 过街改为 9 端口／4 转弯／5 过街，补入漏掉的旧中心节点。

新增连接不是全图净增量：路口内部旧转弯、过街及原中心连接会被退役替换。

| 全图指标 | 本轮之前 | 本轮之后 |
| --- | ---: | ---: |
| 节点 | 10,464 | 10,610 |
| 边 | 10,975 | 11,284 |
| 过街边 | 88 | 287 |
| 连通分量 | 182 | 161 |
| 最大分量节点 | 8,945 | 9,481 |
| 未使用节点／结构错误 | 0 / 0 | 0 / 0 |

基线及数据摘要保存在 `synthetic-preview.annotations.json` 的 `crossroadReviewBaseline`；当前结构和完整清单见 [专项报告](synthetic-preview.crossroads-report.json)。连通分量下降不代表现实连接已经正确。

## 本轮修正的关键问题

1. 普通道路的四个方向拆为独立人行道端口，只有同一街角相邻的外侧端口使用 `turn`，仅计步行距离。
2. 横穿普通道路必须经过 `crossing`，计步行距离并额外等待 20 秒。双车道中间的人行道不能用作免费转角。
3. 双车道接入相对方向的一条共享通道时，中间端口必须由 `medianConnections` 显式标注带等待的接入；不自动猜测连接。
4. 一个双车道路口内部还存在度数为 2 的汇合节点。最初只检查四角连接时，它产生了绕过等待的旁路；现已将该节点纳入路口、退役内部旧边，并增加“接近路段端点汇合”的回归。
5. 结构校验的折线端点容差收紧为 `1e-6` 米，与 C++ `validate_graph` 保持一致，不放宽引擎校验。

路口发现只使用原图中**已经存在的连接边**。相近顶点不是自动连接证据；几何相交、桥下穿行或附近独立路径也不自动连通。度数为 2 的内部节点仅在其两个不同邻点都属于同一紧凑路口时纳入。

规则检查不仅数边，还独立重建方位及正确端口配对，检查重复连接、错误道路侧、遗漏接近方向、未退役中心节点、残留旧边、错误等待时间，以及通过完整接近路段绕过过街的旁路。

## 人工校对图

[完整道路／节点 SVG](synthetic-preview-audit.svg)保留悬停 ID。专项图每页 12 处，只显示道路和节点：蓝线为同角转弯，橙色虚线为带等待过街，空心点为独立端口；棕色虚圈标出待判断位置。

| 页面 | 序号 | 内容 |
| --- | --- | --- |
| [01](synthetic-preview-crossroads-01.svg) | 001–012 | 显式路口 |
| [02](synthetic-preview-crossroads-02.svg) | 013–024 | 显式路口；020 为补入内部汇合节点的位置 |
| [03](synthetic-preview-crossroads-03.svg) | 025–036 | 显式路口 |
| [04](synthetic-preview-crossroads-04.svg) | 037–048 | 显式路口；037 为扩展后的南部斜交路口 |
| [05](synthetic-preview-crossroads-05.svg) | 049–060 | 显式路口 |
| [06](synthetic-preview-crossroads-06.svg) | 061–072 | 显式路口结束，待判断位置开始 |
| [07](synthetic-preview-crossroads-07.svg) | 073–084 | 待判断位置 |
| [08](synthetic-preview-crossroads-08.svg) | 085–096 | 待判断位置 |
| [09](synthetic-preview-crossroads-09.svg) | 097–108 | 待判断位置 |
| [10](synthetic-preview-crossroads-10.svg) | 109–120 | 待判断位置结束，共享通道开始 |
| [11](synthetic-preview-crossroads-11.svg) | 121–132 | 共享通道 |
| [12](synthetic-preview-crossroads-12.svg) | 133–144 | 共享通道 |
| [13](synthetic-preview-crossroads-13.svg) | 145–150 | 共享通道 |

在 Vite 中打开 `/walking-crossroads-01.svg` 至 `/walking-crossroads-13.svg`；完整图为 `/walking-network-review.svg`。上一批 8 处的图 `/walking-network-junctions.svg` 也已更新，但只保留那一批位置，不替代完整清单。

候选 ID 根据原图节点集合生成；扩展路口时旧标注 ID 保留，因此候选 ID 可能不同于标注 ID。报告中的 `modelIds` 给出对应关系。例如第 020 处候选 `crossroad-1f1e0be17b58` 对应标注 `crossroad-fd57472cfc86`。

45 处待判断位置中：38 处方向不明确或并非标准四向；3 处范围过大；2 处同方向有多条共享入口；1 处中间车道人行道不匹配；1 处接近路段过短。它们不是“45 处确认缺横道”。

## 验证与耗时

- Debug：274 项路口／连接／转换相关测试通过。
- Release：完整后端 395 项测试通过，包含上述相关测试、Python→C++→GeoJSON 及 API 入口。
- Debug、Release 各 7 个 C++ 测试程序全部通过；全图文件验证使用 Release 下仍有效的 `TEST_CHECK`。
- 每个新增路口都验证局部有／无过街边的路线，并在完整图中将设施入口耗时与独立 Python Dijkstra 对照。
- 浏览器目视检查了前 6 页（覆盖全部 69 处显式模型），SVG 副本与生成文件一致。未修改前端应用代码；本轮未重新运行前端构建。
- 仍有两条第三方依赖弃用警告，本轮未处理。

首次并发 Release 回归曾出现一次 `INVALID_INPUT: edge path does not match its nodes`。同参数单测、完整回归及随后连续 10 次单项复测均通过，目前未稳定复现，不能断言根因已定位；没有通过自动重试或放宽 C++ 校验掩盖该异常。

完整 900 秒计算，对三个选区内起点各重复两次，使用 10,610 节点／11,284 边：

| 构建 | 中位数 | 最大值 |
| --- | ---: | ---: |
| MSVC Release | 0.295 秒 | 0.304 秒 |
| MSVC Debug | 2.031 秒 | 2.090 秒 |

均小于现有 25 秒调用上限。耗时包含 JSON 编码、子进程启动、C++ 计算与结果解析，不含 HTTP、在线设施采集和浏览器绘制；新旧可达区域不同，不据此宣称算法加速。

测试 XML、性能 JSON 和浏览器截图保存在本机 `C:/Users/ROG/Documents/Codex/2026-09-22/new-chat-2/outputs/road-refinement-20260929/`，文件名前缀为 `crossroad-`。使用已安装的 MSVC；C++ 核心源码本轮未变更，未覆盖正在运行的仓库引擎二进制。

## 重现与后续维护

在仓库根目录执行；修改标注后必须重新生成数据，不能只画 SVG：

```powershell
.\.venv\Scripts\python.exe backend/scripts/export_synthetic_preview.py
.\.venv\Scripts\python.exe backend/scripts/render_network_audit.py
.\.venv\Scripts\python.exe backend/scripts/audit_walking_network.py
.\.venv\Scripts\python.exe backend/scripts/audit_crossroads.py --sheets
Copy-Item -LiteralPath data/networks/synthetic-preview-audit.svg -Destination frontend/public/walking-network-review.svg
Copy-Item -LiteralPath data/networks/synthetic-preview-junction-review.svg -Destination frontend/public/walking-network-junctions.svg
$taskSheets = (Get-Content -Raw data/networks/synthetic-preview.crossroads-report.json | ConvertFrom-Json).reviewSheets
foreach ($taskSheet in $taskSheets) {
    $taskPage = $taskSheet -replace '^synthetic-preview-crossroads-', 'walking-crossroads-'
    Copy-Item -LiteralPath (Join-Path 'data/networks' $taskSheet) -Destination (Join-Path 'frontend/public' $taskPage)
}
```

`audit_crossroads.py` 只输出清单和修复建议，不改权威标注。`prepare_crossroad_repairs.py` 只做干跑并输出 `.proposed.json`；人工审阅后才可替换 `synthetic-preview.annotations.json`。当前没有待自动套用的建议。

设置 `CPP_ENGINE_PATH` 指向对应构建的实际引擎，再运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_crossroad_audit.py backend/tests/test_reviewed_junctions.py backend/tests/test_junction_annotations.py backend/tests/test_crossing_annotations.py backend/tests/test_synthetic_converter.py backend/tests/test_network_audit.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
```

下一步优先从第 06–10 页的45处复杂位置开始，结合更可信的横道、桥梁层级、隔离带、闸门和共享入口信息逐处判断。当前数据缺少这些标签，不能仅凭底图或“四条边相接”认定现实可通行。

本轮没有提交或推送 Git；保留快速模式原始图与其他未提交改动。
