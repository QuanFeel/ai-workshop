# SDD 工作流 Skill

把需求逐步整理为可验收的规格、实现方案和任务清单，并按授权推进实现。支持新项目和已有项目，也可以从已有的规格或任务继续。

## 规格放在哪里

用户指定的目录优先；相对路径以目标项目根目录为基准。未指定时，沿用项目已有约定；没有约定则使用项目根目录下的 `.spec/<功能名>/`。`spec.md`、`plan.md` 和 `tasks.md` 保存在这个目录中。不会要求用户把项目文件放到 Skill 安装目录，也不会迁移已有目录。

## 安装

先克隆本仓库，再将完整的 `skills/sdd-workflow/` 目录复制到当前使用的 Agent 的 Skill 目录，保留 `SKILL.md` 和 `prompts/`。本仓库的分类目录本身不会自动成为其他项目的 Skill 发现目录。

| Agent | 当前项目安装目录 | 用户级安装目录 | 官方说明 |
| --- | --- | --- | --- |
| Codex | `.agents/skills/sdd-workflow/` | `~/.agents/skills/sdd-workflow/` | [Codex Skills](https://learn.chatgpt.com/docs/build-skills) |
| Claude Code | `.claude/skills/sdd-workflow/` | `~/.claude/skills/sdd-workflow/` | [Claude Code Skills](https://code.claude.com/docs/en/skills) |
| Cursor | `.cursor/skills/sdd-workflow/` | `~/.cursor/skills/sdd-workflow/` | [Cursor Skills](https://cursor.com/docs/skills) |
| Gemini CLI | `.gemini/skills/sdd-workflow/` | `~/.gemini/skills/sdd-workflow/` | [Gemini CLI Skills](https://geminicli.com/docs/cli/skills/) |
| GitHub Copilot | `.github/skills/sdd-workflow/` | `~/.copilot/skills/sdd-workflow/` | [Copilot Agent Skills](https://docs.github.com/en/copilot/concepts/customizations/agent-skills) |

项目目录指目标项目的根目录；`~` 指当前用户的主目录。不同产品及版本的发现规则可能变化，其他支持 Agent Skills 的产品请按其官方文档选择安装目录。不要同时安装到多个位置，除非你确实需要多个客户端使用。

例如，在 macOS 或 Linux 中安装到 Codex 的用户目录：

```sh
git clone https://github.com/QuanFeel/ai-workshop.git
cd ai-workshop
skill_target="$HOME/.agents/skills"
if [ -e "$skill_target/sdd-workflow" ] || [ -L "$skill_target/sdd-workflow" ]; then
  printf '%s\n' '目标已存在，请先比较内容并备份，再决定如何更新。'
else
  mkdir -p "$skill_target"
  cp -R skills/sdd-workflow "$skill_target/"
fi
```

Windows PowerShell 示例，同样安装到 Codex 的用户目录：

```powershell
git clone https://github.com/QuanFeel/ai-workshop.git
Set-Location ai-workshop
$skillTarget = Join-Path $env:USERPROFILE '.agents\skills'
$skillDestination = Join-Path $skillTarget 'sdd-workflow'
if (Test-Path -LiteralPath $skillDestination) {
  Write-Host '目标已存在，请先比较内容并备份，再决定如何更新。'
} else {
  New-Item -ItemType Directory -Force -Path $skillTarget | Out-Null
  Copy-Item -Recurse -LiteralPath 'skills/sdd-workflow' -Destination $skillTarget
}
```

其他 Agent 请将示例的目标目录改为上表对应目录。已有同名 Skill 时，先比较并备份，避免覆盖个人修改。安装后按客户端支持的方式刷新或新建会话，再检查 Skill 是否被发现；例如 Gemini CLI 可使用 `/skills list` 和 `/skills reload`。文件复制完成不代表当前会话已经加载。

## 使用

```text
使用 sdd-workflow，帮我为当前项目增加一个任务管理功能。
规格放在项目的 docs/specs/task-management/，先梳理需求，
再完成技术方案、任务拆分和实现，最后验证核心流程。
```

也可以只要求某个阶段，或者指定一个已有的规格目录让 Agent 继续。需要澄清时，会优先调用当前 Agent 在当前模式下可用的系统提问组件；组件不可用时使用普通对话。明确授权连续推进后，无须重复批准已经确定的步骤。部署、推送和其他外部操作仍以用户授权与宿主规则为准。
