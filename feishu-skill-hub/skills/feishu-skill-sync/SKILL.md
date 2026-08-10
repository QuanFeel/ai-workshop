---
name: feishu-skill-sync
description: 在飞书知识库中自动发现、绑定、发布、比较和拉取标准 Agent Skill 文件夹，并管理同一 CLI 下多个独立飞书 OAuth 身份。适用于 Codex、OpenClaw、Hermes、Coze 等可运行 Python 与 lark-cli 的 Agent；缺少官方 CLI 时直接返回可展示的官方安装和配置引导；OAuth 由本 Skill 统一发起，绑定后复用本地缓存，一个或多个 Skills 始终共用一份版本计划和一次写入确认。
metadata:
  version: "0.21.0"
---

# 飞书AgentSkill同步

把用户确认的飞书 `账号 / 知识库 / skills 目录` 作为中央 Skill 库。直接运行本 Skill 目录下的 `scripts/skill_sync.py`（可用绝对路径）；除非修复脚本本身，不要读取源码。

发布和拉取必须由 `skill_sync.py` 完整编排，不得绕过脚本自行拆成 `docs` 与 `drive` 命令。脚本内部负责附件、ZIP 结构、版本和安装结果的全部验证；Agent 不得自行推理验证结果。只有脚本顶层同时返回 `ok: true` 和 `verified: true`，Agent 才能向用户报告已同步。不返回也不兼容逐项验证字段。

## 执行

```bash
# 先查本地缓存；存在时直接复用，不访问飞书
python3 scripts/skill_sync.py library show

# 仅首次使用、用户主动切号或缓存失效时执行
python3 scripts/skill_sync.py library profiles
python3 scripts/skill_sync.py library authorize --lark-profile "<new-or-existing-profile>"
python3 scripts/skill_sync.py library authorize --lark-profile "<new-profile>" --device-code "<same-device-code>"
python3 scripts/skill_sync.py library discover --lark-profile "<profile>"
python3 scripts/skill_sync.py library set --lark-profile "<同一 profile>"

# 日常只调用脚本
python3 scripts/skill_sync.py list
python3 scripts/skill_sync.py publish --source /path/to/skill-or-skills --notes "<更新要点>"
python3 scripts/skill_sync.py pull --skills-dir "<当前 Agent 的标准 Skills 根目录>"
python3 scripts/skill_sync.py pull --skills-dir "<当前 Agent 的标准 Skills 根目录>" --name <name> [<name> ...]
```

拉取时由当前 Agent 根据自身运行环境、配置和已安装 Skills 的位置，自动确定它平时保存 Skills 的根目录，并传给 `--skills-dir`。不要在本 Skill 中写死 Codex、OpenClaw、Hermes 或 Coze 的路径，不要根据本同步 Skill 自身或软链接的位置推断，也不缓存本地 Skills 目录。正常情况下不询问用户；只有当前 Agent 无法唯一确定标准目录，或用户要求自定义位置时，才请用户指定。

### CLI 就绪预检

需要访问飞书的命令会先检查官方 `lark-cli`。未安装时脚本返回 `ok: true`、`action: setup_required`、`ready: false`，这是可继续处理的安装前置状态，不得向用户报告同步失败。

直接向用户展示脚本的 `message` 和 `install_command`，引导安装官方 CLI 并运行 `lark-cli config init --new`；不要报技术错误，不要假装已经安装，也不要要求用户安装第二个 Skill。安装配置完成后原样重试当前同步命令，由本 Skill 的 `library authorize` 使用真实调用链所需的最小 scope 集合发起唯一一次 OAuth。

`library show`、`library clear`、`inspect` 和 `compare` 是本地命令，不因缺少 CLI 而中断。不要绕过脚本自行判断 CLI，也不要在仓库中捆绑可执行文件、机器绝对路径或凭据。

缓存不存在或用户要求切号时才运行 `library profiles`。用户给出的账号名称只表示目标账号意图，不能当成显示标签或 `--account-label`。默认复用现有 Profile：先用账号关键词与 `library profiles` 返回的 Profile 名称和用户名做不区分大小写的精确或近似匹配，排除明显属于其他账号的候选，然后请用户确认最接近的候选。Profile 仅是 CLI 路由线索，最终身份仍由 OAuth 的 Open ID 和 Tenant Key 校验。

