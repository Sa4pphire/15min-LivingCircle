# 区域包与路网范围地图

默认区域为 `data/regions/shanghai-new-jiangwan/`，标识通过 `REGION_ID` 选择。包内当前保存新江湾城演示路网及其地图、官方 BD-09 校准网格、生成源图和人工标注。现有路网的节点、边、设施和人工修改完整保留，合成及未核实标记继续保留。

## 范围和比例尺

区域包不需要 `boundary.geojson`。前后端从 `network.json` 中所有 `edges[*].pathMeters` 的极值计算数据外接矩形；地图中心、允许拖动的视野、最低缩放和起点选择使用这一范围。后台也校验起点，不能通过直接请求绕过范围限制。

当前范围约 5.23 × 5.21 公里，包含 6,576 个节点、7,345 条边。它是数据范围，不是行政边界或经核实的公共通行面。路外起点仍沿用现有合成模型的估算接入规则；靠近数据边缘的 15 分钟结果可能被截断，报告输出 `NETWORK_EXTENT_LIMITS_RESULTS`，前端也展示提示。

SVG 与百度底图均使用相同的范围。缩小到最低比例时，地图会保证视窗留在数据范围内；长宽比不一致时显示范围的一部分，可拖动查看另一部分。“复位”回到范围中心。米制比例尺随当前实际视图更新，缩放不会仅改变一个固定倍数标签。

编辑器的范围根据当前编辑图更新。移动已有路段改变数据范围后，视野限制也随之更新；节点坐标重合仍不会建立新的连接。

## 文件与坐标

```text
shanghai-new-jiangwan/
  manifest.json                 # 版本、文件清单、SHA-256 校验和、来源
  network.json                  # v2 引擎图及设施入口
  context.json                  # 本地地图展示资源
  alignment.bd09.json           # 官方坐标校准网格；无选区多边形
  source/road-graph.json         # 用于重建的道路源图
  source/annotations.json       # 人工校对和可回放编辑记录
  README.md
```

引擎局部米制坐标 X 向东、Y 向北；前端展示坐标 X 向东、Y 向南。原点按坐标标识读取 `network.json.originWgs84` 或 `originBd09`，加载器校验底图和坐标数据的原点一致。原始道路源图可能保留历史提取范围元数据，它不再作为用户选点边界。

当前包的 `facilities` 为空；候选 POI 仍可使用运行机器已有的共享缓存，数据完整度继续按原逻辑说明。包不包含原始接口缓存、SQLite 协调数据库或 API 密钥。将区域包复制到新机器，不会自动取得这台机器原先没有的 POI 清单。

## 编辑器框选导出

1. 打开本地路网编辑器，在入口选择已有区域包。
2. 点击“框选打包”（B），在地图上拖动矩形。选区的宽、高至少 10 米。
3. 查看预览数量、裁剪端点和被排除的连接，填写新区域名称、标识和版本。
4. 点击“生成并下载区域包”。后端检查结构并调用 C++ 引擎，然后建立新目录和 ZIP。可以直接“打开新区域”，也可随后从区域列表选择。

打包采用当前编辑画布的快照，包含未保存的修改；不会保存或覆盖父区域。相同标识已存在时拒绝覆盖。包保存到 `data/regions/<新标识>/`，归档保存到 `data/region-packages/<标识>-<版本>.zip`，ZIP 也会下载到浏览器。

裁剪规则：

- 步行通道按矩形裁剪，保留框内折线；经过边界产生稳定的新节点和路段 ID，记录原路段来源。
- 框内已有共享节点保留原 ID。不同路段在边界或几何交点处重合，不自动合并或建立连接。
- 过街和转弯只保留完全在框内的连接；部分跨框的连接会明确计入预览提示，避免产生半条过街或重复等待。需要完整路口时扩大选框。
- 设施只保留仍有有效框内入口的记录，入口随被裁剪的通道更新路段引用。
- 本地参考图仅保留与新范围相交的要素；百度网格保留覆盖新范围的原有官方锚点，不重新调用转换接口。局部米制原点与父包一致。

框选包的 `manifest.authoringMode` 为 `engine-snapshot`，以 `source/engine-base.json` 保存当前精确裁剪图，`source/annotations.json` 保存后续可回放修改。原始道路源图、路口和人工编辑属性已落实到快照；父包标识和网络修订保留在来源记录。它仍可校验、重新生成、编辑保存、再次框选导出，无需 `boundary.geojson`。

编辑器的选包接口仅改变该编辑会话；主分析页仍按 `REGION_ID` 选择默认包。希望主分析页使用新包时，再设置该配置并重启演示服务。数据边缘仍可能截断可达性结果。

本地开发 API：`GET /api/v1/network-editor/regions` 列表；`GET /api/v1/network-editor/regions/<id>` 元数据；编辑读写、校验、`POST .../package/preview` 与 `POST .../package` 均可通过 `regionId` 选择区域；`GET .../regions/<id>/download` 下载当前包。

## 在编辑器生成本地轮廓

点击右侧“生成本地轮廓”或工具栏“本地轮廓”（C），框选范围后开始生成；空白草稿可直接使用。生成结果先在本地底图上预览，点击“保存轮廓到区域包”才替换 `context.json` 并更新清单校验和。原轮廓和清单自动备份，当前未保存的路网修改保留。

