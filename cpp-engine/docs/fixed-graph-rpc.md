# 固定图服务与 RPC 契约

2026-11-04，第二阶段源码交付。已接入服务级固定图所有权、启动预加载、`ComputeOnGraph` 和新旧方法共用计算门。第三阶段随后完成[Python 导出与轻请求衔接](../../docs/fixed-graph-python.md)，需要显式配置才启用。本文说明服务源码及待验收边界，所有命令仅供阅读，未执行。第一阶段产物格式见[快照 v1](graph-snapshot-v1.md)。

## 一份基础图与多次请求

`EngineServiceImpl` 拥有 `FixedGraphStore`，store 用 `unique_ptr<const GraphSnapshot>` 独占固定快照。构造时可以为空，以支持旧完整输入模式；构造完成后没有修改、清空、替换或加载接口。每次同步 handler 调用 `require(graph_id)` 得到 const 引用，快照一直存活到 server 关闭和销毁之后。

基础图、ID 索引和元数据跨请求只读复用。分析参数、设施、`ExecutionControl`、临时拆边图、搜索数组、几何和结果仍由各请求独立拥有。没有缓存某次计算结果，也没有预先固定与起点、速度相关的计算图。单份常驻图增加服务空闲时的常驻资源；减少重复解析/校验的收益大小需要实测，不能根据数据结构直接给出 RSS 或 QPS。

`graph_identity.hpp` 只检查 `sha256:` 加64位小写十六进制的语法。文件内容摘要仍由 `GraphSnapshotLoader` 实际计算并核对，格式正确不代表文件内容相符。

## 构建与启动入口

RPC 构建仍只支持 Linux，依赖已安装且兼容的 Protobuf/gRPC 配置包与生成器；打开加载器另需 OpenSSL 3.0+ Crypto。构建系统不自动下载依赖。

```bash
# 仅示意，需要用户明确要求后再执行配置、生成和编译。
cmake -S cpp-engine -B build-rpc \
  -DBUILD_RPC_SERVER=ON -DBUILD_GRAPH_SNAPSHOT_LOADER=ON
cmake --build build-rpc
```

这两个开关同时开启时，`isochrone_rpc_server` 链接可选 `isochrone_snapshot_io` 并定义 `ISOCHRONE_WITH_GRAPH_SNAPSHOT_LOADER=1`。只开启 RPC 时仍能构建旧模式服务，源码不引用加载器符号。client 使用共享头文件检查 ID，不链接加载器/core/OpenSSL。默认 CLI 和默认构建开关保持原样。

服务选项为：

```text
isochrone_rpc_server [--port PORT] [--compute-capacity N]
                     [--graph-artifact PATH --graph-id sha256:HEX]
```

`HEX` 必须替换成文件实际 SHA-256 的64位小写摘要。所有选项可任意排列，各至多出现一次。两个图选项必须同时出现且各有值；格式错误、缺值、重复或只提供一个会以参数错误退出。没有编译加载器却提供图选项也会明确报错。省略两个图选项保留原完整输入模式，不自动读取默认文件。

固定图模式的启动顺序是：解析参数、设置终止信号屏蔽 → Loader 有界读取/摘要核对/解析/静态验证 → 将完整快照移动到 service → 构造 builder、注册 service、监听。加载失败退出，日志为 `event=graph_load_failed`，不会开始监听或改用其他版本。成功加载记录 ID、源字节和节点/边/路径点数量、`load_ms`；该字段是未来运行时日志，本次没有测得启动耗时。

只在启动时打开产物文件。启动后修改源文件不会更新当前快照；换版需重新导出并用新 ID 重启。导出入口随第三阶段提供；没有修改实际产物配置或自动部署。

## 新方法、Health 与兼容入口

新方法签名见 [`engine_service.proto`](../rpc/engine_service.proto)：

```proto
rpc ComputeOnGraph(GraphComputeRequest) returns (GraphComputeReply);
message GraphComputeRequest {
  string graph_id = 1;
  bytes analysis_request_json = 2;
}
message GraphComputeReply {
  string graph_id = 1;
  bytes response_json = 2;
}
```

`analysis_request_json` 使用第一阶段的请求专用 v2 格式，包含参数和设施等请求数据，拒绝 nodes/edges。服务按已加载图核对请求引用，再调用快照计算入口；不会用客户端附带的数据替换图。RPC OK 时，计算成功和业务失败封套都带实际选中快照的 ID。

原 `Compute(ComputeRequest) → JsonReply` 保留，仍接收完整图并做完整校验；即使服务加载了固定图，这个旧方法也继续按完整输入工作。调用者必须明确选用新方法，不能把旧路径的测量记成快照复用结果。

