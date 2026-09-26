# 前端地图与交互原型

第一页有两个共用真实区域 SVG/百度底图、但分析结果互不混用的模式：“真实区域”继续在浏览器内显示固定圆与临时路线；“合成算法”通过 Python API 调用 C++，显示引擎返回的 MultiPolygon 近似等时圈与可达街段。后者不显示未经验证的设施或灰区。目前路网和过街连接仍是合成数据，两种模式都不是实测报告。

## 启动

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 Vite 输出的本地地址。检查使用 `npm test` 与 `npm run build`。

### “合成算法”模式：Python → C++ → 页面

从仓库根目录运行 `python backend/scripts/export_synthetic_preview.py`，按
[`data/networks/README.md`](../data/networks/README.md) 的说明启动 Python API 和当前 C++
引擎，按 `data/networks/README.md` 启动 Python API，再运行 Vite。切换到“合成算法”后，选点并点击右上角的计算按钮；页面调用独立的 `/api/v1/synthetic-analyses`，随后轮询 `/api/v1/analyses/{id}`。不需要浏览器环境开关，也不影响“真实区域”的固定圆示意。合成路网把非主干道暂作共享通道，过街／接驳均未实地核实，页面结果**不是
经核实的真实 15 分钟步行等时圈**。API 合成数据使用 WGS-84，前端仅将其转回本地 SVG 坐标；
百度底图开启时叠加仍是近似预览。

合成模式允许在围合区内非道路位置选点：C++ 找最近的可步行边，将直线接入距离按 1.3 米／秒换算并先从 900 秒预算中扣除，例如接入花 5 分钟，沿路网只剩 10 分钟。虚线表示这条**未经核实**的估算接入，不保证穿过建筑、围墙或封闭地块；耗尽预算的选点会被拒绝。C++ 对剩余时间内的街段做截断，再生成 MultiPolygon 近似面，而不是画一个缩小版固定圆。

### 可选：百度真实底图

复制 `frontend/.env.example` 为 `frontend/.env.local`，将可用于浏览器的百度地图 JavaScript API AK 填入 `VITE_BAIDU_BROWSER_AK`，并在百度控制台配置本地开发域名。**不要提交 `.env.local`，也不要放服务端私密 AK**：`VITE_` 值会进入浏览器。暂时没有 AK 时，页面仍能运行，展示带来源署名的 OSM 道路 SVG 预览；它不是百度地图截图或百度瓦片的 SVG 导出。

有 AK 时，页面使用百度 JSAPI 4 显示真实底图，并由官方 `BMap.Convertor` 把本地边界的 WGS‑84 坐标转换为 BD‑09 后叠加 SVG。地图关闭滚轮连续缩放，滚轮仍用于翻页；中／大档可拖动。AK 缺失、转换失败或地图加载失败时均回退到 SVG 预览。由于目前没有 AK，百度叠图的实际四路口对齐仍待人工校验。

### 范围和数据边界

展示区由国帆路（北）、江湾城路（东）、殷高东路（南）、国权北路（西）道路中心线围成，是**项目自定义演示范围，不是行政边界**。路段折线来自 OpenStreetMap；原始 WGS‑84 GeoJSON 在 `src/data/demoBoundary.wgs84.json`，扩展后的无 AK 道路预览在 `src/data/demoContext.extended.wgs84.json`，原始 `demoContext.wgs84.json` 保留不覆盖。来源及 ODbL 许可见数据属性和页面署名。`scripts/build-demo-boundary.mjs` 与 `scripts/build-demo-context.mjs` 记录了一次性提取方式；重新运行会访问 Overpass，需复核新数据，不应直接覆盖已审核资源。

虚线围合区只限制起点；底图数据范围在四路边界的外接框四周额外扩展 1,300 米，覆盖 900 秒 × 1.3 米／秒的理论直线外延并留有余量。缩放等级分别为小档 1.5×、中档 3×、大档 5×；小档也会裁去部分外围画面，中／大档可拖动查看。无 AK 的 SVG 道路与绿地数据覆盖扩展范围，建筑只保留选区周围 450 米以控制体积。区内点击记录候选点，区外点击提示范围限制；“真实区域”示意模式点击分析会显示半径固定为 1,170 米的**示意圆**，不是等时圈。真实计算仍须检查该点是否位于公共步行空间、能否接入路网。**起点选区不用于裁剪真实 15 分钟路网或等时圈**，路网和设施必须覆盖所有区内起点可能到达的外围。当前没有真实体检报告。

## 目前可操作的内容