`library profiles` 的 `token_status` 只是 CLI 状态线索，不能单独证明用户未授权。状态为 `needs_refresh` 时，脚本同时返回 `token_status_is_advisory: true`、`reauthorization_required: false` 和 `next_action: try_read_only_discover`；Agent 必须遵循这些机器字段，不得根据状态文字或过期时间自行要求扫码。选定已有 Profile 后，先对该 Profile 运行只读的 `library discover`，让 lark-cli 在真实请求中处理令牌刷新。成功则直接继续绑定；只有飞书明确返回 Token 失效、OAuth 未完成或缺少必需权限时，才对同一 Profile 运行 `library authorize`，不新建 Profile。

只有用户明确确认现有候选都不是、目标是新身份，才用一个新 Profile 运行 `library authorize`。不要因为关键词没有完全命中就直接创建，也不要继续要求用户核对已排除的 Open ID。不要调用其他飞书业务 Skill 绕过同步脚本。新 Profile 由脚本转交给官方 `lark-cli config init --new --name <profile>`，创建一套唯一 App ID 的新应用与 Profile；`lark-cli` 禁止两个 Profile 复用同一 App ID，不得复制旧 Profile 凭据。

`config init --new` 会持续输出配置 URL 并等待用户在网页完成新应用创建。在 Agent 的可持续执行会话或后台任务中运行，把 URL 原样给用户；不要因短超时重启，不要同时启动第二次初始化。配置完成后，再次运行相同的 `library authorize --lark-profile <profile>` 发起 OAuth。

`library authorize` 首次返回 OAuth `verification_url` 和临时 `device_code`。原样向用户展示 URL，等待用户在网页选择正确飞书身份；不要在同一回合立即轮询，不要重新发起登录。用户说已完成后，用同一 Profile 和该 `device_code` 完成授权；临时代码不落盘。然后执行 `discover` 和 `set`，两者必须显式使用同一 Profile；脚本用 Open ID 与 Tenant Key 校验实际身份。

### 一次性授权范围

`library authorize` 不使用 `--domain`，只通过一个 `--scope` 请求同步脚本真实调用链所需的 12 项权限：

```text
auth:user.id:read offline_access
docx:document:create docx:document:readonly docx:document:write_only
drive:drive.metadata:readonly drive:file:download drive:file:upload
wiki:wiki:readonly wiki:node:retrieve wiki:node:update wiki:space:retrieve
```

对应能力分别是身份校验与令牌刷新、页面创建/读取/覆盖、固定 ZIP 上传/下载/元数据读取、知识空间和节点发现及标题更新。当前 lark-cli 元数据把 `wiki:space:retrieve` 声明为知识空间列表权限，但真实飞书服务端仍可能要求兼容只读权限 `wiki:wiki:readonly`；两项同时保留，确保首次绑定能够完成真实 `library discover`。不得改用含写能力的 `wiki:wiki`。`search:docs:read`、`space:document:retrieve`、`docs:document.media:upload` 以及复制、移动、成员管理、删除等 scope 不在真实调用链中，不得为了“可能以后使用”提前申请。

飞书权限需先在应用后台可用，再由用户在同一授权页面一次授予。新增真实调用时，必须先依据官方 API 或当前 lark-cli 命令元数据确认精确 scope、更新上述集合并提升 Skill 版本；不得依靠动态业务域静默扩大授权。真实操作明确返回缺少新权限时，才对同一 Profile 再运行一次 `library authorize`。

OAuth 完成时，只要 lark-cli 返回任何 `missing` scope，脚本就一次性列出全部缺失项并停止，不把部分授权报告为成功，也不让 Agent 按功能逐项重复授权。`offline_access` 在同一请求中显式加入，供 lark-cli 在后续真实调用中刷新短期 user access token。

`publish` 在生成任何远程写入前检查页面创建/读取/覆盖、ZIP 上传与元数据读取、Wiki 节点读取和标题更新所需的 7 项 scope。缺权限时停在预检阶段，引导用同一 Profile 重跑一次 `library authorize`；不先建正文、不逐项补授权。

未指定知识库和目录时，脚本从 CLI 结果自动选择默认候选：

1. 优先知识库名含 `技能 / Skill / Agent / 智能体`，其次选择 `知识 / Knowledge / Wiki / 资料库 / 文档库` 相关名称。
2. 在知识库中优先精确 `skills`，其次选择名称含 `skill` 或 `技能` 的目录，例如“搭建技能”。
3. 同级候选保持 CLI 返回顺序，使用第一个；用户可直接指定其他候选。

