# Python 固定图衔接与部署输入

2026-11-04，第三阶段源码交付。前两阶段已提供 C++ 只读快照、启动加载、`ComputeOnGraph` 与共用 Gate；本阶段补齐同源导出、Python 启动准备、显式选路以及后续设施/路线调用。本文命令是待用户明确要求后执行的操作示例，本次没有执行导出、协议生成、构建、测试、服务或压测。

## 为什么需要 Python 固定上下文

原流程在每次准备分析时读取并正规化路网，发送完整 nodes/edges。后续 POI 还会按区域 ID 读取区域包和校准文件。单纯删掉请求中的图字段，会留下坐标原点、范围规则、POI 绑定几何与 C++ 常驻图不一致的可能。

新流程由 Python 和 C++ 分别有界读取**同一个图产物的原始字节**，计算 SHA-256，并核对相同配置 ID。Python 从其中固定节点/边、坐标原点、坐标类型和范围规则；匹配的分析请求不再重新读取或正规化编辑目录中的路网。C++ 仍负责图的完整静态校验和逐请求关联校验。

Python 还需要道路几何做 POI 接入估算，因此仍保留一份准备图。不能把这项改造称为“整个系统只剩一份图”。每次请求单独持有设施、类别、参数和结果，基础几何按只读约定借用；新 RPC 编码时移除 nodes/edges，不扫描、正规化或序列化整图。

## 导出的两个文件

入口为 [`export_graph_snapshot.py`](../backend/scripts/export_graph_snapshot.py)，支持区域包或单独网络文件。道路类型与字段规则复用 [`compact_graph_fields`](../backend/app/engine_protocol.py)，不另外实现一套正规化。

| 文件 | 内容与用途 |
| --- | --- |
| `graph.json` | C++ 快照 v1：规范化图、区域、坐标、范围、格式/正规化版本及可选来源包版本。原始字节的 SHA-256 是 `graph_id` |
| `graph.context.json` | Python 请求上下文：`contextFormatVersion`、所配 `graphId`、来源 `networkRevision`、原设施/类别模板、可选默认起点边、校准和 POI 文档 |

设施和类别仍属于请求，放在配套文件中以保留现有功能，未加入 C++ 静态图字段。上下文文件通过 graphId 与图配对；加载后每次复制其请求模板。图 ID 覆盖图文件自身的字节，**不等同于配套上下文文件的摘要**。`networkRevision` 则保留源网络文件的64位摘要，用于来源追踪，不能拿带 `sha256:` 的图 ID 替代它。

同次导出可保存原有 POI 校准和候选文档，Python 启动时验证并持有；后续计算不读取正在编辑的另一份校准。图路径范围模式在导出时固定实际范围，不再随着原文件变化。局部实验图不作为固定产物导出，继续使用原专用入口。

下面是命令形状，路径只是示例，不代表这些文件已经生成：

```bash
# 从已准备好的区域包导出。配套文件自动使用 graph.context.json。
python backend/scripts/export_graph_snapshot.py \
  --region-dir data/regions/shanghai-new-jiangwan \
  --output /absolute/export/graph.json

# 或从单独的网络文件导出；可显式附加 --alignment / --pois。
python backend/scripts/export_graph_snapshot.py \
  --network /absolute/source/network.json --region-id example-region \
  --output /absolute/export/graph.json
```

导出入口拒绝覆盖已有输出。先完成导出，再配置应用；更改源数据后重新导出到新路径，选择新 ID 并重启两端。文件写入失败可能留下不完整的新文件对，本工具不提供跨文件原子发布，也不自动启用配置。图和配套文件各限20,000,000字节；这不是 RSS 硬上限。Python 沿用区域局部投影的 `-85 < lat < 85` 原点范围，比 C++ 产物可表示的纬度范围更窄。

## 显式配置与启动

Python 默认行为保持原样。启用固定图需要在 gRPC 模式下成套设置：

```text
CPP_ENGINE_TRANSPORT=grpc
CPP_ENGINE_RPC_PORT=50051
CPP_ENGINE_RPC_CAPACITY=2
CPP_ENGINE_GRAPH_ARTIFACT=/absolute/export/graph.json
CPP_ENGINE_GRAPH_ID=sha256:<导出器给出的64位小写摘要>
CPP_ENGINE_GRAPH_CONTEXT=/absolute/export/graph.context.json
```

三项图设置不能只填一部分，子进程模式不接受固定图配置。Python 相对图路径按仓库根目录解析。示例 `.env` 只新增注释，没有修改实际 `.env`、依赖版本、部署容器或现有数据。固定图实例不开放网络编辑接口；编辑在另一未配置固定图的文件模式实例中完成。

C++ 服务仍只支持 Linux 构建，需要开启 `BUILD_RPC_SERVER=ON` 和 `BUILD_GRAPH_SNAPSHOT_LOADER=ON`，按[服务与 RPC 契约](../cpp-engine/docs/fixed-graph-rpc.md)用同一个 graph 文件和 ID 启动：

```text
isochrone_rpc_server --graph-artifact /absolute/export/graph.json
                     --graph-id sha256:<同一摘要> --compute-capacity 2
```

