# 生活圈 Agent：配置、执行流程与边界

日期：2026-10-25。

本次增加一个查询型生活圈助手：在用户完成地图选点和生活圈计算后，回答“附近有哪些医疗设施”“最近的医院怎么走”“去这家怎么走”等问题，并复用已有地图路线展示。模型 API 由用户自行提供；AI 负责实现，用户审阅设计、关键代码和已有证据。

本次仅做源码和差异审阅，未运行测试、构建、服务、实际模型请求或地图请求。下文描述的是实现设计和配置约定，不代表已通过运行验收；文末列出需要另行明确授权才能执行的验收场景。

## 1. 配置模型 API

将以下配置块合并到根目录 `.env`，保留原来的百度地图和引擎配置。本次提交不包含 `.env*` 文件，配置说明中的空白值由使用者在本地填写。后端从根 `.env` 和服务端进程环境读取配置；不要求把密钥发到聊天里。

```dotenv
AGENT_ENABLED=false
# 填写 API 根地址，例如包含 /v1；程序追加 /responses
AGENT_API_BASE_URL=
AGENT_API_KEY=
AGENT_MODEL=

AGENT_TIMEOUT_SECONDS=90
AGENT_MODEL_TIMEOUT_SECONDS=25
AGENT_MAX_CONCURRENCY=2
AGENT_MAX_MODEL_CALLS=4
```

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `AGENT_ENABLED` | `false` | 显式启用可选助手 |
| `AGENT_API_BASE_URL` | 空 | Responses API 基础地址，通常以 `/v1` 结尾 |
| `AGENT_API_KEY` | 空 | 服务端模型 API 密钥 |
| `AGENT_MODEL` | 空 | 服务商实际支持的模型名称 |
| `AGENT_TIMEOUT_SECONDS` | `90` | 一次聊天请求的总时间预算，含模型与工具执行 |
| `AGENT_MODEL_TIMEOUT_SECONDS` | `25` | 单次模型请求的时间预算，仍受总预算限制 |
| `AGENT_MAX_CONCURRENCY` | `2` | 单个后端进程同时处理的聊天请求上限 |
| `AGENT_MAX_MODEL_CALLS` | `4` | 单轮对话最多请求模型的次数 |

`AGENT_API_BASE_URL` 应填写基础地址，例如 `https://model.example.com/v1`；程序会在其后追加 `/responses`，不要填写完整的 `/responses` 接口地址。所选服务及模型必须支持 Responses API 的函数工具调用和无服务端存储的连续调用；请求使用 `store: false` 和 `reasoning.encrypted_content` 保留相应关联。仅兼容 Chat Completions 文本接口的服务不满足当前实现要求；不支持协议时显示错误，不能把未执行的工具当成已执行。

