# C++ 等时圈引擎

该目录包含演示的纯空间计算组件。
它不得调用百度 API 或读取凭据。

## 构建

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
ctest --test-dir build --output-on-failure
```

## 约定

- 输入：标准输入上的一个 JSON 对象。
- 输出：标准输出上的一个 JSON 对象。
- 日志：仅输出到标准错误。
- 坐标：相对于路网固定坐标原点的本地米制偏移量，X 向东、Y 向北。

## 路网算法

正式计算入口使用 v2 步行图契约，见 `../contracts/engine-v2.README.md`。`engine.hpp` 定义节点、街段、连接边及引擎结果；`json_parser.cpp` 解析输入；`engine.cpp` 校验图并执行 Dijkstra。两侧人行道分别建边，右转用 `turn`，合法过街用 `crossing`。过街边额外增加 20 秒等待。所有边默认可双向通行，坐标相交不会自动连通。

精确结果是可达街段；展示面由可达街段缓冲并复用现有网格、Marching Squares 和环拼接代码生成。原 IDW 模块保留供历史实验，但不进入 v2 主计算流程。

`../contracts/engine-input.example.json` 是合成示例，不代表真实区域。真实区域数据应按 `../data/networks/README.md` 标注后再启用。
