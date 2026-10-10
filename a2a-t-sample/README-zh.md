# a2a-t-sample

`a2a-t-sample` 是 A2A-T Python SDK 的示例用例集，以用例独立目录组织，包含客户端与服务端可直接运行的入口。

当前示例基于官方 Python A2A SDK（`a2a-sdk`）运行真实的 A2A `HTTP+JSON/REST` 链路：
- `a2a-t-sdk` 客户端仅用于生成结构化 prompt
- `a2a-t-sdk` 服务端仅用于校验结构化 prompt

运行方式见仓库根目录的 [`README_zh.md`](../README_zh.md)。

## 用例清单

| 目录 | 说明 |
| --- | --- |
| [fault-management/](fault-management/) | 事件订阅样例——流式推送 Incident 通知 |
| [ran-energy-saving/](ran-energy-saving/) | 无线网络节能样例——运行 RAN 节能任务 |
| [negotiation/](negotiation/) | 协商闭环样例——离线 propose → accept 往返 |
