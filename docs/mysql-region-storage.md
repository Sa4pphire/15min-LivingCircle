# MySQL 区域包版本存储

更新日期：2026-10-30。首版按用户选择完整保存区域包版本，暂不将节点、道路和 POI 拆成关系表。本文命令用于后续操作说明，本轮没有执行数据库启动、建表、导入、构建、测试或服务验证。

## 存储方式与使用边界

MySQL 保存区域清单及清单引用的 JSON 文件，包括路网、POI、坐标标定、底图上下文和编辑源数据；包内可选的 `README.md` 也随版本保存。导入入口只读取显式选择的区域目录和清单指定文件，不扫描整个工作区或自动导入临时区域。

| 数据 | 首版存储职责 |
| --- | --- |
| 区域记录 | 标识区域及其当前发布版本 |
| 区域包版本 | 保存清单原始字节，以内容摘要区分不可变版本 |
| 包内文件 | 保存相对路径、内容摘要和原始字节 |

文件内容使用 `LONGBLOB` 保存。虽然文件是 JSON，首版仍保留原始字节，避免重新编码改变格式、数值表达、数组顺序或现有 SHA-256 校验和。这种结构提供完整版本存取，不支持直接用 SQL 对某一条道路进行空间查询或逐节点编辑；后续需要这类能力时，再单独设计拆表和索引。二进制列语义参见 [MySQL BLOB 文档](https://dev.mysql.com/doc/refman/8.4/en/blob.html)。

清单中的 `version` 是包的展示版本号，数据库的内容版本 ID 由完整内容计算。同样内容重复导入应复用同一版本；即使清单版本号相同，只要内容变化，也会生成新的内容版本。发布操作只切换区域的发布指针，旧版本继续保留。不同版本之间仍完整保存所有文件，首版没有增量存储、跨版本文件去重或历史清理命令。

本次迁移的是正式区域包。分析任务、计算历史、路线缓存和 Agent 会话没有因此变成数据库持久数据，仍遵循既有进程内或文件缓存机制。C++ 继续使用现有调用协议，在内存中计算，不在 Dijkstra 遍历过程中逐边查询 MySQL。

## 服务如何读取 MySQL

`REGION_STORAGE_BACKEND` 默认为 `file`，保持当前文件区域包模式。设为 `mysql` 后，后端启动时执行以下流程：

1. 从数据库取得已发布区域及其内容版本映射。
2. 按固定版本批量加载各区域包，验证文件与清单。
3. 在本次服务拥有的临时目录中生成区域包快照。
4. 让现有区域展示、计算、POI、标定和路线流程读取该快照。

每个区域在同一个服务实例中固定使用一个已发布版本。运行期间不会逐次查询数据库，也不会随数据库发布指针变化自动切图；发布新版本后，需要重启后端才能采用新版本。正常关闭时清理本次临时快照，下次启动重新从数据库加载。

临时快照是供当前程序使用的派生副本，MySQL 才是该模式的数据来源。数据库在启动加载阶段不可用、默认区域未发布或包数据校验失败时，启动应明确失败，不静默退回仓库旧文件。启动完成后数据库暂时不可用，已经加载的快照仍可用于当前实例的查询。

健康检查反映当前已加载快照能否提供服务，不代表运行期间 MySQL 持续可连接。需要判断数据库可用性时，应另外查看数据库状态。

这一过渡方式保留现有路径接口，减少首次存储迁移对算法和坐标处理的影响；代价是数据库之外仍需要一份本地快照，启动还需要下载和验证数据。大区域、多区域时应分别测量启动时间、快照磁盘和加载峰值内存，不能据此预先宣称性能提升。

MySQL 模式关闭网页编辑器。制作流程继续使用文件模式编辑器：保存本地区域包 → 导入 MySQL 新版本 → 显式发布 → 重启读取该版本的后端。首版没有增加在线数据库编辑或运行中版本热切换。

文件模式编辑器现已提供“发布到数据库”：先保存本地区域包，再从“区域与发布”发布完整版本。按钮复用同一个 MySQL 导入与发布事务，继续保留 CLI；它不会切换本地编辑器的数据源，也不会自动重启展示后端。接口和界面目前仅完成静态审阅，尚未执行实际发布。

普通 C++ 分析和局部实验结果的 `metadata.packageVersionId` 记录其数据库内容版本；区域元数据还包含 `storageBackend`。

`demo_preflight.py` 和 `precache_demo_pois.py` 也只用于 `file` 模式下制作和检查区域包。完成本地准备后再导入、发布；不要用它们修改服务启动时生成的 MySQL 临时快照。

## 配置与依赖

### 本地编辑器发布

本地编辑服务保持 `APP_ENV=development`、`REGION_STORAGE_BACKEND=file`。在根 `.env` 中填写 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER` 和 `MYSQL_PASSWORD`；空白样例见 `.env.windows.example`。这些配置仅供后端使用，网页不接收密码。文件模式仍可使用数据库写入仓储，`REGION_STORAGE_BACKEND` 控制的是服务从哪里读取区域数据。

首次使用需要安装可选驱动，并在已经存在的数据库中显式建表。以下 PowerShell 命令只作说明，本轮未执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-mysql.txt
Push-Location backend
try {
    ..\.venv\Scripts\python.exe -m app.region_store_cli init
} finally {
    Pop-Location
}
```

修改根 `.env` 后重启本地后端，编辑器再读取发布配置状态。页面显示“已配置”只表示参数与驱动存在，不代表数据库可连接、权限足够或表已初始化；这些问题会在实际发布时返回明确错误。发布失败不回滚已经保存的本地编辑，也不会自动重试。

如果使用本文的 Docker MySQL，默认内部服务名 `mysql` 无法由 Windows 进程直接连接。可选的 `deploy/mysql.local.compose.yml` 只在本机回环地址开放数据库端口：

```powershell
docker compose --env-file .env.mysql -p vct-mysql -f deploy/mysql.compose.yml -f deploy/mysql.local.compose.yml up -d
```

Windows 编辑器的根 `.env` 使用 `MYSQL_HOST=127.0.0.1`，端口默认3306，账号、密码、数据库与容器一致。需要其他本机端口时，在 Compose 的 `.env.mysql` 中设置 `MYSQL_LOCAL_PORT`，同时调整编辑器 `.env` 中的 `MYSQL_PORT`。正常生产 MySQL 配置仍不映射主机端口；无需为编辑器把数据库开放到公网。以上命令仍需已有共享 Docker 网络、已填写的数据库初始化配置及首次初始化完成。

`POST /api/v1/network-editor/publication?regionId=<id>` 只接受当前已保存文件的 `revision`。同进程保存与发布复用编辑器锁，发布前核对版本，导入全部清单文件并更新该区域发布指针。成功后返回内容版本 `versionId`，使用 MySQL 的展示后端仍须重启后才加载新版本；当前文件模式编辑服务继续读本地包。

### MySQL 展示服务

本地 Python 环境的可选依赖位于 `backend/requirements-mysql.txt`。默认文件模式不要求安装 MySQL 驱动。后端 Docker 镜像通过 `WITH_MYSQL=true` 构建参数安装可选依赖，前端镜像不受影响。

| 配置 | 用途 |
| --- | --- |
| `REGION_STORAGE_BACKEND` | `file` 或 `mysql` |
| `REGION_ID` | 默认展示和分析的区域，MySQL 模式下须有已发布版本 |
| `MYSQL_HOST` / `MYSQL_PORT` | MySQL 地址；同一 Docker 网络下使用服务名 `mysql` 和 3306 |
| `MYSQL_DATABASE` | 区域包数据库，样例为 `livingcircle` |
| `MYSQL_USER` / `MYSQL_PASSWORD` | 后端与管理命令使用的数据库账号 |
| `MYSQL_ROOT_PASSWORD` | MySQL 容器首次初始化所需的管理员密码；后端 Compose 将其覆盖为空，应用使用普通账号 |
| `BACKEND_ENV_FILE` | 独立后端容器的配置文件，相对路径以 `deploy/` 为基准 |
| `BACKEND_IMAGE` | 包含 MySQL 可选依赖的后端镜像 |

在仓库根目录创建独立配置，已有文件不覆盖：

```powershell
if (-not (Test-Path -LiteralPath .env.mysql)) {
    Copy-Item .env.mysql.example .env.mysql
}
```

填写样例中的数据库密码，并按需要加入已有服务端地图与 Agent 配置。后端若使用这份配置运行，不会自动继承另一份 `.env` 中的全部容器环境。真实密码只保存在本机或部署环境，不提交到仓库。

`--env-file .env.mysql` 用于 Compose 解析变量；`BACKEND_ENV_FILE=../.env.mysql` 则选择注入后端容器的环境文件。两者承担不同职责，均已体现在样例配置中。

## 准备数据库与后端镜像

以下示例在仓库根目录的 PowerShell 中执行，只是使用说明。若上一阶段已创建 `vct` 网络，直接复用，不必重复创建：

```powershell
docker network create vct
docker compose --env-file .env.mysql -p vct-mysql -f deploy/mysql.compose.yml up -d
docker build --build-arg WITH_MYSQL=true -t vct-backend:mysql .
```

数据库配置使用 MySQL 8.4、持久数据卷和共享的内部 Docker 网络，默认不发布主机 3306 端口。数据库容器完成初始化并可用后，再执行下节管理命令。初始密码或数据库设置通常仅在空数据卷初始化时生效；修改环境文件不等同于修改已有数据库账号。

当前导入器对单文件设置 32 MiB 上限，MySQL Compose 将 `max_allowed_packet` 配为 128 MiB，为客户端传输时的编码开销留出空间。这些是首版明确的输入与部署限制，`LONGBLOB` 类型本身的理论容量不代表本项目可接受同样大的文件。需要超过限制的数据时，应先审阅实际包大小与加载成本，再调整实现和配置。

## 建表、导入与发布

管理命令使用 `python -m app.region_store_cli`，不会启动 FastAPI 或计算引擎。首次使用显式初始化表结构：

```powershell
docker compose --env-file .env.mysql -p vct-backend -f deploy/backend.compose.yml run --rm --no-deps backend python -m app.region_store_cli init
```

选定一个已有的完整有效区域包，导入并发布：

```powershell
docker compose --env-file .env.mysql -p vct-backend -f deploy/backend.compose.yml run --rm --no-deps backend python -m app.region_store_cli import /app/data/regions/shanghai-new-jiangwan --publish
```

这里的 `/app/data/...` 是后端容器内挂载的数据路径。导入结果包含 `region_id`、`version_id`、`package_version`、`created` 和 `published`；保存其中的 `version_id`，供后续发布、回退和导出使用。`package_version` 是清单中的展示版本号，不能代替内容版本 ID。导入会核对清单、文件校验和及区域数据；草稿或不完整区域不作为正式发布数据。原始本地区域包保留，不由导入命令改写。

也可以先导入但不发布，审阅记录后再明确切换：

```powershell
docker compose --env-file .env.mysql -p vct-backend -f deploy/backend.compose.yml run --rm --no-deps backend python -m app.region_store_cli import /app/data/regions/shanghai-new-jiangwan

$regionVersion = '替换为导入输出中的 version_id'
docker compose --env-file .env.mysql -p vct-backend -f deploy/backend.compose.yml run --rm --no-deps backend python -m app.region_store_cli publish shanghai-new-jiangwan $regionVersion
```

发布命令输出 `region_id` 和 `version_id`。查看发布列表只读取区域与版本记录，不扫描或返回全部大文件内容：

```powershell
docker compose --env-file .env.mysql -p vct-backend -f deploy/backend.compose.yml run --rm --no-deps backend python -m app.region_store_cli list
```

发布记录准备好后再启动后端，前端仍通过原来的 API 使用区域：

```powershell
docker compose --env-file .env.mysql -p vct-backend -f deploy/backend.compose.yml up -d
```

若后端已经运行，数据库中的新发布指针不会触发容器重建。需要显式重启后端，使其重新固定发布映射；重启会结束原进程中的任务与会话：

```powershell
docker compose --env-file .env.mysql -p vct-backend -f deploy/backend.compose.yml restart backend
```

## 导出与回退

将指定内容版本恢复成完整区域目录：

```powershell
$regionVersion = '替换为需要导出的内容版本ID'
docker compose --env-file .env.mysql -p vct-backend -f deploy/backend.compose.yml run --rm --no-deps backend python -m app.region_store_cli export shanghai-new-jiangwan $regionVersion /app/data/region-exports/shanghai-new-jiangwan
```

导出目标目录必须尚不存在；已存在但为空的目录也不接受。导出结果包含原始 `manifest.json`、全部清单文件及已保存的可选 README；原字节与摘要用于核对数据是否完整。

继续采用 MySQL 时，可将区域发布指针切回已有旧内容版本，再重启后端。切换到文件模式时，先导出需要保留的数据库版本，放入文件模式所用区域目录并确认 `REGION_ID`，然后改用 `REGION_STORAGE_BACKEND=file` 重建后端容器。仅修改配置不会自动把数据库的新数据同步回仓库旧文件。

按版本导出便于移交和回退，但不替代完整数据库备份。本轮未删除旧版本、执行恢复或验证备份。

## 当前交付状态

本轮交付包含仓储源码、初始化 SQL、管理命令、可选依赖、部署样例及应用读取入口的衔接。运行时采用启动固定快照，保留现有计算口径和 API 使用方式。未执行数据库连接、导入导出、镜像构建、启动、测试或性能测量；因此功能、运行兼容和容量结论仍待明确授权后的实际证据。
