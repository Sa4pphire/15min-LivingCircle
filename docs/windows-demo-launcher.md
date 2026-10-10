# Windows 演示服务启停

双击仓库根目录的 `start-demo.bat`，会直接启动服务并自动打开生活圈展示页，无需选择菜单：

- 生活圈展示：`http://127.0.0.1:5173/`
- 路网编辑器：`http://127.0.0.1:5173/?mode=editor`（服务已就绪，按需手动打开）

两页面共用前端 5173 / 后端 8000，只启动一套服务。双击时会重启启动器管理的服务，
让最新代码和区域配置生效；全部启动检查成功后才打开展示页。启动失败时保留窗口显示错误。
服务在后台运行，关闭浏览器不会停止服务；运行 `start-demo.bat stop` 可停止。

以下命令在仓库根目录执行；使用 BAT 的绝对路径时，也可从其他目录调用：

```bat
start-demo.bat start
start-demo.bat status
start-demo.bat stop
start-demo.bat restart
```

带参数运行时保留原有命令行用法，不自动打开浏览器；`start`、`restart` 启动的服务同时支持展示与编辑页面。
API 默认在 `127.0.0.1:8000`。
启动器仅管理自身创建并记录了 PID、创建时间和可执行路径的进程。会检查 Python
虚拟环境启动器的子进程，避免只停止外层进程而留下后端。

## 首次运行

需要已安装的 Python 虚拟环境、前端依赖、Node.js 和 C++ 编译器。脚本不安装依赖，
不联网下载工具，不修改系统执行策略。BAT 的 `ExecutionPolicy Bypass` 仅适用于本次
PowerShell 进程。PS1 使用 UTF-8 带 BOM，可由 Windows PowerShell 5.1 或 PowerShell 7 运行。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
npm --prefix frontend ci
```

引擎缺失或源码、头文件比可执行文件新时，启动器自动构建。优先使用 CMake；
当前引擎无额外链接依赖，缺少 CMake 时可以使用 PATH 中的 MinGW `g++` 直接编译。
旧 GCC 使用 `-std=c++2a`，较新 GCC 使用 `-std=c++20`。编译产物放在已忽略的
`cpp-engine/build/demo-launcher/`，不覆盖手工构建的引擎或其 CMake 缓存。
还会用局部实验契约检查 `localGrayZones`，不会只凭 `--health` 认为旧引擎兼容。

强制重编译并重启：

```bat
start-demo.bat restart -BuildEngine
```

保留原来的前台调试方式：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-demo.ps1 -Action run
```

前台模式按 Ctrl+C 清理本次启动的服务；不要直接结束 PowerShell 进程。若被强制结束，
仍可用 BAT 的停止命令清理已登记进程。

## 路网与坐标

默认沿用当前项目的统一数据源 `data/networks/synthetic-preview.json`，同时联调
900 秒合成分析和 180 秒局部实验。该文件是未经实地核实的合成路网，目前缺少设施
及经核实的裁剪出口；不能据此声称真实设施匮乏。启动器不生成、修改或补造路网。

如需小型三色契约样例，显式指定文件；该文件使用 BD-09 起点，而默认现有路网使用
WGS-84，页面坐标类型和起点也应随之调整，不能只修改坐标类型标签。

```bat
start-demo.bat restart -LocalNetworkPath "contracts/engine-local-experiment.input.example.json"
```

指定其他局部文件也用 `-LocalNetworkPath`；已有 `LOCAL_EXPERIMENT_NETWORK_PATH` 环境
变量会被保留使用，未设置时才共用 `SYNTHETIC_NETWORK_PATH`。自检选择文件声明的
演示中心或真实边上的中点／端点。特殊选区建议在 JSON 中声明 `demoCenterWgs84`
或 `demoCenterBd09`，并提供有效 `originEdgeId`。

## 端口冲突与配置更新

8000／5173 被手工启动的服务占用时，启动器明确报错，既不接管也不停止它们。
请在原终端停止旧服务，或者用另一组端口启动；代理会自动指向同组后端。
同一组启动器进程、路网及代码未变时，重复 `start` 不会再次启动；检测到配置
或源码变化时要求 `restart`，防止继续使用旧引擎和旧后端。

```bat
start-demo.bat start -BackendPort 8129 -FrontendPort 5182
start-demo.bat status -BackendPort 8129 -FrontendPort 5182
start-demo.bat stop -BackendPort 8129 -FrontendPort 5182
```

更换端口后，启停命令都必须带同一组端口。手工 `npm run dev` 的 Vite 配置不变，
只有启动器专用的 `frontend/scripts/demo-vite.mjs` 支持该代理覆盖，并使用独立的
Vite 依赖缓存，不与手工启动的开发服务共用预构建缓存。

## 日志和自检

日志与进程登记位于 `data/cache/demo-*`，均已由仓库现有规则忽略。
启动失败会清理本次创建的进程，显示具体错误及日志目录，并返回非零退出码。
不会按进程名或仅凭端口杀进程；PID 被复用时也不会停止新的无关进程。

启动自检经过 Vite `/api` 代理，分别调用合成分析及局部实验，再检查任务结果。
单次分析等待 35 秒，以涵盖引擎的 25 秒调用上限。前后端就绪检查使用实际经过时间，
并在 PowerShell 5.1 使用 `UseBasicParsing`，避免依赖旧 IE 解析器。

不启动服务的依赖／契约检查：

```bat
start-demo.bat check
```

`check` 不自动编译；引擎过期时提示通过 `start` 或 `restart -BuildEngine` 构建。
