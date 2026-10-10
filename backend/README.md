# 在线计算：百度步行采样与 Python 近似等时圈

[返回项目主页](../README.md) · [合成模拟算法](../cpp-engine/README.md)

本文对应页面中的“在线计算”。它使用百度地图提供的步行距离、耗时和路线，不调用 C++ 路网引擎，也不直接调用百度等时圈接口。Python 根据有限采样点估计连续耗时场，再提取 900 秒轮廓。

## 1. 整体流程

```text
地图选点
  → Python 统一为 BD-09 坐标
  → 生成 48 个目的地，查询百度步行 RouteMatrix
  → 合并中心点的 0 秒样本，IDW 插值到米制网格
  → Marching Squares 提取 900 秒闭合轮廓
  → 转回 BD-09，读取区域地点快照、查询参考路线并估算服务盲区
  → 前端绘制 MultiPolygon、POI 与路线动画
```

前后端先按所选区域包校验起点范围。区域地图用于定位和展示；在线模式的步行耗时来自百度服务，不是从区域包的合成道路图算出来的。

## 2. 步行耗时采样

以起点为局部平面原点，X 向东、Y 向北。默认每隔 30° 取一个方向，每个方向取 300、600、900、1200 米四个目的地，共 48 个；中心点另作为耗时为 0 的样本。

目的地从米制偏移近似换算为 BD-09 经纬度，调用客户端的 `/routematrix/v2/walking`。返回的 `distanceMeters` 和 `durationSeconds` 按目的地顺序合并，最终得到 49 个耗时样本。采样半径是直线距离，不代表实际步行路程或可达范围。

默认参数如下，定义在[采样模块](app/sampling.py)和[等时圈流程](app/sampled_analysis.py)中：

| 参数 | 当前默认值 |
| --- | --- |
| 步行时间阈值 | 900 秒 |
| 采样方向 | 12 个，每隔 30° |
| 采样半径 | 300、600、900、1200 米 |
| 插值网格步长 | 100 米 |
| IDW 距离幂次 | 2 |

## 3. IDW 插值与轮廓提取

在局部 `[-1200, 1200] × [-1200, 1200]` 米范围内生成规则网格。默认是 25 × 25 个网格点；每个点的耗时按所有样本的距离反比权重估计：

```text
w_i(p) = 1 / distance(p, sample_i)^2
T(p)   = Σ(w_i(p) × duration_i) / Σ(w_i(p))
```

网格点恰好落在样本上时直接使用该样本耗时，避免除以零。实现见 [interpolation.py](app/interpolation.py)。

[contour.py](app/contour.py) 使用 Marching Squares，根据网格四角是否小于等于 900 秒生成等值线段，在线段内线性插值求交点，然后连接闭合环，输出 `MultiPolygon`。米制几何保存在 `isochroneMeters`，经纬度几何保存在 `isochrone`。

这是近似方法，当前有以下边界：

- IDW 按平面距离插值，没有显式建模围墙、水体、道路侧和路口等待，可能把阻隔两侧的耗时平滑到一起。
- 外围采样仍可达时，现实现不会自动扩大 1200 米采样范围；触及网格边界的等值线可能不闭合。轮廓提取只保留闭合环，因此可能出现缺块或空结果。
- 轮廓组环实现将各闭合环作为独立 Polygon，不提供完整的嵌套环／内洞分类。它不能替代精细路网的可达性验证。

因此结果明确包含 `approximate: true`，不能把每一个面内点都断言为实际 15 分钟可达。

## 4. POI 与路线展示

设施查询复用项目的五类规则：学校、医院、商超、公共服务、餐饮。先按当前等时圈的外包半径向外增加 1000 米查询候选，再按不规则等时圈筛选面内 POI。圈外候选仍可参与圈内服务覆盖估算。

区域包提供 `pois.json` 时，默认直接筛选包内地点快照，不调用地点检索 API，也不依赖原机器的请求缓存；旧包没有该文件时兼容原检索流程。快照未经完整性核实，因此不会把未取得的类别判为设施缺口。前端在选定起点并完成计算后显示面内地点，进入页面及计算过程中隐藏。在线步行耗时和参考路线仍使用百度服务，详情见[区域包说明](../docs/region-packages.md)。

设施点是地图候选，不等于已核实入口。`insideDisplayPolygon` 表示位置在近似面内，不表示已验证步行耗时。

路线有两种用途：

