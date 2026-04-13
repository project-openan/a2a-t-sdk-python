# A2A-T Prompt 共享重构实现问题清单

以下问题不阻塞当前开发，会按当前最保守理解继续实现，待全部代码完成后统一复核。

## 1. `A2ATConfig` 顶层新增字段的最终命名

当前按设计文档实现为：
- `language`
- `prompt_resource_version`

如果后续配置规范要求不同命名，需要在实现完成后统一调整映射层。

## 2. `package_data/prompt_resources/` 的初始内置场景集

当前会先补最小可运行样例资源，满足测试与链路打通。

如果业务侧后续要求补齐更多场景，需要追加资源文件，不影响当前共享架构。

## 3. server 结果模型中的 `notes/confidence`

当前按新设计收缩，不再作为主结果输出字段。

如果已有外部调用方依赖，需要在后续评估是否增加兼容期映射。

## 4. `ScenarioRecognizer` 建议接口缺少 prompt 资源入参

设计文档中的建议接口只包含：
- `normalized_input`
- `scenarios`
- `language`

但在实际开发中，共享 `ScenarioRecognizer` 还需要消费第一阶段 prompt 资源，因此当前实现补充了：
- `system_prompt`
- `user_prompt`

后续需要回看设计文档是否同步收敛这一点。
