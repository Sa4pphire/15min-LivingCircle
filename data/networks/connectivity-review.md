# 路网连接精细化核查记录

更新：2026-09-30。本记录针对由快速／专家模式同一份源路网转换出的 `synthetic-preview.json`；快速模式展示原始道路图，专家模式使用 C++ 步行图。数据仍是**合成、待核实路网**，不是现实过街合法性的证明。

## 最新进展：十字路口专项校对

后续已新增 60 处四向模型，并扩展南部旧路口、修复内部汇合造成的免费过街旁路；2026-09-29 第二轮又把 2 处规则拒绝候选修复为 3 条显式标注（见 [十字路口专项记录·第二轮](crossroads-review.md)）。2026-09-30 将双车道外侧人行道试点接入其北端路口，并关闭两条错误的中线步行入口及对应连接。当前为 10,614 节点／11,298 边／294 过街边／158 连通分量；完整扫描的 150 处候选中，71 处显式模型通过、36 处共享连接通过、43 处仍需人工判断。完整结果及 13 页 SVG 索引见 [十字路口专项记录](crossroads-review.md)；最新局部改动与验证见 [双车道试点](divided-road-pilot.md)。

下面的 8 处及相关统计保留为上一批历史记录，不代表当前全图数量。当前 `review-south-central` 已改为 9 端口／4 转弯／5 过街，`review-yinxing-guoquan` 已改为 14 端口／4 转弯／6 过街；其余历史位置标注保持不变。

## 上一批处理结果（历史）

保留此前两处人工标记过街及 `blue-crossroads-01` 路口，新增以下 8 处显式路口配置。位置名称用于校对导航，不能据此认定横道位置已经核实。

| 标注 ID | 位置说明 | 独立端口 | 转弯边 | 过街边 |
| --- | --- | ---: | ---: | ---: |
| review-guofan-guoxiu | 北部国帆路沿线四向路口 | 8 | 4 | 4 |
| review-yinxing-guoquan | 国权北路／殷行路双车道路口 | 16 | 4 | 8 |
| review-yingao-guoquan | 国权北路／殷高东路双车道路口 | 16 | 4 | 8 |
| review-south-central | 南部斜交路口 | 8 | 4 | 4 |
| review-southeast-outer | 东南外围斜交路口一 | 8 | 4 | 4 |
| review-southeast-outer-2 | 东南外围斜交路口二 | 8 | 4 | 4 |
| review-north-outer | 北部外围四向路口 | 8 | 4 | 4 |
| review-central-shared | 中部共享支路接入路口 | 7 | 4 | 3 |

共新增 **32 条转弯边、39 条过街边**。旧路口内部连接也被替换，因此全图净增加 58 条边，不是 71 条。

| 结构指标 | 处理前 | 处理后 |
| --- | ---: | ---: |
| 节点数 | 10,438 | 10,464 |
| 边数 | 10,917 | 10,975 |
| 过街边数 | 49 | 88 |
| 连通分量 | 188 | 182 |
| 最大连通分量节点数 | 8,879 | 8,945 |

完整图通过唯一 ID、有限坐标、合法端点、折线端点对齐、非零长度及过街等待时间检查，无未使用节点。连通分量减少并不等于现实路网正确性已验收。

## 连接规则

- 标注显式指定原图节点和各接近方向的边；程序只展开这些已选位置，不根据几何相交、坐标靠近自动补连接。
- 路口各方向的人行道切回独立端口，仅通过显式连接通行。旧中心节点的内部连接被移除，防止产生隐藏的免费跨街捷径。
- 同一街角只连接外侧人行道，`turn` 仅计步行距离；`crossing` 计步行距离并加 20 秒等待。
- 双车道的中间人行道跨越侧向道路时同样使用 `crossing`，不能充当免费转角。
- 共享支路可接入同侧街角，但不能因此免费横穿普通道路。
- 遗漏接近方向、重复选边、相反方向合组、过短路段等错误会中止导出，不静默生成连接。

新增标注均保留 `geometry_inferred_unverified`。已查看当前百度底图总览和更新后的完整／局部 SVG，但**没有逐个证实合法人行横道、桥梁层级、闸门开放与隔离带**。原图缺少相应标签，需要进一步人工核查。

