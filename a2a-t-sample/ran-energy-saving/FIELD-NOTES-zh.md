# 现场笔记 —— 真机网管集成

交付 live 剖面的现场操作说明。此前关于 offline/scripted、OSS 代理、MIE 审批和观测控制台的
演示期笔记不再适用。

## 第一次运行前

1. 复制 `env.example` 为 `.env`：填写模型端点、`A2AT_SAMPLE_NMS_*`、回调公网 URL，以及使用
   gateway 时的 gateway 公网 URL。
2. 保持 `A2AT_SAMPLE_LONG_TASK_TRACKING=1`；真机 Task 经常超过同步观察窗口。
3. 干净重启：先 `python run_demo.py --stop`，再按目标模式启动。不要复用另一套配置启动的进程。

## 需要收集的证据

- `RUN_DIR/.env`、`RUN_DIR/pids.json` 与各进程日志；
- `runs/<runId>/wire.jsonl`（逐帧原始 A2A 报文）；
- 使用本地参考服务端时的 `runs/<runId>/server-trace.jsonl`；
- run id、实例/端口/模型、运维原文与观察到的 Task-T。

## 常见症状

| 症状 | 排查 |
|---|---|
| UI 起来了，gateway 没起来 | gateway 公网 URL 与 `--gateway-port` 是否可用 |
| 收不到网管推送 | `A2AT_SAMPLE_WEBHOOK_PUBLIC_URL`、防火墙/NAT、接收端口 |
| run 在网管 Task 之前结束 | 长任务跟踪与轮询配额 |
| Task-T 发送前被拒 | 运维原文、SDK 校验错误码、模型端点 |
| Task 停在 `SUBMITTED` | 厂商状态推送/订阅；wire 日志确认是否收到推送 |

## 规则

- 运维原文是 Task-T 的唯一输入，客户端不得“修正”它。
- approval 1 之外的审批属于厂商网管。
- trace/Langfuse/metrics 失败是 best effort，不改变协议结果。