Python RPC 绑定必须根据当前 proto 重新生成；现有 [`generate_engine_rpc.py`](../backend/scripts/generate_engine_rpc.py) 已读取这份 proto，不需要手改生成代码。固定模式会检查生成消息是否支持新方法/Health 字段，过时绑定明确报配置错误。这里未执行生成或安装依赖。

应用 lifespan 在开始受理之前加载固定上下文，再通过 Health 核对 C++ 报告的已加载图 ID。没有快照、版本不一致或服务不可用时启动失败，既有清理流程仍关闭客户端资源。每次新计算的回复还会再次核对 ID，启动检查不能替代逐调用检查。

## 哪些请求使用固定图

选路发生在 [`main._load_analysis_input`](../backend/app/main.py) 读取可编辑文件之前，遵循原有区域优先级：

| 请求 | 选择 |
| --- | --- |
| 显式 `regionId` 等于产物区域 | 普通或合成分析入口使用固定上下文，区域选择优先于默认文件 |
| 显式选择其他区域 | 保留完整输入路径 |
| 文件模式无显式区域 | 产物区域必须等于 `REGION_ID`，并匹配普通/合成入口；合成入口对应 synthetic 产物 |
| MySQL 模式无显式区域 | 原流程已按默认区域选包；产物等于默认区域时使用固定上下文 |
| 局部实验、百度采样分析 | 保留各自原流程，不自动附加固定图 ID |

匹配成功后，请求的坐标和范围由产物判断；不再借当前区域文件做起点判断。选择固定路径后遇到图错误会报告错误，不尝试旧方法或换图。未匹配区域走旧路径是明确的入口选择，不是失败后的降级。

固定计算上下文覆盖分析及其后续计算。区域浏览/资产接口和独立 POI 搜索仍属于现有区域功能，未改造成整个网站的资源发布系统；部署时应将对应区域展示资产与导出版本一起管理。

## 请求生命周期与 POI

固定上下文在启动时完成文件读取和准备，`prepare_request()` 只做本次起点/范围判断、默认请求参数组合和设施/类别的私有复制。配套 POI 搜索每次返回独立记录，补充 localPoint、接入边和耗时不会修改共享候选文档。校准闭包在启动时建立并复用。Python 的 `dataclass(frozen=True)` 仅限制属性重新赋值，内部 dict/list 依靠封闭写路径及只读借用约定；现有 POI/路线消费者已按源码审阅，不能称为类型系统强制的深不可变。

`_run_analysis` 将准备阶段的 `metadata.graphId` 显式传给首算、`facilitiesOnly` 补算及覆盖重算。`PoiRouteStore` 保存该次 payload/meta，路线请求继续传入同一个 ID。`run_engine()` 不会根据全局配置给任意输入自动贴版本。

固定模式的 POI 补全优先使用上下文中的候选和预制校准，显式刷新仍可走现有缓存/在线发现；新发现的设施按同一固定几何绑定。缺预制校准时沿用固定原点的原生 BD09 或现有正向坐标转换逻辑，不读取当前区域 alignment。在线候选仍不代表经过核实的入口或完整服务清单。

`run_engine(payload, graph_id=...)` 仍进入原 `EngineRuntime` semaphore，C++ 两个方法也共用一个 Gate。取消和关闭继续采用既有本地调用清理：客户端完成取消不证明远端 CPU 已退出，服务端名额仍由服务端处理生命周期释放。这符合 [gRPC 协作取消的边界](https://grpc.io/docs/guides/cancellation/)。

## 错误、观测与证据边界

固定配置错误、旧生成绑定、图未加载或版本不一致都明确报告，不重试、不改用子进程。`GraphComputeReply.graph_id` 在业务 JSON 解码之前检查，避免接受错误版本的业务成功或失败结果。

| 固定图错误码 | 含义 |
| --- | --- |
| `INVALID_GRAPH_ID` | 请求 ID 格式无效 |
| `GRAPH_NOT_LOADED` | C++ 未加载固定图，或 Health 未报告所需资源 |
| `GRAPH_VERSION_MISMATCH` | 请求、Python 配置、C++ 常驻图或回复的身份不一致 |
| `ENGINE_CONFIGURATION_ERROR` | 固定图配置不完整、传输选择错误或生成绑定不支持新协议 |

传输诊断保留 encode/roundTrip/decode/total 和输入/输出 JSON 字节，新增 `method`、固定路径的 `graphId`；日志注明方法和图 ID。报告元数据注明 `graphId` / `engineInputMode`，路线结果也返回 graphId。`networkLoad` 在新路径主要反映请求准备，旧路径还包含文件读取/正规化，比较时必须区分工作量。Health 的规模信息通过既有 `EngineHealth.detail` JSON 暴露，不把静态规模当作实测容量。

本次完成源码与差异、接口和独立交叉静态审阅，未执行导出、生成、编译、测试、服务或压测。三个阶段的源码衔接不等于运行验收；仍需在用户明确要求后检查产物互通、匹配/错误版本、旧入口、设施与路线的同版调用、请求隔离、取消和关闭。QPS、P99、常驻内存及启动成本尚无本阶段测量，压测工具/原始记录继续放项目外临时目录。
