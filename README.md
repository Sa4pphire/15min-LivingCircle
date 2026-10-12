# Vision China Terminal（VCT）

<img width="1393" height="220" alt="QQ20261010-124208" src="https://github.com/user-attachments/assets/b22a5c45-8fc3-4965-83aa-dd90fc563a1d" />

Vision China Terminal（中国观察终端）是面向社区步行可达性分析的 Web 演示项目。用户选择区域和起点，查看 15 分钟步行范围、周边民生设施、到设施的路线及服务覆盖提示。该仓库通过加载不同区域的区域数据包来实现不同区域的分析演示。在保留较高精度计算结果的同时做到了不同区域的灵活迁移。该仓库内置上海-新江湾城演示区域包，也包含了网页版的路网编辑器，可以通过路网编辑器维护、创建和导出区域数据。

## 两种计算模式

| 模式 | 模式特点 | 算法文档 |
| --- | --- | --- |
| 在线计算 | 使用在线地图信息查看步行范围、周边设施和规划路线；需联网及地图API配置，查询速度受网络和服务配额影响 | [在线计算算法](backend/README.md) |
| 合成模拟 | 基于可编辑的本地区域路网和模拟算法进行计算，范围计算不依赖在线请求API | [合成模拟算法](cpp-engine/README.md) |


## 部署与运行

前端、Python 后端和 C++ 计算引擎都在评审电脑上运行，通过 localhost 访问，地图 AK 由评审自行配置。可以使用 Docker 统一构建运行环境，也可以使用 Windows BAT 启动。以下命令在仓库根目录的 PowerShell 中执行。

AK是百度地图的API密钥，需要用户自行在开发者平台申请并按照文档补充。否则将无法使用在线计算路线服务，仅能通过合成计算模式体验VCT。

### 方式一：Docker 本地运行

安装并启动 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，使用 Linux containers，Docker Compose 需 2.24+。Python、Node.js 和 C++ 引擎在镜像内构建，评审电脑只需准备 Docker 运行环境。首次构建需要联网下载镜像和依赖。

#### 1. 配置 AK

根目录配置文件不存在时，从空白样例创建：

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item .env.example .env
}
```

在根目录 `.env` 填写：

| 配置项 | 用途 |
| --- | --- |
| `BAIDU_SERVER_AK` | 服务端在线步行计算和地图查询 |
| `VITE_BAIDU_BROWSER_AK` | 百度底图和地点定位，需允许 `localhost` / `127.0.0.1` 来源 |
| `REGION_ID` | 默认区域，初始值为 `shanghai-new-jiangwan` |
| `LOCAL_PORT` | 本机访问端口，默认 `8000` |

浏览器 AK 在镜像构建时读取，服务端 AK 在容器启动时读取。Docker 构建会排除本地私密配置文件，只将浏览器 AK 传给前端构建。AK 留空时可用“合成模拟”和包内地点快照演示。

#### 2. 构建与启动

```powershell
docker compose up --build -d
docker compose ps
```

构建会生成前端和 Release C++ 引擎。启动后等待状态变为 `healthy`，打开 [生活圈展示](http://localhost:8000/)；[路网编辑器](http://localhost:8000/?mode=editor)使用同一套服务。设置其他 `LOCAL_PORT` 时，访问地址使用对应端口。

Compose 将 `./data` 挂载到容器，区域修改和缓存保存在项目目录。迁移时保留完整的 `data/regions/`；ZIP 区域包需先解压到对应目录。修改 AK、默认区域或源码后，再执行 `docker compose up --build -d`，然后刷新页面。

```powershell
docker compose logs -f app
docker compose down
```

第一条命令查看日志；第二条停止本次演示。服务端与浏览器 AK 的含义也见下方 Windows 配置说明。

### 方式二：Windows 本地运行

#### 在评审电脑上运行

##### 1. 准备运行环境

- Python 3.12 或以上。
- Node.js 22.12 或以上的 22.x 版本，包含 npm。
- 支持 C++20 的编译器，例如已加入 PATH 的 MinGW `g++`。如使用 CMake，需安装 CMake 3.20+ 及匹配的编译工具链。

首次安装依赖需要联网：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm.cmd --prefix frontend ci
```

