# 统一 walkway：类型迁移，不是路网重建

`sidewalk` 与 `shared_way` 统一为 `walkway`。Dijkstra、双向通行、节点连接规则和计时公式不变，`turn`／`crossing` 保留。节点 ID、边 ID、节点坐标、全部折线、端点、设施入口引用及过街等待均不得随迁移改变。

| 属性 | 分侧通道 | 共享通道 |
| --- | --- | --- |
| `kind` | `walkway` | `walkway` |
| `accessMode` | `separated` | `shared` |
| `streetBlockId` | 必填 | 必填 |
| `side` | `left`／`right` | 不设置 |
| `widthMeters`、`sharedWayType` | 不设置 | 保留原值 |

这不是取消接入约束：分侧通道仍禁止通过同一节点或转弯边免费换侧，POI 估算接入仍限定同侧并检查跨路直线；共享通道仍沿用宽度、中心线投影与横向距离计时。缺失 `accessMode` 的新输入应拒绝，而不是猜测可以自由穿行。

## 兼容与生成链

schemaVersion 继续为 2。C++ 和 Python 兼容旧 JSON 类型，但规范化数据、返回的可达街段和设施路线统一为 `walkway`。现有道路偏移／街角生成器的历史语义标签保留在内部，生成出口统一规范化；不会为了类型命名重建路口或产生第二套路网。

源道路图 `demoRoadGraph.local.json` 的 `roadMajor`／`roadLocal`／`roadPath` 不改，它不是引擎步行边类型。人工编辑记录的基线哈希和 upsert 边同时迁移，并检查新记录能重放出同一份路网。

```powershell
# 只检查；不写文件。
.\.venv\Scripts\python.exe backend/scripts/migrate_walkways.py
# 应用类型迁移；先备份到被 Git 忽略的 data/cache/。
.\.venv\Scripts\python.exe backend/scripts/migrate_walkways.py --write
# 后续重生成仍输出 walkway，并保留人工编辑。
.\.venv\Scripts\python.exe backend/scripts/export_synthetic_preview.py
```

契约输入样例包含 accessMode；输出样例仅在原有 kind 字段改为 walkway，设施结果与几何格式不变。查看 [引擎契约](../contracts/engine-v2.README.md)。

## 地图编辑器

图层、连线、单条和批量属性只有“步行通道 · walkway”“转弯连接”“过街连接”三种类型。接入约束作为步行通道属性保留，可查看分侧通道的左右侧，或共享通道的宽度与空间类别。仅统一类型时这些属性必须保持原值；不要把批量类型按钮当作取消过街约束。

更新后重新构建 C++，重启开发服务并刷新编辑器。旧窗口的保存版本会因路网文件迁移而失效；未保存草稿可先下载，再在新窗口导入并检查，不能强行覆盖版本冲突。

类型迁移不验证现实通行许可，现有数据继续标记为合成／未核实。测试代码仅保留本地，不随 Git 提交。