DeepSeek 的官方根地址 `https://api.deepseek.com` 与模型 `deepseek-flash` 可用于 Responses，不必强行补 `/v1` 或更换模型名。其官方兼容说明指出 `include`、`store` 等未支持的顶层参数会被忽略，响应固定 `store: false`；不能仅凭存在这些字段就判定请求失败。详见 [DeepSeek Responses 文档](https://api-docs.deepseek.com/guides/responses_api/)。

DeepSeek 默认开启思考模式，而强制工具选择 `tool_choice: required` 在思考模式下会被拒绝。针对主机名严格等于 `api.deepseek.com` 的配置，后端在本轮每次 Responses 请求中加入 `reasoning: {effort: "none"}`，按其协议关闭思考模式，继续保留首轮必须取证和后端事实校验。其他服务不自动增加这一参数；代理地址也不会仅凭模型名被识别为 DeepSeek。不修改真实 `.env` 或切换模型，不通过自动重试消耗额外调用。这里的取舍是首版简单设施查询使用非思考模式，未来复杂建模助手需要单独设计推理与工具选择策略。[DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[强制工具选择限制](https://api-docs.deepseek.com/api/create-chat-completion/)

工具的模型侧 Schema 保留类型、枚举、必填和额外字段约束，不发送 `minLength/maxLength`；DeepSeek 严格模式未支持这两项。关键词最多120字符、设施ID非空且最多255字符等校验仍由后端执行，移除模型侧限制不会放宽实际工具操作权限。[DeepSeek 工具约束说明](https://api-docs.deepseek.com/guides/tool_calls/)

模型请求使用已有 `httpx`，不引入新的模型 SDK 依赖。模型密钥只由后端读取，不能使用 `VITE_` 前缀、写进 `frontend/.env.local` 或发送到浏览器。示例中的密钥和地址留空，启用前自行填写。配置改变后重启后端；Windows 启动方式和 Docker 方式仍按项目 README 操作。Docker Compose 已通过 `env_file` 将根 `.env` 传入后端容器，无需为模型密钥增加前端构建参数。

关闭助手或缺少模型配置时，原有区域选择、生活圈分析和路线查询继续沿用现有入口。状态接口只返回是否启用、是否配置齐全；不会返回密钥，也不主动请求模型来验证凭据。

### 出现模型请求错误时

错误提示会包含真实上游 HTTP 状态，以及可安全展示的错误 `code/type/param`；日志使用 `agent_provider_error` 标记，并记录经过限制的请求 ID 和已知参数线索。上游的原始错误正文、密钥、请求头和用户输入不直接显示或记录，错误体读取上限为64 KiB。

旧版“模型请求被拒绝或服务异常”把多种上游错误统一变成了本地502，仅凭这句话不能确定是模型名称、参数、额度还是服务故障。新的本地502也不等于上游502，应以提示中的“上游 HTTP”字段为准。配置齐全不等于密钥、额度和实际服务已经验证；修改 `.env` 后须重启后端，不能依据运行进程未加载的新文件值断言服务当前配置。

## 2. 使用流程与结果口径

1. 选择区域、计算模式和起点，等待当前生活圈分析完成。
2. 打开对话入口，询问当前生活圈内的医疗设施或路线；尚未计算时先提示用户完成地图选点和计算。
3. 后端读取该次分析，模型选择受约束工具获取设施、比较路线。最终事实文案及卡片由后端根据工具产物组织，不直接展示模型自由编写的设施、耗时或转向结论。设施 ID 和路线以服务器保存的分析为准。
4. 界面显示文字、候选设施和比较结果；需要显示路线时，复用服务器返回的现有完整路线绘制。

“医院”在首版对应当前分析中的医疗类别，包含社区医疗中心。查询范围是当前已完成的生活圈，不会自动扩展为全市地点搜索，也不会把面内候选都视为已经确认 15 分钟可达。

“最近”按成功取得的路线步行耗时比较，不以直线距离替代。每次最近查询最多请求 8 条候选路线，路线阶段最多使用 30 秒；只有部分候选取得路线、候选清单不完整或路线阶段预算耗尽时，说明已比较的范围，不宣称找到全区域绝对最近的医院。未取得路线的设施不能编造耗时；路线超过 15 分钟时明确标注超出预算。

在线模式复用百度按需步行路线，合成模式复用 C++ 路网路线。回答保留当前模式、数据来源和未核实状态。模型只读取设施和路线摘要，地图展示使用后端取得的路线几何。首版不生成没有来源的“前行若干米再左转”等逐步指令；可给出已取得的距离、耗时和地图路线。

## 3. 接口与上下文

| 接口 | 行为 |
| --- | --- |
| `GET /api/v1/agent/status` | 返回 `{enabled, configured}`，用于界面显示助手配置状态 |
| `POST /api/v1/agent/chat` | 接收问题及当前分析上下文，执行有界模型／工具循环并返回回答和地图动作 |

聊天请求包含：

```json
{
  "message": "最近的医院怎么走？",
  "context": {
    "regionId": "shanghai-new-jiangwan",
    "mode": "synthetic",
    "analysisId": "当前已完成分析的 ID",
    "resultRevision": 1
  }
}
```

连续追问时携带上一轮返回的 `conversationId`。`context.mode` 只接受 `real` 或 `synthetic`；`resultRevision` 使用当前分析实际返回的版本，示例值不代表固定版本。

响应提供 `reply`、`conversationId`、`context`、`facilities`、`comparison`、`mapAction`。模型完成工具循环后，后端从工具结果生成事实文案、设施与比较数据。路线地图动作携带现有完整路线；模型只见摘要，不负责生成折线坐标。没有合法路线时不返回虚构的路线动作。

上下文绑定区域、模式、分析 ID 和结果版本，后端需要再次核对。切换区域、起点、计算模式或分析结果后清空旧对话及其地图动作；旧请求迟到的响应不能覆盖新地图。会话过期后要求开启新对话，不把旧设施 ID 静默套用到新的生活圈。

## 4. 执行边界与取舍

- **模型与工具分工。** 模型负责理解问题和选择工具，后端负责事实文案、设施卡片和地图动作。首轮要求工具调用；若服务忽略这一要求，没有实际工具产物时返回错误。这样牺牲部分自由表达，避免模型即使调用摘要后仍编造路线结论。
- **有界请求。** 一轮默认最多 4 次模型请求，工具调用上限为 6 次，并受默认 90 秒总预算约束；模型次数与时间可用上述配置收紧或调整。最近查询另有 8 条路线和 30 秒阶段上限，不能把 25 秒单次模型预算误认为整轮总耗时。
- **共享计算资源。** 聊天有独立并发上限；其中的合成路线仍使用现有 C++ 业务预算，不另开无预算的引擎通道。资源繁忙、模型超时或路线失败时返回可理解的错误／部分结果，不能无限等待。
- **轻量会话。** 同一后端进程最多保存 100 个会话，空闲 600 秒后过期，保留最近 6 轮。会话存于内存，服务重启后失效；这些数量是设计上限，尚无本轮容量或性能实测证据。
- **取消与关闭。** 客户端取消时终止本轮；服务关闭在首个等待前关闭助手、分析与直接引擎准入，再并行清理。沿用既有共用 5 秒排空窗口，子进程真实回收可能继续消耗时间；90 秒总预算及 30 秒路线阶段预算也不代表强行截断底层清理。
- **可选依赖。** 缺少模型配置不影响原功能；只有用户开启聊天才需要实际的模型请求。`store: false` 是模型请求参数，不等于对第三方服务的数据政策作保证。
- **查询权限。** 当前聊天不开放路网写入、区域创建、补边或自动修复。OSM 建图和建模 Agent 仍按[独立研究方案](offline-road-generation-editor-design.md)推进，避免查询对话直接改动源路网。

## 5. 关键代码阅读顺序

沿“用户提问 → 模型选工具 → 后端查路 → 回传结果 → 地图展示”阅读：

- [接口：agent_chat](F:/OSChina/15min-LivingCircle/backend/app/agent_api.py:24)：接受问题和上下文，客户端取消时取消工具循环。
- [请求参数：AgentRuntime._model_payload](F:/OSChina/15min-LivingCircle/backend/app/agent_runtime.py:168)：组装 Responses 请求，对 DeepSeek 官方地址关闭思考模式以兼容首轮强制工具调用。
- [执行循环：AgentRuntime._tool_loop](F:/OSChina/15min-LivingCircle/backend/app/agent_runtime.py:228)：绑定当前分析，保留 Responses 工具关联，限制模型和工具次数；会话在 `chat` 中有界受理及提交。
- [最近医疗设施：AnalysisTools._nearest_medical](F:/OSChina/15min-LivingCircle/backend/app/agent_tools.py:310)：直线距离只决定探查顺序，结果按成功路线步行耗时选择，明确部分比较与超过阈值。
- [地图动作：showAgentRoute](F:/OSChina/15min-LivingCircle/frontend/src/App.vue:215)：校验上下文并调用纯坐标适配函数，直接安装已经取得的路线，不再发起路线请求。

工具循环中的代表性代码是：

```python
items.extend(outputs)
items.append({"type": "function_call_output", "call_id": call_id,
              "output": json.dumps(result, ensure_ascii=False, allow_nan=False)})
```

第一行保留本轮模型的完整输出，包括推理项和函数调用；第二行用同一个 `call_id` 把应用执行的结果交回模型。`store: false` 时不能仅保留一句回答或只记住函数名。最终给用户的数值、设施与绘图动作仍来自后端工具产物。

已有路线能力的计算入口保留：

- [合成 POI 路线：PoiRouteStore.route](F:/OSChina/15min-LivingCircle/backend/app/poi_routes.py:143)：检查路线如何复用分析图和起点。
- [在线 POI 路线：route_sampled_poi](F:/OSChina/15min-LivingCircle/backend/app/sampled_poi_routes.py:38)：检查按需查询和已有路线复用。
- [主页面](F:/OSChina/15min-LivingCircle/frontend/src/App.vue)：检查分析上下文、选中设施和地图路线的关联。

审阅时重点确认：上下文来自哪里、谁执行工具、失败是否可见、切换地图时怎样丢弃旧响应，以及最终事实文案是否只使用真实返回值。

## 6. 待明确授权后执行的验收

以下是验收清单，本次未执行；也没有用启动程序、实际请求或构建替代测试。

- 配置关闭、配置缺失、服务端地址错误、鉴权失败及模型不支持工具调用时，原地图功能保持可用，助手给出对应提示。
- 未选点、分析未完成、分析已清理、区域／模式／结果版本不匹配时，禁止查询旧上下文。
- 两种模式分别询问“最近的医院怎么走”，核对医疗候选、真实耗时、地图折线和超过 15 分钟提示。
- 候选未核齐、部分路线失败、8 条路线／30 秒预算触顶时，回答准确限定比较范围，不把缺失当作不存在。
- 连续追问、同名设施、会话过期、切换地图和旧请求迟到时，设施与路线不会串到另一份分析。
- 并发或模型／工具调用预算耗尽、取消和超时场景中，不泄漏聊天许可，也不突破现有 C++ 进程预算。
- 使用用户实际提供的兼容模型服务做端到端验收后，再记录真实行为；性能、容量和费用另行采样评估，不能从一次成功对话推导。
