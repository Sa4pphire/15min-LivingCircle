# 百度 POI：Python 主服务与 C++ 演示共用缓存

## 当前链路

两种 C++ 演示仍读取已建模的 `synthetic-preview.json`。在线 POI 只丰富本次请求，不改写静态路网文件；C++ 不读取 AK，也不直接请求百度：

```text
普通 Python POI 调用 / C++ 演示的 PoiService
  → 同一个 BaiduClient
  → cache.py 的 JSON 响应缓存与请求合并
  → 未命中才请求百度
  → Python 规范化设施、尝试接入已建模步行边
  → C++ 计算 → Python 输出候选点、分类数量与模型耗时
```

共享实现以协作者提交 `5be3f6a` 的 `cache.py`、`poi.py` 和客户端缓存设计为基础。保留 `make_cache_key`、`get_cached_json`、`save_cached_json` 及 `createdAt/payload` JSON 封装，不另设一套 C++ 专用响应缓存。

`poi_cache.py` 现在提供 SQLite 请求租约、鉴权／配额冷却和旧缓存迁移基础。在线响应统一写为 JSON；SQLite 中旧 `entries` 只作为迁移来源，**不再新增第二套规范化 POI 响应**。已有有效的演示缓存能自动转成共享 JSON，保留原查询时间、导航点及原始分页数量，不消耗 API 额度；旧文件不会被整体删除。

## 协作者如何复用

直接复用客户端，原有返回字段不变：

```python
from app.baidu.client import BaiduClient

client = BaiduClient()  # 生产环境默认开启共享缓存
try:
    basic_points = await client.search_pois("学校", (lng, lat), coord_type="bd09ll")
    detailed_page = await client.search_poi_page("学校", (lng, lat), coord_type="bd09ll")
finally:
    await client.aclose()
```

上述两次相同参数查询共用一次 `scope=2` 响应；详细页额外提供分页状态、标签和可选导航点。mock 测试默认不启用缓存，需要时传 `cache_dir=tmp_path, cache_enabled=True`。

要与 C++ 页面复用相同的四类检索、空间网格和分页策略，推荐直接复用更高一层：

```python
from app.pois import CATEGORIES, PoiService
from app.schemas import CenterPoint

service = PoiService()
try:
    records, metadata = await service.search(
        CenterPoint(lng=lng, lat=lat, coordType="bd09ll"),
        radius=2340,
        category_ids=list(CATEGORIES),
    )
finally:
    await service.client.aclose()
```

协作者现有 `poi.py.collect_pois` 保留菜市场、药店、小学的既有契约，也会通过同一个 `BaiduClient` 缓存。它的关键词或中心、半径与演示不同，**不保证互相命中**；不同业务查询不能为了省请求而混用结果。需要完全共用四类清单时，用 `PoiService` 或下面的 HTTP 入口。

如果两人在不同电脑开发，仅共用 GitHub 代码不等于共用缓存数据：各自的 `data/cache` 仍是独立文件。建议两人的页面调用**同一个开发后端**的 POI 入口；不要把 AK 或缓存响应提交到 GitHub 来同步，也不要把 SQLite 协调数据库放到未经验证的网络文件共享中。

## HTTP 入口与结果

`POST /api/v1/pois/search` 独立检索候选设施：

```json
{
  "center": {"lng": 121.504429458, "lat": 31.331174183, "coordType": "wgs84ll"},
  "radiusMeters": 2340,
  "categories": ["education", "healthcare", "shopping", "public_service"],
  "refresh": false
}
```

返回 `items`、`metadata`、`categoryLabels`。四类默认关键词为学校／幼儿园、医院／社区卫生服务中心、超市／便利店、街道办／社区事务受理中心／派出所／邮局。

对 `/api/v1/synthetic-analyses` 或 `/api/v1/local-experiments` 请求加 `includePois: true`，即可在计算前注入候选 POI。后端旧调用默认 `false`，保持离线兼容；前端这两个模式默认 `true`。`refreshPois: true` 明确绕过新鲜缓存并消耗额度，普通重复分析不要开启。

输出增量见 [baidu-poi-result.example.json](../contracts/baidu-poi-result.example.json)：

- `poiFacilities`：BD-09 原始点位 GeoJSON，包含类别、接入状态、模型耗时及展示范围内标记。
- `poiCategories`：检索数量、近似面内数量、局部可达街段附近数量、模型可达数量和未接入数量。一个 UID 可属于多个类别，分类计数相加不等于去重总数。
- `metadata.poi`：真实 HTTP 请求次数、缓存命中、旧缓存使用、查询范围、分页状态、坐标对齐方式及数据核查状态。缓存来源为 `shared_baidu_json`。

## 缓存配置与省额规则

