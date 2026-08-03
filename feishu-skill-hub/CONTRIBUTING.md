# 参与贡献

感谢改进飞书 Skill Hub。提交前请遵循以下规则：

1. 不提交真实 App ID、App Secret、Token、`device_code`、Open ID、Tenant Key、知识库 Token、Profile 配置或本机缓存。
2. 测试必须使用明显的假值，例如 `test-user`、`example-token` 和 `example.invalid`。
3. 不在测试中访问真实飞书账号或产生远端写入；外部 CLI 调用必须 mock。
4. 修改同步行为时同步更新 `SKILL.md`，并按 SemVer 提升对应 `metadata.version`。
5. 保持 `feishu-cli` 与 `feishu-skill-sync` 的职责边界：前者负责官方 CLI 通用能力，后者负责知识库同步协议。
6. 运行 `python3 scripts/verify.py`，确认全部检查通过。

提交说明应包含修改目的、风险边界和验证结果。涉及权限范围、版本协议、ZIP 结构或确认机制的改动，需要单独说明兼容性影响。
