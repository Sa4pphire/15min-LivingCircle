# 固定图快照第一阶段：数据、加载与校验

2026-11-04。此文记录第一阶段 C++ 库接口及 JSON 产物契约。第二阶段已在源码中接入 RPC 启动预加载、`FixedGraphStore` 和新方法，见[固定图服务与 RPC 契约](fixed-graph-rpc.md)；第三阶段已提供[Python 固定上下文与导出入口](../../docs/fixed-graph-python.md)。三阶段均未运行验收，实际部署仍需显式导出、选版和配置。

## 数据和使用方式

`GraphSnapshot` 是已经完成图静态校验的只读资源，保存图身份、来源/坐标元数据、基础 `RoadGraph`、节点与边 ID 索引以及源字节数和结构数量。内部字符串和容器拥有数据，不借用 JSON 原文或解析树。对象不可复制、不可移动；移动 `unique_ptr` 只转交所有权，不移动快照本体。

只能由 `GraphSnapshotLoader` 构造快照。`ParsedGraphSnapshot` 是解析后的普通数据，不表示图已通过拓扑校验，也不能直接转成可跳过校验的计算输入。

下面展示库接口的组合关系，变量由调用方提供；这不是已经执行的调用记录：

```cpp
const auto snapshot = isochrone::GraphSnapshotLoader::load(
    artifact_path, expected_graph_id);

const auto request = isochrone::parse_analysis_request(request_json);
const auto result = isochrone::compute_reachability(
    *snapshot, request, control);
```

调用方让 `snapshot` 存活至所有同步计算返回；每次请求拥有自己的参数、`ExecutionControl` 和结果。快照计算入口复用图静态验证与 ID 索引，仍检查请求参数、局部边界引用、设施入口几何、类别与模式约束。临时拆边、邻接表、搜索和显示几何仍由每次计算单独创建。

## 产物格式

产物为 UTF-8 JSON，整份原始字节不超过 20,000,000 字节，包括所有元数据和空白。`graph_id` 格式为 `sha256:` 后接原始文件字节的 64 位小写十六进制 SHA-256；ID 由调用方从部署清单取得，不写进它自身摘要所覆盖的正文。更换换行、空白或任意字段都会产生不同身份，这是文件内容身份，不是语义去重承诺。

| 字段 | 契约 |
| --- | --- |
| `schemaVersion` | `2`，基础节点/道路使用既有 v2 字段 |
| `snapshotFormatVersion` | `1` |
| `normalizationVersion` | `1`；道路类型已经正规化为 `walkway/turn/crossing`，步行道路明确提供 `accessMode` |
| `regionId` | 非空区域标识 |
| `coordinateFrame.geographicType` | `bd09ll` 或 `wgs84ll` |
| `coordinateFrame.origin` | 有效经纬度 `lng/lat`，固定局部米制坐标的原点 |
| `sourcePackageVersionId` | 可省略；提供时是 64 位小写十六进制内容版本 |
| `synthetic` | 布尔值，标识是否为合成图 |
| `supportedCenterBoundsMeters` | 有限且有序的 `minX/maxX/minY/maxY` 范围；允许某一维相等 |
| `publicWalkableAreasMeters` | 公共步行面闭合环；非合成图必须提供，合成图可省略 |
| `supportedCenterPolygonMeters` | 可选闭合范围环 |
| `nodes/edges` | 既有图字段与限制，最多 30,000 节点、50,000 边 |

起点、速度、阈值、设施、类别、局部实验和路线目标属于请求，不能混入快照产物。详细拒绝字段与格式检查以 `parse_graph_snapshot` 为准。旧完整输入继续接受其原有字段与兼容道路类型，新格式的正规化要求不反向改变旧协议。

以下仅为格式示例，坐标和道路均为教学数据，未运行验证，也不是项目真实区域：

```json
{
  "schemaVersion": 2,
  "snapshotFormatVersion": 1,
  "normalizationVersion": 1,
  "regionId": "example-region",
  "coordinateFrame": {
    "geographicType": "wgs84ll",
    "origin": {"lng": 0, "lat": 0}
  },
  "synthetic": true,
  "supportedCenterBoundsMeters": {
    "minX": -10, "maxX": 110, "minY": -10, "maxY": 10
  },
  "nodes": [
    {"id": "n1", "xMeters": 0, "yMeters": 0},
    {"id": "n2", "xMeters": 100, "yMeters": 0}
  ],
  "edges": [{
    "id": "e1", "from": "n1", "to": "n2",
    "kind": "walkway", "accessMode": "shared",
    "streetBlockId": "b1", "sharedWayType": "pedestrian_street",
    "widthMeters": 2,
    "pathMeters": [[0, 0], [100, 0]]
  }]
}
```