- `ANALYSIS_CACHE_DIR`：两方必须指向同一目录，默认仓库的 `data/cache`；相对路径统一相对仓库根目录解析，不受从 `backend/` 启动影响。
- `POI_CACHE_PATH`：默认 `data/cache/baidu-pois.sqlite3`，协调并发、记录冷却并读取旧缓存。若自行覆盖，它也须在双方保持一致；只共享 JSON 目录、却使用不同协调数据库不能保证并发请求合并。
- `POI_CACHE_TTL_HOURS=168`：POI 新鲜期 7 天；`CACHE_TTL_HOURS=24` 用于其他普通接口。正向坐标校准使用 30 天缓存。
- `POI_CACHE_STALE_HOURS=720`：请求失败时最多使用 30 天内旧数据，明确标为旧缓存，不伪装为新查询。
- POI 查询中心按 500 米网格固定，半径补 360 米，保证覆盖用户要求的圆；同一格、同一半径和类别可复用。默认每类最多 2 页，每页 20 项，整体检索预算 12 秒。
- 缓存键包括服务、接口和全部实际公开参数：关键词、坐标系、中心、半径、页码、详情等级等；不包含 AK。空的成功结果也缓存，失败不保存为空结果。
- SQLite 租约合并同参数的并发冷请求／并发强制刷新，30 秒超时；鉴权或配额错误冷却 60 秒，期间可使用有效旧缓存。
- 兼容协作者旧 JSON 格式，但旧 `scope=1` 与新 `scope=2` 不可混用；取得导航点时首次详情查询可能仍需一次请求，这是参数不同，不是重复扣费。

百度缓存和使用范围仍须符合账户权限及[开放平台服务协议](https://lbsyun.baidu.com/docs/pcsa?title=law%2Fopen%2Flaw)，缓存文件已被 Git 忽略，不用于公开重发布。

## 坐标、设施入口与灰区的边界

地点检索输出保留 BD-09，不把 WGS-84 查询输入误当成返回坐标系。WGS-84 合成图使用百度正向转换的三个锚点建立近似局部映射；不是精确逆转换，不用于证明米级真实精度。[地点检索说明](https://lbsyun.baidu.com/docs/webapi?title=placev3/guide/webservice-placeapiV3/interfaceDocumentV2)、[坐标转换说明](https://lbsyun.baidu.com/docs/webapi?title=geoconv/guide/changeposition-base)。

POI 中心点只用来显示，不是设施入口。只有 API 提供导航点，且它位于已建模的人行道／共享通道附近（默认 3 米，并排人行道仍检查侧别歧义），才建立**未核实的模型接入**。不能凭 POI 中心直线穿越地块，也不新增过街连接；无可靠接入时 `modelTravelTimeSeconds` 与 `modelReachable` 为 `null`。

前端“圈内设施”按近似展示面筛选；局部实验按可达街段附近 15 米或模型可达筛选。两者均不等于已核实的真实覆盖。在线设施类别保持 `dataStatus/localInventoryStatus: incomplete`，因此不能以 API 清单生成真实匮乏结论；当前路网未核实裁剪出口的限制仍保留。

## 密钥与本地运行

推荐将服务端 AK 置于启动环境的 `BAIDU_SERVER_AK` 或忽略的根 `.env`，不要放入前端构建。为了兼容目前配置，后端也能从 `frontend/.env.local` 读取 `BAIDU_SERVER_AK`／旧 `VITE_BAIDU_SERVER_AK`；Vite 仅暴露 `VITE_BAIDU_BROWSER_` 前缀。不要发送真实密钥或提交 `.env.local`。若此前服务端密钥已经进入构建／被传播，应在百度控制台轮换。

本地示例：`./start-demo.ps1 start -BackendPort 8129 -FrontendPort 5182`，再访问 `http://127.0.0.1:5182/?mode=synthetic` 或 `?mode=local`。停止这组服务时使用相同端口：`./start-demo.ps1 stop -BackendPort 8129 -FrontendPort 5182`。没有浏览器 AK 时仍可使用合成 SVG 视图；服务端 POI 查询独立配置。

## C++ 合成演示的操作

1. 切换到“合成算法”，点击地图选点或“选区域中心”，再计算 15 分钟等时圈。默认同时加载四类 POI，不需要改写合成路网文件。
2. 计算完成自动切换到“全圈”视野，适配实际 MultiPolygon 和圈内点位，并避开分类控件与图例。原来的大／中／小缩放仍可用；“全圈”按钮可恢复总览。
3. 地图用“校／医／购／公”区分类别。点击筛选只改变显示，不调用 API；点击点位查看名称、地址与接入状态。POI 不在近似等时圈面内时不显示，即便其模型耗时可达。
4. 第二屏对比每类的“检索候选／圈内／圈内模型可达”数量。医院圈内为 0 不代表 AK 调用失败，也不代表真实设施匮乏。一个学校及其不同门口可能是独立 POI，数量不等于机构数。
5. 地图和报告显示本次缓存命中与 API 请求数。普通重复计算优先用共享缓存；只有明确点击“刷新 POI（调用 API）”才要求强制更新。

百度底图或浏览器坐标校准失败时会回退到 SVG，后端 POI 检索与共享缓存不受这一浏览器故障影响；点位采用明确标注的近似局部坐标对齐。

## 不消耗额度的验证

`test_cache.py`、`test_baidu_cache.py`、`test_poi.py` 保留协作者测试；`test_pois.py` 与 `test_shared_poi_cache.py` 覆盖演示、JSON／旧 SQLite 兼容、跨入口命中、并发合并、刷新、失败旧缓存回退、错误参数隔离及 C++ 往返。测试使用 mock 和临时目录，不依赖真实 AK。
