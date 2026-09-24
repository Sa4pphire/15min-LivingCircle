# 15 分钟生活圈智能体检与规划助手

面向比赛演示的 Web Demo：依据新江湾城样例区的步行路网 JSON，对公共步行空间内的中心点计算 15 分钟可达街段与近似等时圈，并按设施入口路网耗时统计覆盖、标注疑似服务灰区。真实路网与设施数据仍待采集核实。

## 技术架构

- 前端：Vite + 原生 JavaScript；目前提供路网示意图，百度地图底图尚待接入
- 主服务：Python 3.12 + FastAPI + httpx
- 算法引擎：C++20 命令行程序，通过标准输入/输出交换 JSON
- 部署：Docker Compose
- 测试：pytest + CTest + GitHub Actions

```text
Browser
  -> FastAPI REST API
      -> Python-normalized walking-network JSON
      -> C++ isochrone + facility-service engine
      -> reachable walkways + approximate display polygons + candidate gray zones
```

## 当前状态

当前已建立：

- FastAPI 健康检查和分析任务接口
- C++ 双侧人行道最短路引擎、多入口设施耗时、逐类灰区及近似展示面生成
- Vite 路网、等时圈和疑似灰区示意界面
- Python/C++ 测试骨架
- Docker 多阶段构建
- GitHub Actions CI
- API 与 C++ 引擎 JSON 契约示例

真实演示区域尚无人工核实的步行路网。默认分析会返回 `UNSUPPORTED_AREA`；`contracts/engine-input.example.json` 仅是合成联调样例，不能作为上海街道数据使用。如何标注真实路网见 `data/networks/README.md`。

## 快速启动

1. 复制环境变量：

   ```bash
   cp .env.example .env
   ```

2. 若要分析真实区域，按 `data/networks/README.md` 提供已核实的路网，并设置 `WALKING_NETWORK_PATH`。百度地图 AK 将用于后续底图和 POI 功能。

3. 构建并启动：

   ```bash
   docker compose up --build
   ```

4. 打开 <http://localhost:8000>。

## 本地开发

后端：

```bash
python -m venv .venv
pip install -r backend/requirements-dev.txt
uvicorn app.main:app --app-dir backend --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

C++ 引擎：

```bash
cmake -S cpp-engine -B cpp-engine/build
cmake --build cpp-engine/build --config Release
ctest --test-dir cpp-engine/build -C Release --output-on-failure
```

Python 测试：

```bash
pytest backend/tests
```

## 协作边界

- C++ 成员主责 `cpp-engine/` 和 `frontend/` 中的地图图层。
- Python 成员主责 `backend/` 中的百度 API、任务编排、POI 和报告。
- `contracts/`、Docker、CI 和演示材料共同维护。
- 公共接口字段变更必须同时更新契约示例和测试。

## 计划节点

- 9 月 24 日：地图、FastAPI、C++ 引擎假数据链路跑通
- 9 月 30 日：真实等时圈和三类 POI 完成
- 10 月 3 日：盲区与体检报告完成
- 10 月 5 日：功能冻结
- 10 月 10 日：完成正式提交包

## License

[MIT](LICENSE)
