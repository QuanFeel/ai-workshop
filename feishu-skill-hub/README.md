# 飞书 Skill Hub

把飞书知识库作为团队内部的 Skill 中心库，让 Codex、OpenClaw、Hermes、Coze 等 Agent 能够发布、发现、安装和更新同一份标准 Skill 文件夹。

本项目包含两个职责分离的 Agent Skills：

| Skill | 职责 |
| --- | --- |
| `feishu-cli` | 安装、配置并安全使用官方 `lark-cli`，管理 Profile 和通用 OAuth 操作 |
| `feishu-skill-sync` | 绑定知识库目录，打包完整 Skill，执行版本门禁、发布、拉取和安装验证 |

项目不包含飞书 CLI 可执行文件、App Secret、Token、Profile、知识库 Token 或任何本机缓存。

## 工作方式

```mermaid
flowchart LR
    A[本地标准 Skill 文件夹] --> B[版本检查]
    B --> C[完整 ZIP]
    C --> D[飞书知识库中心库]
    D --> E[其他 Agent 拉取]
    E --> F[校验后安装或升级]
```

- 知识库中每个 Skill 对应一个中文说明页和固定名称的 `<skill-name>.zip`。
- 版本只读取 `SKILL.md` frontmatter 的 `metadata.version`，并按 SemVer 比较。
- 远端不存在时创建；本地版本更高时更新；版本相同跳过；远端更高时拒绝覆盖。
- 发布和拉取都先返回统一计划；发生写入时只确认一次。
- 只有脚本顶层同时返回 `ok: true` 和 `verified: true`，才能认定同步完成。

## 环境要求

- Python 3.10 或更高版本；同步脚本只使用 Python 标准库。
- Node.js 当前 LTS，用于安装官方飞书 CLI。
- 官方 `@larksuite/cli`。本地验证使用过 `lark-cli 1.0.80`；安装命令默认获取官方最新版本。
- 能运行标准 Agent Skills 的 Codex、OpenClaw、Hermes、Coze 或其他 Agent 环境。

## 安装

克隆仓库：

```bash
git clone https://gitee.com/zhongwangquan/ai-workshop.git
cd ai-workshop/feishu-skill-hub
```

把两个 Skill 复制到当前 Agent 的全局 Skills 目录。下面以通用目录 `~/.agents/skills` 为例：

```bash
mkdir -p ~/.agents/skills
cp -R skills/feishu-cli ~/.agents/skills/
cp -R skills/feishu-skill-sync ~/.agents/skills/
```

如果目标位置已有同名目录，先备份并人工比较，不要直接覆盖。不同 Agent 的标准 Skills 根目录可能不同，应由当前 Agent 根据自身环境确定。

## 首次配置

先安装并验证官方 CLI：

```bash
npx @larksuite/cli@latest install
lark-cli --version
lark-cli profile list
```

需要新身份时创建独立 Profile：

```bash
lark-cli config init --new --name "<profile-name>"
```

然后由 `feishu-skill-sync` 发起同步所需的一次最小权限 OAuth。不要先让 `feishu-cli` 运行通用 `auth login --recommend`：

```bash
SYNC="$HOME/.agents/skills/feishu-skill-sync/scripts/skill_sync.py"

python3 "$SYNC" library profiles
python3 "$SYNC" library authorize --lark-profile "<profile-name>"
```

首次授权会返回验证地址和临时 `device_code`。在网页完成授权后，用同一 Profile 和同一个临时代码继续：

```bash
python3 "$SYNC" library authorize \
  --lark-profile "<profile-name>" \
  --device-code "<same-device-code>"

python3 "$SYNC" library discover --lark-profile "<profile-name>"
python3 "$SYNC" library set --lark-profile "<profile-name>"
```

`device_code` 是临时凭据，不要截图公开、写入文件或提交到 Git。

## 日常使用

```bash
# 查看本地缓存的知识库位置，不访问飞书
python3 "$SYNC" library show

# 查看中心库中的 Skills
python3 "$SYNC" list

# 发布一个 Skill 或整个 skills 父目录
python3 "$SYNC" publish \
  --source /path/to/skill-or-skills \
  --notes "本次更新要点"

# 拉取中心库全部 Skills
python3 "$SYNC" pull --skills-dir "/path/to/current-agent/skills"

# 只拉取指定 Skills
python3 "$SYNC" pull \
  --skills-dir "/path/to/current-agent/skills" \
  --name skill-a skill-b
```

发布和拉取第一次只会返回计划与确认码；确认真实账号、知识库位置、目标目录和版本动作后，再原样执行并追加脚本返回的确认码。

## 权限与安全边界

- OAuth 只申请同步真实调用链使用的身份、文档、云盘和知识库权限，不使用 `--domain all`。
- Profile 只是 CLI 路由；真实身份需要通过 OAuth 用户、脱敏 Open ID 和知识库位置共同确认。
- 缓存默认写入 `~/.cache/feishu-skill-sync/library.json`，权限为当前用户可读写；不保存 access token、refresh token、App Secret 或 `device_code`。
- CLI 错误不会回显原始 stdout/stderr；结构化错误中的 Token、临时代码和身份指纹会被脱敏。
- ZIP 拒绝绝对路径、`..` 路径和符号链接；安装升级保留临时备份，失败时恢复原目录。
- 删除、降级、回滚、权限成员管理和未确认写入不在本项目范围内。

更多行为约束以两个 Skill 自身的 `SKILL.md` 为准。

## 验证

项目提供不访问飞书、不产生远端写入的本地验证入口：

```bash
python3 scripts/verify.py
```

它会运行 `feishu-skill-sync` 的单元测试、Python 编译、两个 Skill 的本地解析检查，以及可用时的 Ruff 检查。当前公开版本本地验证结果为 57 项测试通过。

真实飞书发布和拉取依赖用户自己的 Profile、权限和知识库，因此不会被自动测试伪装成已经联调成功。

## 参与贡献

提交修改前请阅读 [`CONTRIBUTING.md`](./CONTRIBUTING.md) 和 [`SECURITY.md`](./SECURITY.md)。不要在 Issue、日志或测试样本中放入真实凭据和飞书资源 Token。

## 许可证

本目录及其两个 Skill 使用 [MIT License](./LICENSE)。官方 `lark-cli` 是外部依赖，不包含在本仓库中，也不受本目录许可证重新授权。