原 `Health → JsonReply` 的 `response_json = 1` 与其中健康 JSON 保留。新增 `graph_snapshot = 2` 嵌套消息，仅加载快照时设置，包含 `graph_id`、`source_bytes`、`node_count`、`edge_count`、`path_point_count`。旧客户端可以继续读取字段1；这遵循 [Protobuf 新增字段的二进制兼容规则](https://protobuf.dev/programming-guides/proto3/#binary-wire-safe-changes)，不代表实际部署的运行兼容已验证。Health 不占计算名额，描述的是已加载资源；Health OK 不保证下次计算有空闲名额或在某个时限内结束。

## 共用计算门与错误

`Compute` 与 `ComputeOnGraph` 都调用 `RunComputeCall` 并传入同一个服务成员 `computing_`。容量为 N 表示两个入口合计最多持有 N 个应用处理许可；当前默认 N=2，满额直接拒绝，无新增应用等待队列。

调用顺序：检查取消/超时/drain → 取得许可 → 再检查受理状态 → 新方法选择固定图 → 检查并解析 JSON → 请求验证及计算 → JSON 序列化 → 构造和检查完整回复 → 清理局部状态并归还许可。选择图错误也经过此门，因此服务繁忙时可以先得到 ENGINE_BUSY，而不是 ID 错误。

| 条件 | gRPC 状态 | `engine-error-code` / 业务结果 |
| --- | --- | --- |
| 图 ID 缺失或格式错误 | INVALID_ARGUMENT | INVALID_GRAPH_ID |
| 合法 ID，但未加载快照 | FAILED_PRECONDITION | GRAPH_NOT_LOADED |
| 合法 ID，与常驻版本不同 | FAILED_PRECONDITION | GRAPH_VERSION_MISMATCH |
| 两个入口合计名额已满 | RESOURCE_EXHAUSTED | ENGINE_BUSY |
| 回复超过总消息上限 | RESOURCE_EXHAUSTED | ENGINE_RESPONSE_TOO_LARGE |
| 正在 drain，尚未受理 | UNAVAILABLE | 不设置专用原因码 |
| 观察到 deadline / 取消 | DEADLINE_EXCEEDED / CANCELLED | 不设置专用原因码 |
| JSON/业务参数无效，包括超出 JSON 输入限额 | OK | `success:false`、业务 INVALID_INPUT，带已选中图 ID |
| 起点业务错误 | OK | ORIGIN_NOT_ON_WALKWAY / AMBIGUOUS_ORIGIN_SIDE |
| 计算内部标准异常 | 通常 OK | 原业务 ENGINE_ERROR；回复构造等外层失败为 INTERNAL |

图选择错误在业务异常封套之外转换，服务和示例 client 均不重试、不按需加载、不自动切换旧方法。JSON 解析错误等沿用旧业务映射。RPC OK 和进程退出码0都不能代替检查 JSON 的 `success`。

应用 JSON 限额仍为20,000,000字节，服务接收完整消息上限20 MiB、发送完整消息上限64 MiB。`SetReply` 的 `ByteSizeLong()` 计入 ID/Health 元信息、字段标签和长度；失败或取消清空整个回复，成功保留元信息。这些大小约束并非进程内存硬上界，gRPC 接收解码发生在 handler/Gate 之前，返回后的传输也不由应用许可覆盖。

`ComputePermit` 在公共处理函数的外层 try 之前构造，异常清理和错误回复准备期间继续持有许可。客户端停止等待或服务观察到取消，不立即减计数；要等本次应用处理退出才释放。解析/序列化仍在阶段边界观察取消，计算内部沿用检查点，不承诺立即终止或固定取消耗时。

## 客户端与关闭

示例 C++ client 新增 `compute-on-graph --graph-id ID [--port N]`，从 stdin 有界读取请求 JSON，完整 protobuf 大小检查通过后发起新方法。deadline 仍从读完 stdin 后设置，计算25秒，Health 3秒；不包括 stdin 读取时间。RPC OK 后先核对返回图 ID，再输出原业务 JSON。RPC 失败只向 stderr 打印数值状态；目前示例 CLI 不展示 trailing metadata 原因码。

旧 `compute` / `health` 保留，二者不接受 `--graph-id`。Health 的图身份和简要规模写入 stderr，stdout 仍只有原健康 JSON。第三阶段 Python 已提供固定上下文、同源导出、轻请求 codec 和新方法调用，未配置时继续调用旧方法。

关闭顺序为 `BeginDrain → Shutdown(现有5秒宽限) → Wait → server.reset() → service/store析构`。BeginDrain 只置位，不删除快照。声明顺序使 service 比 server 活得更久，正常关闭还显式先销毁 server。已有请求保留宽限和协作取消，不把5秒当作进程必定退出的硬时限。

## 本轮证据与后续验收

已做源码、接口/调用点、CMake 依赖方向、异常/析构顺序、差异空白和独立交叉静态审阅。没有执行协议生成、配置/编译、单测/集成/冒烟、服务或压测；没有新增测试脚本。本阶段只完成源码，不能声称已证明混合并发容量、运行兼容、关闭安全或性能提升。

待用户明确授权运行后，重点检查：加载失败不监听；旧模式/新模式及错误 ID；新旧方法合计容量；业务错误、取消与超限后的许可回收；Health 元信息；关闭期间快照借用结束；新旧输出兼容。吞吐、P99 与内存另以固定版本和负载的真实测量为准，压测工具与原始记录仍放项目外临时目录。
