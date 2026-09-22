# 15 分钟生活圈智能体检与规划助手

面向比赛演示的 Web Demo：基于百度地图开放能力，对指定中心点生成近似 15 分钟步行等时圈，统计菜市场、药店、小学等设施覆盖情况，并识别步行服务盲区。

## 技术架构

- 前端：Vite + 原生 JavaScript + 百度地图 JavaScript API
- 主服务：Python 3.12 + FastAPI + httpx
- 算法引擎：C++20 命令行程序，通过标准输入/输出交换 JSON
- 部署：Docker Compose
- 测试：pytest + CTest + GitHub Actions

```text
Browser
  -> FastAPI REST API
      -> Baidu Place / RouteMatrix / Geocoding
      -> C++ isochrone engine
      -> GeoJSON analysis result
```

## 当前状态

仓库骨架已建立，包含：

- FastAPI 健康检查和分析任务接口
- C++ 引擎健康检查程序
- Vite 单页演示界面
- Python/C++ 测试骨架
- Docker 多阶段构建
- GitHub Actions CI
- API 与 C++ 引擎 JSON 契约示例

空间分析和百度 API 接入将在后续里程碑中实现。

## 快速启动

1. 复制环境变量：

   ```bash
   cp .env.example .env
   ```

2. 填写百度地图浏览器 AK 和服务端 AK。

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