所有用户、知识库和目录名称必须来自 CLI 或用户本次选择，不得在 Skill 中写入具体账号、Open ID、知识库名或目录路径。

对用户固定原样展示脚本返回的 `library.display`：

```text
Profile：<实际使用的 CLI Profile>
OAuth 用户：<已验证用户名>（<脱敏 Open ID>）
目前位置：<知识库> / <完整目录路径>
```

Profile 是实际 CLI 路由，Open ID 是实际 OAuth 用户指纹；两者都必须展示。不要展示 Tenant Key、推断账号类型或账号备注。`--account-label` 已停用，脚本会拒绝它；切号只能通过独立 Profile + OAuth 完成。

`discover` 后核对一次上述真实身份和默认位置，确认后写入缓存。以后 `library show` 只读取本地缓存；`list`、`publish` 和 `pull` 直接使用缓存 Profile、节点和目录，不再运行 `whoami`、`auth status --verify` 或用户信息接口。用户纠正账号、主动切号，或飞书返回授权/权限/节点失效错误时，清除缓存并重新绑定。

## 人工确认

`library set` 首次调用只返回身份与位置计划，用户确认一次后缓存。后续发布或拉取不再确认身份，只对实际写入的 Skill、版本和动作返回计划与确认码；无写入时直接跳过。`publish` 和 `pull` 天然处理一个或多个 Skills，始终只确认一次，不逐个询问。用户回复“确认”即可原样重跑并追加确认码；用户直接说明修改项时重新预检。不要要求固定确认口令。

## 协议

- 版本真源必须是 `SKILL.md` frontmatter 中的三段式 `metadata.version`；同时支持 `metadata: {"version":"1.0.0"}` 和 YAML 多行映射 `metadata:` / `version: "1.0.0"`。缺少该字段、只写顶层 `version` 或只在正文写“当前版本”都必须拒绝同步，不得猜测版本；远端不存在则创建，同版本跳过，本地较高才发布，本地较低拒绝覆盖远端。
- ZIP 固定为 `<name>.zip`，解压后直接得到同名 Skill 文件夹；文件名不带版本。
- `pull` 必须在脚本内部验证页面包名、同级 ZIP 节点、ZIP 顶层目录、`SKILL.md`、页面版本和安装后版本；任一不一致必须直接失败，不得返回 `verified: true`。
- 飞书知识库节点标题必须跟随 `SKILL.md` 的一级标题；升级预检明确展示旧标题与新标题，写入后通过官方 Wiki OpenAPI 同步标题。同版本但远端标题漂移时只修复节点标题，不覆盖正文和 ZIP。
- 发布成功必须同时验证知识库节点标题、页面 `name / version / package` 与同级 `<name>.zip` 文件节点；只写入正文、只改标题、只在正文中写包名或只生成本地 ZIP 都不算完整版本发布。验证失败必须返回错误，Agent 不得向用户报告已同步。
- 高版本发布需提供 500 字以内更新要点；首次发布缺省为“首次发布”。页面顶部保存当前说明，累计更新记录放在末尾，最新在前。
- `publish --source` 接受一个或多个 Skill 目录或父目录；`pull` 默认选择中央库全部 Skill，也可用 `--name` 选择一个或多个。集合中每个 Skill 独立应用版本门禁，同版本和较低版本自动跳过。
- 本地拉取目标由运行本 Skill 的 Agent 选择其标准 Skills 根目录并传入 `--skills-dir`；脚本不设平台默认路径，也不将本地目录写入中央库缓存。
- 脚本校验包结构、固定文件节点、危险路径和版本门禁；不修改源 Skill，不自动提升版本，打包时排除 `tests/`。

已安装 `lark-cli` 时直接使用；未安装时返回非错误的安装前置状态和官方安装配置文字。新 Profile 的应用凭据由官方 `config init --new` 管理，同步脚本不读取、复制或输出 App Secret。缓存默认位于 `~/.cache/feishu-skill-sync/library.json`，仅保存 CLI Profile、`open_id`、`tenant_key` 和目录标识，不保存凭据；后续直接复用，不主动重新验证。旧协议、用户主动切号、授权失效或位置失效时重新绑定。删除、回滚、降级和未确认写入不属于本流程。
