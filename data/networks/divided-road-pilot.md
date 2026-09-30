# 双车道外侧人行道局部试点

更新：2026-09-30。殷行路南侧／城投茂庭附近的局部试点已向北延伸约 20 米，直至国权北路／殷行路路口的南侧端口。位置名用于导航；仍属合成、待实地核实数据，不作为现实通行合法性的证明。

## 本次改动

- 普通双车道被分别向两侧偏移时，会错误地产生四条人行道。试点内移除两条面向中央车道的纵向 `sidewalk`，只保留道路外围两条；每条仍可双向步行。
- 两条车道的原始方向相反，不能统一删除同名 `left`／`right`。配置明确指定每条车道的外侧，并检查其确实背离另一条车道。
- 只移除框内部分，框外片段保留。切断位置不会新建横穿连接；换侧仍须走原有显式 `crossing`，计入步行距离和等待。
- 北端路口 `review-yinxing-guoquan` 的 `south-0`、`south-2` 原是内侧人行道入口，现显式撤销，同时撤销与其相连的两条 `median-south-north-*` 过街边。保留 `south-1`、`south-3` 两条外侧入口、同角转弯及 `cross-south` 的一次 20 秒过街等待。北臂两个原中线端口在该路口成为断头端口，不擅自新增斜穿连接；北侧道路仍需后续实地核验。
- 未修改 C++ 寻路代码、前端布局及快速模式动画。快速／专家模式继续使用同一份源路网；规则写在共同源图的 `dividedRoadSections` 中，由 Python 转换器生成专家模式的步行图。快速模式现有两条车道示意不是精确人行道几何。

范围（`preview-local-v1`，向东 X 正、向南 Y 正）：`X [-300, -240]`、`Y [260, 420]`。

| 原始车道 ID | 保留的外侧 |
| --- | --- |
| `w:132863832:0:0` | `right` |
| `w:226161205:0:0` | `right` |

这是显式局部试点，**没有根据距离批量合并全图的平行道路**。仅这一个路口在 `junctionApproachClosures` 中列出要关闭的两个端口和两条中线连接；转换器要求逐项匹配，若还有未列出的连接或端口不在试点范围便拒绝导出，不静默丢弃分支或过街边。

## 数据与校对入口

- 试点配置：`frontend/src/data/demoSidewalkSections.local.json`。
- 共同源图仍为 8,518 节点／9,232 边；只增加规则元数据，原节点、原边和 ID 不变。
- 导出的 v2 图：10,614 节点／11,298 边，294 条过街边；72 处显式路口规则均通过，路网审计无结构错误。
- 除上述两条已列明的中线过街边外，其余过街、转弯和共享通道边内容未改。两种前端模式仍取同一份源图。
- [局部前后对比 SVG](divided-road-pilot.svg)：左图旧四条，右图两条外侧；仅显示指定车道对应的人行道，不包含旁侧原有共享步道。悬停可查看边 ID。
- Vite 启动后打开 `/walking-divided-road-pilot.svg`；全图校对仍使用 `/walking-network-review.svg`。

## 验证

- 2026-09-30 完整后端 416 项通过，针对改变后的路由分别使用 Debug／Release 引擎各跑 17 项，均通过。独立审计 150 个候选路口，其中 71 处显式模型通过、36 处共享通道通过、43 处仍需人工判断。
- 在简单双车道图中，没有 `crossing` 时对侧设施不可达；添加显式过街后，C++ 输出为步行距离耗时加一次 20 秒等待。
- 校验局部截面从四条变两条，已关闭的内侧入口和两条连接均不再出现；框外片段保持、无悬空引用，所有已标注路口独立结构检查通过。
- 前端 136 项测试通过，生产构建成功；既有大型路网分块体积警告仍在。

这些检查验证模型结构和计时语义，不代表已证实现实隔离带、人行横道或入口的位置。

## 重现

在仓库根目录执行：

```powershell
Push-Location frontend
node scripts/build-demo-road-graph.mjs --write
Pop-Location
.\.venv\Scripts\python.exe backend/scripts/export_synthetic_preview.py
.\.venv\Scripts\python.exe backend/scripts/render_divided_road_review.py
.\.venv\Scripts\python.exe backend/scripts/audit_walking_network.py
.\.venv\Scripts\python.exe backend/scripts/audit_crossroads.py --sheets
Copy-Item -LiteralPath data/networks/divided-road-pilot.svg -Destination frontend/public/walking-divided-road-pilot.svg
Copy-Item -LiteralPath data/networks/synthetic-preview-audit.svg -Destination frontend/public/walking-network-review.svg
```

全图及路口专项图的其他复制命令见现有专项记录。本轮只做局部优化，等待人工校对后再确定下一阶段范围。
