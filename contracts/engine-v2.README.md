# 步行路网引擎契约 v2

Python 向引擎标准输入写入一个 JSON 对象；引擎向标准输出写入一个 JSON 对象。`schemaVersion` 必须为 `2`。完整输入见 `engine-input.example.json`。

- 坐标是相对于 `originBd09` 的局部米制坐标，X 向东、Y 向北。`originBd09` 只供 Python 坐标转换，引擎忽略此字段。
- 一个街区路段有左右两侧各一条 `sidewalk` 边；每条边默认双向通行。若某侧不存在可步行通道，则不创建该边。
- `turn` 连接同侧相邻人行道，耗时仅为长度除以步速。`crossing` 仅用于人工确认的合法过街位置，额外增加 `crossingWaitSeconds`。坐标相交不会自动建连。
- `originMeters` 为查询点；可选 `originEdgeId` 指定其所在侧的人行道。未指定时，引擎吸附到 30 米内最近的人行道。
- 成功结果中的 `reachableEdges` 是精确可达街段，`frontierMeters` 是 15 分钟边界点，`displayPolygonMeters` 是街段缓冲形成的近似展示面。覆盖判断不得使用展示面。
- 错误结果为 `{"schemaVersion":2,"success":false,"error":{"code":"…","message":"…"}}`。

当前 `contracts/engine-input.example.json` 是合成路网，仅供开发和联调，不代表任何真实街区。