## 校对入口

- `synthetic-preview-audit.svg`：完整道路与节点图。空心橙点为断头节点，蓝色为显式标注连接及端口。悬停可查看节点或边 ID。
- `synthetic-preview-junction-review.svg`：本批 8 处路口分图。蓝线为无等待转弯，橙色虚线为带等待过街；线段相交但没有同一节点仍不连通。
- `synthetic-preview.connectivity-report.json`：前后结构统计、路口清单及剩余候选顶点。

Vite 启动后，可打开 `/walking-network-review.svg` 和 `/walking-network-junctions.svg`。它们不叠加 POI、建筑或等时圈，便于人工校对。

原图发现 303 个主路分岔候选**顶点**，其中 288 个仍需核查。同一真实路口可能对应多个顶点；这些数字不是独立路口或缺失横道数量。剩余分量和断头点也可能是合法死路、私有路径或数据缺失，不应为降低数量而批量连接。

## 验证

- 新旧连接相关回归：Debug、Release 各 **75 项通过**。覆盖旋转四向路口、共享 T 型支路、不同道路侧、双车道中间端口和错误标注拒绝。
- 每个新增路口的局部图及完整图抽查：C++ 耗时结果与独立 Python Dijkstra 对照；移除过街边后不能通过同角转弯绕过等待。
- 7 个 C++ 测试程序在 Debug、Release 下各全部通过，使用 Release 下仍有效的 `TEST_CHECK`；两个引擎健康检查均成功。
- 完整后端回归（Release 引擎）：**164 项通过**，包含文件／标准输入、Python→C++→GeoJSON、API 演示入口等检查。两条第三方依赖弃用警告未处理。
- 前端：**87 项通过**，`npm run build` 成功。仍存在已有的大型路网数据分块体积警告，本轮不改变数据加载策略。

旧 MinGW 8.1 在 Debug 编译时发生内部崩溃，后改用本机已有 VS 2022 C++20 工具链编译验证，未下载新工具、未覆盖当前运行的引擎二进制。

### 完整路网性能抽测

对三个选区内起点（包含离路中心点）各运行两次 900 秒计算，每种配置共 6 次，完整使用 10,464 节点／10,975 边。

| 构建 | 中位耗时 | 最大耗时 |
| --- | ---: | ---: |
| MSVC Debug | 3.050 秒 | 3.383 秒 |
| MSVC Release | 0.435 秒 | 0.469 秒 |

这是本机观察值，计入 Python JSON 编码、进程启动、C++ 计算和输出解析；不含 HTTP、在线 POI 采集及浏览器绘制，不是负载或真实精度验收。所有抽测均低于 25 秒调用上限。

## 重现命令

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe backend/scripts/export_synthetic_preview.py
.\.venv\Scripts\python.exe backend/scripts/render_network_audit.py
.\.venv\Scripts\python.exe backend/scripts/audit_walking_network.py
Copy-Item -LiteralPath data/networks/synthetic-preview-audit.svg -Destination frontend/public/walking-network-review.svg
Copy-Item -LiteralPath data/networks/synthetic-preview-junction-review.svg -Destination frontend/public/walking-network-junctions.svg
```

设置 `CPP_ENGINE_PATH` 为实际已编译引擎后运行回归；Debug 和 Release 分别指定相应二进制：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_reviewed_junctions.py backend/tests/test_network_audit.py backend/tests/test_junction_annotations.py backend/tests/test_crossing_annotations.py backend/tests/test_synthetic_converter.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe backend/scripts/benchmark_refined_network.py --engine $env:CPP_ENGINE_PATH --output outputs/road-performance.json --label release
```

## 下一轮优先核查

先人工审阅 8 处局部图中的过街位置、双车道隔离带及共享支路入口，再按常用演示起点的可达路径筛选剩余候选。用百度底图辅助定位，但横道、跨桥或封闭地块连接需更可信证据；无法确认时保持待核实或不补连接。修改标注后必须重新导出并回归，不能仅改 SVG。
