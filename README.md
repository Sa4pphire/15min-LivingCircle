# 15 分钟生活圈智能体检与规划助手

面向比赛演示的步行可达性分析 Web 项目。目标是在已核实的社区步行路网上，计算中心点出发的 15 分钟可达街段、近似等时圈，以及按设施入口耗时判断的服务覆盖和疑似灰区。

项目采用 **Vue 3 + Vite 前端、Python/FastAPI 主服务、C++20 路网引擎**。当前处于演示与联调阶段：真实路网和设施入口仍待采集核实，尚不能生成经验证的真实社区体检报告。

## 当前演示模式

| 模式 | 数据与计算 | 使用边界 |
| --- | --- | --- |
| 真实区域 | 在新江湾城真实地理背景上，由浏览器显示半径 1,170 米的固定圆和临时路线 | 只是交互示意；名称不表示结果已真实验证，固定圆不是等时圈 |
| 合成算法 | Python 加载 `synthetic-preview.json`，调用 C++ 计算 900 秒可达街段和 MultiPolygon 近似面 | 路网连接、过街和起点直线接入未经核实；不作为真实可达性或设施覆盖结论 |
| 局部实验 | 独立接口在指定的小片路网上计算 180 秒可达街段，按类别显示已覆盖、候选未覆盖和未知 | 不进入完整 15 分钟报告；默认真实数据缺失，需显式指定局部文件或合成契约样例 |

前两种模式可使用百度地图 JavaScript API 底图；缺少浏览器 AK 或加载失败时回退到带来源署名的 OSM 道路 SVG 预览。局部实验使用独立街段视图，不需要浏览器 AK。

真实分析接口 `/api/v1/analyses` 与合成接口相互独立。默认真实文件 `data/networks/shanghai-new-jiangwan.json` 尚不存在，真实请求返回 `UNSUPPORTED_AREA`，不会静默替换成合成结果。详见 [前端说明](frontend/README.md) 和 [路网数据说明](data/networks/README.md)。

## 技术架构

| 层 | 技术 | 职责 |
| --- | --- | --- |
| 前端 | Vue 3、JavaScript、Vite 7；可选百度 JSAPI 4 | 选点、任务状态、街段与近似面展示 |
| 主服务 | Python 3.12、FastAPI、httpx | 数据加载、坐标转换、任务编排、引擎子进程和结果适配 |
| 计算引擎 | C++20、CMake 3.20+ | 图校验、Dijkstra、设施入口耗时、街段截断及展示几何 |
| 部署与质量检查 | Docker Compose、CTest、pytest、Node.js 内置测试、GitHub Actions | 构建、契约检查和自动化验证 |

```text
Vue 页面
  → FastAPI REST API
  → Python 规范化路网 JSON（schemaVersion: 2，局部米制坐标）
  → C++ 子进程：标准输入接收 JSON，标准输出返回 JSON
  → Python 转换为地图坐标及 GeoJSON 风格结果
  → 前端绘制可达街段、近似面或局部分类街段
```

这条链路适用于合成算法、局部实验及真实分析接口；当前“真实区域”示意模式仍在浏览器内计算展示结果。C++ 不调用百度 API、不读取 AK，也不需要作为常驻服务启动。Python 单次引擎调用上限为 25 秒。

百度坐标转换、RouteMatrix、POI 客户端及采样模块位于 `backend/app/`，与正式分析任务、设施入口数据和页面报告的完整对接仍需继续完成。

## 仓库结构

```text
backend/           Python API、地图服务客户端、数据适配和测试
cpp-engine/        C++ 类型、JSON 解析、路网算法和 CTest
frontend/          Vue 页面、地图图层、演示资源和前端测试
contracts/         Python↔C++ 与公共 API 的契约说明及合成样例
data/networks/     路网数据、核查要求和合成预览
docs/              架构、测试计划与协作者对接说明
.github/workflows/ 自动化构建和测试
start-demo.ps1     Windows 本地演示启动脚本
```

## 路网算法概要