范围、公共步行面和合成图信息由解析器检查并被整文件摘要覆盖，当前 C++ 快照只保留计算/身份需要的元数据，不保留这些面用于重新实现 Python 的起点接纳判断。后续 Python 接入必须从相同已核对产物固定它们，不能混用正在编辑的另一份文件。

## 加载和失败清理

加载顺序为：检查预期 ID 格式 → 有界二进制读取 → 计算原始字节 SHA-256 → 核对 ID → 解析产物 → 静态校验图并建立索引 → 返回 `unique_ptr<const GraphSnapshot>`。

读取上限依据实际读到的字节，最多额外读取一个字节以区分恰好达到上限与超限，不依据事先的文件大小作无界分配。文件流和 OpenSSL EVP 上下文均由 RAII 管理，所有 EVP 返回状态均检查。摘要不匹配、解析失败或图校验失败都不返回快照，不尝试其他文件或版本。

参数/内容错误使用 `std::invalid_argument`，文件及摘要计算故障使用 `std::runtime_error`；内存分配等标准异常保持传播。外层服务以后负责把启动失败或请求错误转成适合它的状态，此阶段不增加 RPC 错误映射。

加载期间原文、DOM、图和辅助容器可能部分重叠。DOM 在产物解析返回时释放；构造成功后，加载器返回前释放原文和一次性辅助结构。源字节限制与节点/边数量限制不是 RSS 硬上限，节点计数检查发生在 DOM 形成之后。结构统计不等于物理内存测量。

## 解析和校验的两条路径

完整输入路径保留 `parse_engine_input` / `parse_synthetic_network`。共享的图字段 helper 不再同时解析设施与类别，但旧入口继续按原先顺序组合请求字段、图、设施/类别和联合校验。

轻请求入口 `parse_analysis_request` 仍要求 `schemaVersion:2`，使用原来的请求字段、默认值和数量限制，并明确拒绝 `nodes/edges`。它只负责解析；参数和对图的引用在计算入口验证。

旧完整校验按请求数值 → 图节点 → 局部边界引用 → 图边/拓扑 → 类别/设施的顺序组合。新快照在构造时执行图静态检查；每次计算通过只读索引检查请求。设施入口不仅核对边 ID，也保留贴边投影与接入路径约束；路线模式组合和起点投影仍进入原有计算流程。

已有 `graphValidation` 阶段名称保留；在快照入口中它主要覆盖请求校验，在旧入口中仍覆盖完整图/请求校验。因此不能直接将两条路径的该阶段耗时当作同一工作量；比较时应注明入口和加载成本。

## 链接和依赖

| 目标 | 内容与依赖 |
| --- | --- |
| `isochrone_core` | 快照数据、静态/请求校验、两种计算入口；没有 JSON、文件系统、OpenSSL 或 gRPC 依赖 |
| `isochrone_json` | 完整输入、轻请求和产物解析；链接 core |
| `isochrone_snapshot_io` | 有界读文件、EVP SHA-256、私有快照构造；链接 JSON 与 `OpenSSL::Crypto` |

`BUILD_GRAPH_SNAPSHOT_LOADER` 默认 `OFF`。仅显式打开此选项才查找 OpenSSL 3.0 或以上版本的 Crypto 组件并建立加载器库；不自动下载或安装依赖。默认 CLI 和已有 RPC 入口不自动链接或启用加载器。此阶段也没有修改 RPC 协议或 Python 业务调用。

## 交付与证据边界

本阶段以源码、声明/定义/依赖和差异审阅交付。未执行 CMake 配置、依赖安装、编译、测试、程序、服务或压测。既有 RPC 与稳定性待验收状态保留，不能据此宣称新路径已经联调、运行兼容或提升吞吐。

后续明确要求运行验收时，需分别覆盖产物大小/摘要/版本/图错误、请求关联错误、旧完整输入语义、同一快照多次计算的私有状态和取消清理。并发与性能证据在服务接入后单独取得。