支持 OSM 道路、建筑、绿地、水体及包含内洞的复杂多边形关系。坐标写入包内现有东／南局部平面；BD-09 包的检索包络外扩 1500 米，结果通过官方 WGS-84 正向转换的 250 米网格对齐后按选框裁剪。几何简化容差为 1 米，仅作用于显示轮廓。首次生成需要联网及该坐标模型所需的现有服务端地图配置，生成后展示直接读取包内文件。源数据稀疏不会自动补画地物。

- `POST /api/v1/network-editor/context/preview?regionId=<id>`：提交 `revision` 和 `boundsMeters`（X 东正、Y 北正），返回轮廓、分类计数及 `previewId`。
- `PUT /api/v1/network-editor/context?regionId=<id>`：提交同一 `revision` 和 `previewId`，保存预览。跨区域、过期或文件已变化的预览会被拒绝。

单边最大 6 公里、面积最大 25 平方公里；重复请求复用本地 OSM／坐标转换缓存。`context.json` 记录 OSM 来源、ODbL 署名、生成时间与坐标模型，`manifest.contextSource` 记录轮廓来源；框选导出继续保留这些信息。下载、转换或保存失败时保留上次有效数据。

## 加载和迁移

将目录放到 `data/regions/<id>/`，设置 `REGION_ID=<id>`，重启后端并刷新网页。支持 WGS-84 原点与官方正向校准网格的区域，也支持新建的原生 BD-09 原点区域；网络格式仍由现有 Python/C++ 契约约束。空白草稿用于编辑，绘制通道并保存后才能作为路由数据使用。

## 从地点新建区域

在编辑器的区域选择页点击“创建新区域包”，输入省市、区县或附近地标，点击“创建并打开编辑器”。地点查询使用浏览器地图 AK；缺少密钥、网络超时或查询无结果时显示错误并允许重试。成功后自动建立 `data/regions/region-<随机标识>/`，并定位到该地点的百度底图。

新建包不含路段，`manifest.status` 初始为 `draft`，`network.authoringWorkspace` 为 `true`。空白节点数组和仅有节点的草稿可以保存；新增步行通道并通过检查保存后，状态更新为 `ready`。工作画布的平移缩放独立于已绘制路网的外接范围，便于继续向周边添加数据；分析使用的范围仍从实际路段派生。只有一条水平或垂直通道时，退化轴附加最小显示余量，避免地图范围为零。

本类区域使用 `originBd09`，`context.originBd09` 和 `alignment.kind=bd09_local_meters`。百度坐标保持原始标识，局部米制平面采用近似换算，不含 WGS-84 校准网格。已有 WGS-84 包继续使用原有校准模型。框选导出的子包保留相同原点和坐标模型，恢复由数据范围约束的编辑视野。

新建接口为本地开发 `POST /api/v1/network-editor/regions`，请求包含地点文本、名称和浏览器查询得到的 `centerBd09`。名称和标识可在区域列表中查看。密钥不进入区域文件。

后端提供：

- `GET /api/v1/region`：名称、版本、网络修订、节点/边数量、派生范围、地图资源地址。
- `GET /api/v1/region/assets/context`：地图数据。
- `GET /api/v1/region/assets/alignment`：本地区的校准网格。

前端在启动前读取区域配置，不再静态导入固定样区地图或固定坐标原点。分析结果记录区域 ID 与网络修订。浏览器编辑器草稿按区域 ID 隔离；保存编辑会更新包内 `network.json`、`source/annotations.json` 和清单校验和。

容器已包含 `data/regions/`；现有 Compose 数据挂载也包含区域包。使用 `.env.example` 中的 `REGION_ID` 配置即可。`SYNTHETIC_NETWORK_PATH`、`POI_MAP_ASSET_PATH` 等旧路径覆盖如果保留，必须与选中的区域包一致。

本地 `start-demo.bat` 和 `start-network-editor.bat` 从后端配置读取区域路径，并在启动前校验清单及校验和。启动后也验证 `/api/v1/region` 和前端代理，避免仅引擎健康而网页区域加载失败。更改区域配置后执行 `start-demo.bat restart`；编辑器服务执行 `start-network-editor.bat restart`。旧服务仍使用启动时的环境，刷新网页不能改变后端的路径配置。

## 编制和导出

在仓库根目录检查并生成 ZIP：

```powershell
.\.venv\Scripts\python.exe backend/scripts/package_region.py --archive
```

输出为 `data/region-packages/shanghai-new-jiangwan-0.1.0.zip`。压缩包只包含清单列出的数据文件与说明，不包含测试、生成 SVG 或缓存目录。

从本区域包的源图和标注重建引擎图：

```powershell
.\.venv\Scripts\python.exe backend/scripts/export_synthetic_preview.py
```

旧数据位置仍保留，可以显式使用 `--legacy` 进行历史源数据导出。新编辑优先通过当前区域包进行，避免维护两份各自变化的运行图。发布前先增加清单版本，再检查并归档；编辑后的已有 ZIP 不会自动更新，应重新运行打包命令。

校验覆盖文件路径、内容哈希、图结构、原点一致性和校准网格范围；未通过时停止打包。图结构通过不代表现实通行已实地核实。