- **设施参考路线**：每类最多保留 5 个在 900 秒内的目的地，优先探查距离起点较近的候选，另留少量失败／超时替补。调用 `/direction/v2/walking` 获取折线和整条路线耗时。不是为所有 POI 都提前算路。
- **方向参考路线**：每个采样方向先尝试外层目的地，再尝试一层内侧目的地；将百度折线裁剪到近似等时圈，用于展示道路走向。裁剪依据几何面，而非沿路逐秒截断，不能把动画终点当作精确 900 秒终点。

点击报告或地图上的 POI，通过按需路线接口获取路线；已有分析参考路线可直接复用，否则查共享缓存或请求百度步行规划。超过 900 秒的路线仍可返回，`withinThreshold` 用于说明是否符合时间预算。

相关实现：[sampled_service.py](app/sampled_service.py)、[representative_routes.py](app/representative_routes.py)、[route_sampling.py](app/route_sampling.py)、[sampled_poi_routes.py](app/sampled_poi_routes.py)。动画仅表现路线绘制，不是实际行走或后台计算进度。

## 5. 疑似服务盲区

当前在线模式采用**直线服务半径估算**，不是从每个设施反向计算步行等时圈：

1. 在局部米制坐标下，以同类别 POI 为中心生成半径 1000 米的服务圆，并合并。
2. 只在本次等时圈内做覆盖／未覆盖分割，保留各区域缺少的类别组合；圈外 POI 的服务圆也能覆盖圈内区域。
3. 查询分页未完成、记录被丢弃或查询报错的类别记为未知，不因数据缺失断言设施不存在。
4. 保留输入面的内洞，过滤面积小于 100 平方米的零碎盲区，并输出被过滤面积。

实现见 [blind_zone_coverage.py](app/blind_zone_coverage.py)。结果包含 `coverageMetric: "straight_line_radius"` 和 `candidateOnly: true`。即使查询完整度对应的状态为 `confirmed`，也仅表示当前查询规则下可作覆盖估算，不代表设施清单、实际入口或地块通行已经核实。

这一口径与 C++ 引擎按入口路网耗时生成的灰色街段不同，不应直接比较两个模式的盲区面积或覆盖比例。

## 6. 前后端接口

| 接口 | 用途 |
| --- | --- |
| `POST /api/v1/sampled-analyses` | 提交 `center`、可选 `regionId`；当前固定分析 15 分钟，返回 `analysisId` |
| `GET /api/v1/sampled-analyses/{analysisId}` | 轮询 `queued/running/completed/failed`；完成后读取 `result` |
| `POST /api/v1/sampled-analyses/{analysisId}/poi-route` | 提交 `{"poiId":"当前分析中的设施 ID"}`，查询选中设施的路线 |

`center` 包含 `lng`、`lat` 和 `coordType`（`wgs84ll` 或 `bd09ll`）。WGS-84 起点先通过百度客户端转换为 BD-09，不能只更改类型标签。

主要结果字段：

| 字段 | 含义 |
| --- | --- |
| `sourceMode` | `baidu-sampled`，用于与 C++ 模式区分 |
| `isochrone` / `isochroneMeters` | BD-09／以本次起点为原点的局部米制 MultiPolygon |
| `durationSamples` | 各采样点、步行距离与耗时 |
| `poiFacilities` / `poiCategories` / `poiInfo` | 面内设施候选、分类数量与查询状态 |
| `routeSegments` / `samplingRouteSegments` | 设施参考路线／方向参考路线 |
| `routeFailures` / `samplingRouteFailures` | 局部路线查询失败或超限记录 |
| `blindZones` | 直线服务半径估算的疑似盲区，包含未知类别 |

后端实现入口在 [main.py](app/main.py)，前端适配在 [sampledAnalysisClient.js](../frontend/src/sampledAnalysisClient.js)。任务状态暂存进程内存，重启服务后需重新分析；前端轮询时限不意味着取消后端任务。

## 7. 配置与性能边界

部署见[项目主页](../README.md#部署与运行)。新的在线查询需要服务端 `BAIDU_SERVER_AK`；浏览器地图底图另用 `VITE_BAIDU_BROWSER_AK`，两者不能混用。

客户端具备响应缓存、并发限制、限速及有限重试。默认 `BAIDU_MAX_QPS=1`、`BAIDU_MAX_CONCURRENCY=2`，每个 HTTP 请求默认超时 10 秒；完整任务还包含 POI 和多条路线查询，因此不能将单次请求超时当成整次分析耗时上限。当前在线分析在单进程内串行进入主计算流程，接口进度是阶段提示，不是逐请求精确进度。

冷缓存、网络状况、服务额度及查询数量都影响耗时。优化应优先关注缓存命中率和请求数量，再调整采样密度；增加网格精度不能弥补原始采样不足。POI 与缓存说明见 [地图设施接入文档](../docs/baidu-poi-integration.md)。