- 普通道路两侧分别标为双向 `sidewalk`；同侧转弯使用 `turn`，换侧必须经过合法 `crossing`。过街耗时为步行时间加默认 20 秒等待，单边可覆盖等待值。
- 经核实可自由穿行的步行街或共享小巷使用双向 `shared_way` 中心线。横向接入距离计时，不额外增加过街等待。
- 拓扑由节点 ID 和显式连接决定；几何相交或坐标接近不会自动连通。
- 起点及设施入口在接入位置拆边后运行 Dijkstra；多个入口取最短耗时，900 秒处截断可达街段，过街边须完整走完才显示可达。
- `reachableEdges` 和设施耗时用于可达性判定；`displayGeometryMeters` 是便于画图的近似面，不能用面覆盖替代路网判定。
- 局部实验显式记录裁剪出口及核查状态。图外可能存在捷径或设施，因此数据不充分时显示“未知”，不推断设施匮乏。

完整算法见 [C++ 引擎说明](cpp-engine/README.md)，字段和坐标约定见 [v2 契约](contracts/engine-v2.README.md)。

## 快速启动：Docker

需要 Docker 和 Docker Compose。在项目根目录执行：

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Linux/macOS 可用 `cp .env.example .env` 复制配置。打开 <http://localhost:8000>；健康检查为 <http://localhost:8000/api/v1/health>，API 文档为 <http://localhost:8000/docs>。

Docker 会构建前端与 C++，构建过程中运行 CTest，再由 FastAPI 同时提供 API 和前端静态文件。没有 AK 或真实路网时仍可查看示意页面和合成算法，但真实分析接口不可用。根目录 `.env` 由 Compose 注入后端；其中 `/app/...` 路径是容器路径。

## 本地开发：Windows

需要 Python 3.12、Node.js 22、PowerShell 7、CMake 3.20+ 和支持 C++20 的编译器。先在项目根目录安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
```

### 一键启动演示

```powershell
pwsh -File .\start-demo.ps1
```

脚本检查依赖和引擎，启动或复用 `8000` 端口的 API 与 `5173` 端口的 Vite，并执行健康检查和一次合成图 Python→C++ 往返联调。打开 <http://localhost:5173>。按 Ctrl+C 只停止脚本启动的服务，已复用的服务不会被停止；日志位于已忽略的 `data/cache/`。

脚本不会自动安装 Python/npm 依赖；引擎缺失时会尝试 CMake 构建。修改 C++ 后需要强制重新编译时运行：

```powershell
pwsh -File .\start-demo.ps1 -BuildEngine
```

### 分别启动各模块

先构建引擎：

```powershell
cmake -S cpp-engine -B cpp-engine/build -DCMAKE_BUILD_TYPE=Release
cmake --build cpp-engine/build --config Release --target isochrone_engine
```

启动后端时，将 `CPP_ENGINE_PATH` 指向实际生成的文件。Visual Studio 等多配置生成器通常生成在 `build/Release/`；单配置生成器通常生成在 `build/`：

```powershell
$env:CPP_ENGINE_PATH = "cpp-engine/build/Release/isochrone_engine.exe"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

另开终端，从项目根目录启动前端：

```powershell
npm --prefix frontend run dev
```

Vite 将 `/api` 请求代理到 `http://127.0.0.1:8000`。Linux/macOS 手动构建方式见 [引擎说明](cpp-engine/README.md)，可执行文件通常为 `cpp-engine/build/isochrone_engine`。

### 局部实验样例

在启动后端前显式设置以下变量，再切换页面的“局部实验”，点击“填入契约样例坐标”：

```powershell
$env:LOCAL_EXPERIMENT_NETWORK_PATH = "contracts/engine-local-experiment.input.example.json"
```

这份样例是合成教学数据。真实局部文件使用同一变量配置，但不能放到 `WALKING_NETWORK_PATH` 中替代完整生活圈路网。

## 配置与密钥

| 配置 | 用途 |
| --- | --- |
| `CPP_ENGINE_PATH` | C++ 可执行文件路径；本地与容器路径不同 |
| `WALKING_NETWORK_PATH` | 完整 15 分钟分析路网，默认 `data/networks/shanghai-new-jiangwan.json` |
| `LOCAL_EXPERIMENT_NETWORK_PATH` | 独立局部实验路网，默认 `data/networks/new-jiangwan-local.json` |
| `BAIDU_SERVER_AK` | 仅供 Python 服务端地图请求使用 |
| `VITE_BAIDU_BROWSER_AK` | 仅供前端百度 JSAPI 底图使用，会暴露给浏览器 |
| `ANALYSIS_CACHE_DIR` | 缓存/日志目录配置；具体能力以当前调用模块为准 |

