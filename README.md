# 15 分钟生活圈智能体检与规划助手

面向社区步行可达性分析的 Web 演示项目。用户选择区域和起点，查看 15 分钟步行范围、周边民生设施、到设施的路线及服务覆盖提示。该仓库通过加载不同区域的区域数据包来实现不同区域的分析演示。在保留较高精度计算结果的同时做到了不同区域的灵活迁移。该仓库内置上海-新江湾城演示区域包，也包含了网页版的路网编辑器，可以通过路网编辑器维护、创建和导出区域数据。

## 两种计算模式

| 模式 | 模式特点 | 算法文档 |
| --- | --- | --- |
| 在线计算 | 使用在线地图信息查看步行范围、周边设施和规划路线；需联网及地图API配置，查询速度受网络和服务配额影响 | [在线计算算法](backend/README.md) |
| 合成模拟 | 基于可编辑的本地区域路网和模拟算法进行计算，范围计算不依赖在线请求API | [合成模拟算法](cpp-engine/README.md) |



## 部署与运行

### Windows 本地运行

准备 Python 3.12+、Node.js 22，以及支持 C++20 的编译器；推荐安装 CMake 3.20+。使用 Windows 自带 PowerShell 即可。

在仓库根目录安装依赖并启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm --prefix frontend ci
.\start-demo.bat start -EngineConfiguration Release
```

首次启动会按需编译 C++ 引擎并检查前后端连通性。打开 [生活圈页面](http://127.0.0.1:5173/)；[路网编辑器](http://127.0.0.1:5173/?mode=editor)共用同一套服务。之后也可以直接双击 `start-demo.bat` 启动并打开页面。

服务在后台运行，关闭网页不会停止。常用命令：

```powershell
.\start-demo.bat status
.\start-demo.bat stop
.\start-demo.bat restart -EngineConfiguration Release
```

详细启停、端口和日志说明见 [Windows 启动说明](docs/windows-demo-launcher.md)。

### Docker 部署

准备 Docker 和 Docker Compose，在仓库根目录执行：

```powershell
# 仅在根目录尚无 .env 时复制；已有配置不要覆盖。
Copy-Item .env.example .env
docker compose up --build -d
```

Linux/macOS 可使用 `cp .env.example .env`。按需填写 `.env` 中的服务端 AK，打开 [应用页面](http://localhost:8000/)。镜像包含前端、Python 服务和 Release C++ 引擎；Compose 将本地 `data/` 挂载到容器，保留区域数据与运行缓存。

查看日志：`docker compose logs -f app`；停止：`docker compose down`。

### 地图与区域配置

| 配置 | 设置位置 | 用途 |
| --- | --- | --- |
| `BAIDU_SERVER_AK` | 根目录 `.env` 或后端进程环境 | 在线步行查询、POI 查询及相关地图服务 |
| `VITE_BAIDU_BROWSER_AK` | `frontend/.env.local`，参考[配置样例](frontend/.env.example) | 百度地图底图；需配置允许访问的域名 |
| `REGION_ID` | 根目录 `.env` 或后端进程环境 | 默认区域，初始值为 `shanghai-new-jiangwan` |

缺少浏览器 AK 时可使用本地区域地图预览；缺少服务端 AK 时，合成路网计算仍可运行，但不能发起新的在线地图查询。区域包内的 `pois.json` 保存地点快照，新江湾城包当前含 642 个去重地点，迁移后可直接读取；选定起点并完成计算后才显示范围内的地点。

前端 AK 在启动／构建时读取。Docker 部署如需百度底图，先配置 `frontend/.env.local`，再构建镜像；仅修改容器运行时的环境变量不会更新前端。**服务端 AK 不得放入任何 `VITE_` 变量，也不要提交真实密钥。** 本机不要照搬 `.env.example` 中的 `/app/...` 容器路径。

更换区域或后端配置后重启服务。区域数据格式、维护与打包见 [区域包说明](docs/region-packages.md)，手动建图见 [路网编辑器说明](docs/network-editor.md)。

## 开发入口

- [在线计算算法与接口](backend/README.md)
- [合成模拟算法与 C++ 构建](cpp-engine/README.md)
- [Python ↔ C++ 数据契约](contracts/engine-v2.README.md)
- [API 文档](http://localhost:8000/docs) · [健康检查](http://localhost:8000/api/v1/health)

项目代码采用 [MIT License](LICENSE)。OSM 衍生区域资源须保留 OpenStreetMap contributors 署名及 ODbL 来源说明；百度地图服务和数据另遵循其使用条件。
