# Codex Quota Dashboard

一个可开源、零模型调用的 Codex 周额度预览面板。

它通过本机 `codex app-server` 读取当前账号的真实额度和精确重置时间，并用固定 `100%` 基准生成每日预算；无需自建服务器。

## 能做什么

- 读取真实 Codex 剩余额度、已用比例和服务端重置时间。
- 从多个额度窗口中选择最长的 Codex 窗口作为周额度。
- 按工作日 `2`、周末 `1` 分配固定 `100%` 周预算；首尾非完整日按时长折算，整数预算合计始终为 `100%`。
- 在真实剩余额度达到 `5%`、`1%`、`0%` 时给出状态等级。
- 提供一个只监听本机的多 Tab 预算看板：额度预算与省 Token 建议分开显示。
- 预算页支持“预计重置”和“恢复官方重置”；过期日期置灰、今日和重置日突出显示。
- 内置四条通用省 Token 建议、RTK 输出压缩参考和可编辑复制的 `AGENTS.md` Prompt。

## 打开预览面板

要求：Node.js 18+、已安装并登录的 Codex CLI。

```bash
node scripts/dashboard-server.mjs
```

如果 `codex` 不在 `PATH`：

```bash
CODEX_BIN=/absolute/path/to/codex node scripts/dashboard-server.mjs
```

脚本本身不调用模型，不产生新的推理 Token。它只向本机 Codex App Server 发起账号读取请求。

然后访问：

```text
http://127.0.0.1:8788/
```

看板只监听 `127.0.0.1`，首次打开和点击“刷新额度”时读取数据，不进行后台轮询。可以通过 `CODEX_QUOTA_DASHBOARD_PORT` 修改端口。

"预计重置"、省 Token 建议和 Prompt 仅保存在当前浏览器的 `localStorage`。它们不属于 Codex 官方额度数据，也不会被上传或共享；“恢复官方重置”会清除本地计划并重新采用 Codex 返回的重置时间。如需多人共享，请在自己的部署中另行实现鉴权和存储。

## 预算与真实额度的边界

真实额度和预算是两组不同数据：

- `remainingPercent`：Codex 返回的真实周剩余额度。
- `budget.baselinePercent`：永远从 `100%` 开始的计划基准。
- `budget.today.budgetPercent`：当天计划额度，不是当天真实消耗。

实际额度下降不会导致日历预算跟着重算。只有重置时间或额度窗口本身发生变化，预算周期才会变化。

## 测试

```bash
node --test tests/*.test.mjs
node --check scripts/codex-quota.mjs
node --check scripts/dashboard-server.mjs
```

测试不需要真实账号，只验证额度窗口选择和预算算法。

## 安全报告

请不要在 Issue 中粘贴 Auth、Token、完整会话日志或包含账号信息的命令输出。安全问题请参阅 [SECURITY.md](./SECURITY.md)。

## 许可证

[MIT](./LICENSE)