- 默认“真实区域”模式下，在四路围合区内选点，再点击右上角“生成示意分析”：出现 1,170 米固定圆、未核实接入虚线和按临时路网距离逐段描绘的路线。重新选点会清除旧结果；再次分析可重播。没有 AK 时在 SVG 底图上运行。
- 切换到“合成算法”后，在同一份底图内选点，点击右上角“计算 15 分钟等时圈”；C++ 返回 MultiPolygon 和可达街段并在地图上动画呈现。第二屏展示路网计数、到街边的估算时间与数据精度说明。
- 地图右侧提供“大／中／小”三级比例视图：相对完整范围基准分别为 5×、3×、1.5×，大档围绕已选点。默认显示中档；切换时有短暂平滑过渡。中／大档可按住拖动底图，小档固定在中心视野。真实范围 SVG 预览与合成算法演示共用这一选择。启用百度底图时同样使用三级视图，不开放连续滚轮缩放，以免与翻页冲突。
- 第一屏分为项目信息、横向铺满的地图和 `LIVING CIRCLE` 字样三部分。底部字母的分层错位效果自动循环，不依赖鼠标位置；开启“减少动态效果”时停止动画。
- 滚轮累计滑动一定距离后整屏翻页：地图演示独占第一页，控制面板在第二页；触屏可上下滑动，也可使用页面导航按钮。
- 起点以小圆点显示：绿色为已分析起点，橙色为待分析起点。
- 桌面鼠标在地图上显示“步行探针”；可在围合区内选点。当前不展示设施和灰区交互。
- 主按钮与地图结果有按下、处理中和完成反馈；触屏与“减少动态效果”模式保留功能但收敛动画。
- 第二页内容较长时可在页内滚动；窄屏下起点、结果、服务类别顺序堆叠。
- 控制面板页底部使用保持字形比例的全宽 `GEOVIEW` 描边字；桌面鼠标滑过字母时，彗星状光迹仅在字形内部出现并逐渐消散。

入口是 `src/entry.js`。主界面位于 `src/App.vue`，两模式共用的地图位于 `src/RealMapStage.vue`，地图坐标与 GeoJSON→SVG 投影在 `src/mapGeometry.js`；主样式、真实地图样式分别在 `src/demo.css`、`src/realMap.css`。原有的 `src/main.js` 和 `src/styles.css` 暂时保留，不被新入口使用。

## 后续真实数据对接

`vite.config.js` 已将 `/api` 代理到本地 `8000` 端口。`src/analysisClient.js` 仅用于“真实区域”的离线固定圆；`src/cppAnalysisClient.js` 用于“合成算法”，把 Python/C++ 输出规范化到同一地图显示契约。`App.vue` 独立管理两模式的选点、状态与重试；`RealMapStage.vue` 只消费结果并负责 SVG/百度投影与动画。不要将本版示意圆或未核实合成路网用于正式报告。

地图结果的最小契约为 `schemaVersion`、`coordinateSystem`、`displayArea` 和 `routeSegments`。“真实区域”返回 `coordinateSystem: "preview-local-v1"`、`displayArea: { type: "circle", center: {x,y}, radius: 1170 }`；“合成算法”返回同一局部坐标系的 `displayArea: { type: "polygon", geometry: { type: "MultiPolygon", coordinates: ... } }`。两者的路线均为 `routeSegments`；动画顺序只用于展示，不代表逐街段耗时。后端真实结果接入时，需使用经校准的 BD‑09 坐标或在适配层转到 SVG 局部坐标。目前临时局部坐标与百度底图仅做仿射预览对齐，不能用作精确分析。

### 选区内合成道路图（建模第一阶段）

`src/data/demoRoadGraph.local.json` 是与无 AK 的 SVG 底图对齐的 `preview-local-v1` 局部坐标图：`selectionBoundary` 保留四路围合的起点选区；`coverageBoundary` 是该边界外接框四周扩展 1,300 米后的道路建模矩形，与地图全览基准范围使用同一套 `expandedLocalBounds` 计算。`nodes` 存节点 ID 与 `x/y`；`edges` 存两端 ID、`roadMajor`／`roadLocal`／`roadPath`／`inferredJunction` 类别、来源 way ID 和局部长度；`junctions` 是共有顶点，`inferredJunctions` 单独记录未核实的推断连接。图中**没有 WGS‑84 或 BD‑09 运行时坐标**。`node scripts/build-demo-road-graph.mjs --write` 可从已保存的 `demoContext.extended.wgs84.json` 重建它，`node scripts/render-demo-road-graph.mjs --write` 可生成全图校核图；开发预览可打开 `/demo-road-graph.svg`。

建图保留整个 `coverageBoundary` 内的道路折线，穿越四路围合边界的同一来源道路不再被截断；起点仍只能落在 `selectionBoundary` 内。原始 SVG 已做约 2 米折线简化，导致一些真实道路端点靠近另一条线段，却不再共享顶点。`src/roadGraphRepair.js` 只对**不同连通片**中相距不超过 1.5 米的端点—线段做推断补连：拆开目标线段，并增加一条短 `inferredJunction` 边；明显平行的端点—线段关系不补，线段中部彼此相交也不自动补。每个推断均为 `verified: false`，可与原始道路边区分。

补连前为 7,492 节点、6,886 边、875 个连通片；补连后为 8,518 节点、9,232 边、1,320 条推断连接记录、72 个连通片。`node scripts/audit-demo-road-graph.mjs` 输出全图端点、未消除的近距断点及当前示例起点的连通统计；追加 `--details` 可列出周边所有端点。`node scripts/render-demo-connectivity.mjs --write` 生成 `/demo-connectivity-audit.svg`，与 `--selection-only --write` 生成的旧图 `/demo-connectivity-before.svg` 对照。测试覆盖端点接入、平行近邻不接、几何交叉不接、边长／ID／范围一致性和示例起点的路线延伸。

由于仍缺少原始 OSM 节点 ID、桥梁／隧道层级、步行权限、人行道及合法过街标注，这只是**用于路线动画的合成模型**，不能据此宣称真实步行可达。剩余 72 个连通片和近距断点需要继续核查；桌面宽屏或拖动时可能露出基准矩形外少量底图，那里尚未保证完整路网。真实版必须以可能到达的范围而非屏幕边缘决定数据覆盖，并用可信数据核实连接与过街，再由后端结果替换示意圆与路线。
