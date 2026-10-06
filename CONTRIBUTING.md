# 贡献及维护

## 路网数据贡献

待补充

## python与C++的数据契约

公共请求/响应结构以及 Python 到 C++ 的消息位于 contracts/ 中。
任何契约变更都必须更新：

- 相关示例 JSON。
- Python schema 和调用方。
- 当引擎契约变更时，更新 C++ 解析器。
- 当公共结果变更时，更新前端适配。
- 同步本地验证；测试文件不提交，`backend/tests/`、`cpp-engine/tests/`、`frontend/tests/` 均已忽略。

## Definition of done

- 完成相关本地验证与构建检查，记录尚未解决的问题；CI 构建通过不代表算法已验收。
- 不提交 API 密钥或个人数据。
- 错误对用户可见，并且不会让 UI 卡在加载状态。
- 对于影响设置或行为的变更，更新 README 或文档。