##### 2. 配置地图 AK

从空白样例创建配置文件；已有文件时保留原配置：

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item .env.windows.example .env
}
if (-not (Test-Path -LiteralPath frontend/.env.local)) {
    Copy-Item frontend/.env.example frontend/.env.local
}
```

用文本编辑器填写：

| 配置文件 | 配置项 | 用途 |
| --- | --- | --- |
| 根目录 `.env` | `BAIDU_SERVER_AK` | 在线步行计算、路线查询和按需 POI 检索 |
| `frontend/.env.local` | `VITE_BAIDU_BROWSER_AK` | 百度底图和编辑器中的地点定位；需允许实际使用的 `localhost` / `127.0.0.1` 来源 |
| 根目录 `.env` | `REGION_ID` | 默认区域，初始值为 `shanghai-new-jiangwan` |

本地配置使用 [.env.windows.example](.env.windows.example)，引擎和区域文件路径由启动器自动解析。服务端 AK 只填写 `BAIDU_SERVER_AK`，前端文件只填写浏览器 AK；真实密钥不随仓库提交。

AK 留空时，可用本地区域地图和“合成模拟”演示。区域包内的 `pois.json` 保存地点快照，新江湾城包当前含 642 个去重地点，迁移后可直接读取。在线计算、百度在线路线和百度底图需要对应的 AK 与网络连接。

##### 3. 双击启动

完成首次安装和配置后，双击仓库根目录的 `start-demo.bat`。脚本会按需编译引擎、启动前后端并检查计算链路，成功后自动打开生活圈展示页。

- [生活圈展示](http://127.0.0.1:5173/)
- [路网编辑器](http://127.0.0.1:5173/?mode=editor)：共用已经启动的服务，按需手动打开。
- [后端健康检查](http://127.0.0.1:8000/api/v1/health)

也可在终端使用以下命令，启动后手动打开上面的页面：

```powershell
.\start-demo.bat start
.\start-demo.bat status
.\start-demo.bat stop
.\start-demo.bat restart
```

服务在后台运行，关闭网页不会停止；结束演示后执行 `stop`。修改 AK 或默认区域后，再次双击脚本或执行 `restart`，然后刷新网页。启动失败时查看窗口提示和 `data/cache/demo-*.log`；端口 8000 / 5173 冲突及其他参数说明见 [Windows 启动说明](docs/windows-demo-launcher.md)。

##### 4. 迁移到另一台电脑

复制或克隆项目源码，保留 `frontend/package-lock.json` 和完整的 `data/regions/` 区域数据。新电脑按上述步骤创建 `.venv`、安装前端依赖并填写自己的 AK。原电脑的虚拟环境、依赖目录和密钥无需复制。

自定义区域将完整目录放到 `data/regions/<id>/`，可在页面选择；需要更改默认区域时，在 `.env` 设置 `REGION_ID=<id>` 并重启。ZIP 区域包需先解压到对应目录。选定起点并完成计算后，页面只显示范围内的地点。

区域数据格式、维护与打包见 [区域包说明](docs/region-packages.md)，手动建图见 [路网编辑器说明](docs/network-editor.md)。

<img width="1261" height="999" alt="QQ20261010-124504" src="https://github.com/user-attachments/assets/9146381d-2e70-41e1-961c-667809c82826" />

## 开发入口

- [在线计算算法与接口](backend/README.md)
- [合成模拟算法与 C++ 构建](cpp-engine/README.md)
- [Python ↔ C++ 数据契约](contracts/engine-v2.README.md)
- [API 文档](http://localhost:8000/docs) · [健康检查](http://localhost:8000/api/v1/health)

项目代码采用 [MIT License](LICENSE)。OSM 衍生区域资源须保留 OpenStreetMap contributors 署名及 ODbL 来源说明；百度地图服务和数据另遵循其使用条件。