本地后端通过进程环境读取配置，不会自动加载根目录 `.env`；可在启动前使用 PowerShell 的 `$env:变量名` 设置。不要直接将 Docker 示例中的 `/app/...` 路径用于本机。

前端配置单独放在 `frontend/.env.local`，可从 `frontend/.env.example` 复制并填写浏览器 AK。Vite 的环境变量在启动/构建时读取；Docker 运行时设置根目录的 `BAIDU_BROWSER_AK` 不会自动替换已构建前端中的 `VITE_BAIDU_BROWSER_AK`。浏览器 AK 应配置域名限制，服务端 AK 不得放入任何 `VITE_` 变量。

不要提交真实 AK、私密数据或含凭据的请求日志。坐标必须明确区分 WGS-84、BD-09 与局部米制坐标；不能仅修改坐标类型标签而不转换数值。

## API 与数据契约

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/v1/health` | 后端与引擎健康状态、服务端 AK 是否配置 |
| `GET /api/v1/presets` | 演示区域预设 |
| `POST /api/v1/analyses` | 真实路网分析；缺少数据或区域不支持时返回 `UNSUPPORTED_AREA` |
| `POST /api/v1/synthetic-analyses` | 独立合成路网分析 |
| `GET /api/v1/analyses/{analysis_id}` | 查询上述分析任务状态与结果 |
| `POST /api/v1/local-experiments` | 独立 180 秒局部实验 |
| `GET /api/v1/local-experiments/{experiment_id}` | 查询局部实验任务 |

任务接口先返回 `analysisId`，随后轮询状态。当前任务状态保存在进程内存中，服务重启后不会保留；它是演示实现，不是生产级任务平台。

- [公共分析请求样例](contracts/analysis-request.example.json)与[结果样例](contracts/analysis-result.example.json)
- [引擎 v2 输入](contracts/engine-input.example.json)与[输出结构样例](contracts/engine-output.example.json)
- [局部实验合成输入](contracts/engine-local-experiment.input.example.json)与[输出样例](contracts/engine-local-experiment.output.example.json)
- [Python↔C++ 对接清单](contracts/python-handoff.md)

契约样例用于开发和字段说明；除明确说明外，不应假定输入输出文件是同一轮引擎计算的逐字段配对结果。

## 数据边界与下一阶段

真实路网必须覆盖支持起点可能到达的完整范围，并核实公共步行空间、人行道侧、合法过街、路口连接及设施入口。不能把 OSM 预览的推断连接或任意直线接入直接当作真实通行关系；`synthetic: true` 的数据必须保留合成标记。

当前优先工作是完成真实样区标注、POI 与入口整理、正式 API/前端报告联调，以及真实路线对照和性能验收。小片局部实验只证明核查范围内的计算，不替代完整生活圈结果。具体要求见 [数据说明](data/networks/README.md)、[架构说明](docs/architecture.md) 和 [测试计划](docs/test-plan.md)。

## 测试与协作

按需从根目录执行以下检查；修改 C++ 后先构建全部测试目标：

```powershell
cmake --build cpp-engine/build --config Release
ctest --test-dir cpp-engine/build -C Release --output-on-failure
.\.venv\Scripts\python.exe -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
```

运行 Python↔C++ 契约测试时，`CPP_ENGINE_PATH` 必须指向本轮编译的引擎。GitHub Actions 会构建并检查 Debug/Release C++、Python 契约、前端和 Docker 镜像；合成测试通过不等同于真实路网精度验收。

C++ 成员主责引擎与前端地图图层，Python 成员主责地图 API、任务编排、POI 和报告；`contracts/`、部署、CI 与演示材料共同维护。接口变更须同步样例、解析器、调用方和测试，贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可与数据来源

项目代码使用 [MIT License](LICENSE)。OSM 衍生预览资源的来源和 ODbL 说明见 [前端数据说明](frontend/README.md)及对应资源属性；第三方地图服务与数据的使用条件不由代码的 MIT 许可替代。
