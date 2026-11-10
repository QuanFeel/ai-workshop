# AI Workshop

面向 AI 使用者和 Agent 开发者的开源实验仓库。每个目录都是一个可以独立阅读或使用的小项目。

## Prompts

| 目录 | 内容 | 使用方式 |
| --- | --- | --- |
| [`prompts/3D-memory-courtyard/`](./prompts/3D-memory-courtyard/) | 用 AI 生成“记忆小院”3D 互动相册的完整提示词 | 阅读并复制 `prompt.md` |
| [`prompts/paper-time-pomodoro/`](./prompts/paper-time-pomodoro/) | 用 AI 生成“纸上时光番茄钟”本地演示的完整提示词 | 阅读并复制 [`prompt.md`](./prompts/paper-time-pomodoro/prompt.md) |

## Skills

| 目录 | 内容 | 使用方式 |
| --- | --- | --- |
| [`skills/sdd-workflow/`](./skills/sdd-workflow/) | 从需求、方案、任务到实现的通用 SDD 开发 Skill | [安装与使用](./skills/sdd-workflow/README.md) |
| [`skills/feishu-skill-hub/`](./skills/feishu-skill-hub/) | 用飞书知识库集中发布、拉取和更新 Agent Skills 的飞书同步 Skill | 安装 `feishu-skill-sync` |

## Extensions / 插件

| 目录 | 内容 | 使用方式 |
| --- | --- | --- |
| [`extensions/codex-quota-insights/`](./extensions/codex-quota-insights/) | 读取真实 Codex 周额度、固定预算与 Token 活动摘要 | 本仓库提供可独立运行的本地看板和脚本 |

旧版 Prompt 路径保留兼容副本；修改 Prompt 时请同时同步对应旧地址，确保已分享链接仍可使用。旧项目入口提供新目录链接。

## 使用原则

- 每个项目的依赖、安装、验证和安全边界，以该目录中的 README 为准。
- 不要把 Token、App Secret、OAuth 临时代码、个人配置或本机缓存提交到仓库。
- 涉及外部平台写入的工具，必须先展示真实账号、目标位置和写入计划，再由用户确认。

## 许可证

本仓库及其中的所有项目统一采用 [MIT License](./LICENSE)。
