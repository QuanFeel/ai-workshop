---
name: feishu-cli
description: 安装、配置并安全使用官方 lark-cli，处理多 Profile、多用户身份、OAuth 授权、云文档、云盘和知识库。当本机未安装飞书 CLI、需要协作安装与初始化、创建或选择独立 Profile、登录或补权限，以及执行飞书读写任务时使用。
metadata: {"version":"0.1.3"}
---

# 飞书 CLI

使用系统可发现的官方 `lark-cli`。本 Skill 不捆绑 CLI 可执行文件、软链接、官方命令手册、机器绝对路径或任何凭据。

## 安装与就绪检查

先检查 CLI，不要因为当前 Skill 已安装就假定 CLI 也已安装：

```bash
command -v lark-cli
lark-cli --version
```

PowerShell 使用 `Get-Command lark-cli`。若 CLI 已存在，跳过安装；不要重复安装或擅自升级。

若 CLI 不存在：

1. 检查 `node --version` 和 `npx --version`。缺少 Node.js 时，直接文字引导用户安装当前 Node.js LTS；不要使用 `sudo`，不要擅自选择或修改用户的系统包管理器。
2. 告知用户接下来会从 npm 下载并安装官方飞书 CLI；需要用户授权外部安装时先取得确认。
3. 执行官方安装命令：

```bash
npx @larksuite/cli@latest install
```

4. 再运行 `lark-cli --version`。只有命令成功且返回版本后，才能报告 CLI 已安装。

当前 Agent 无法执行安装、缺少网络或需要用户操作时，不要返回模糊错误，也不要假装完成；直接展示上述准确命令和受阻原因，等用户完成后继续验证。

## 初次配置

CLI 安装后先运行 `lark-cli profile list`。已有目标 Profile 时复用，不得为了省事覆盖配置。首次配置或用户明确需要新身份时，创建独立 Profile：

```bash
lark-cli config init --new --name "<profile-name>"
```

该命令会持续输出配置 URL 并等待用户在网页完成应用创建。放在可持续的前台会话或后台任务中运行，把 URL 原样展示给用户；短超时不代表失败，不要重启，也不要同时发起第二次初始化。

不同 OAuth 身份使用不同 Profile 和独立 App ID。不要复制旧 Profile 的 App Secret，也不要把 App Secret 写入命令、日志、Skill 或项目文件。

## OAuth 授权

授权前先展示目标 Profile，并请用户确认准备在网页选择的飞书身份。优先采用分步设备授权：

```bash
lark-cli --profile "<profile-name>" auth login --recommend --no-wait --json
```

只把返回的验证地址交给用户。`device_code` 是临时凭据，不得展示给无关人员、写入文件或长期保存。用户确认网页授权完成后，在同一受控会话中使用同一个临时代码完成登录：

```bash
lark-cli --profile "<profile-name>" auth login \
  --device-code "<same-device-code>" \
  --json
```

最后必须实时验证：

```bash
lark-cli --profile "<profile-name>" whoami --as user
lark-cli --profile "<profile-name>" auth status --verify --json
```

不能仅凭 Profile 名、应用 ID、浏览器页面或“授权成功”文字推断真实账号。向用户展示实际用户名和脱敏后的身份信息，再由用户确认。

若由 `$feishu-skill-sync` 转交到本 Skill，只负责安装、基础配置、必要的 Profile 创建和 CLI 就绪；不要运行本节的通用 `auth login --recommend`，也不要提前发起 OAuth。完成后原样重试同步命令，让同步 Skill 的 `library authorize` 用真实调用链所需的最小 scope 集合完成唯一一次 OAuth，再验证知识库绑定。不要代替业务 Skill 拼接文档、云盘或知识库命令。

## 加载当前 CLI 的官方说明

执行具体飞书任务前先读取当前 CLI 随附的通用规则，再按任务读取对应官方 Skill：

```bash
lark-cli skills read lark-shared
lark-cli skills read lark-doc
lark-cli skills read lark-drive
lark-cli skills read lark-wiki
```

没有高层命令时再读取 `lark-openapi-explorer`。官方 Skill 指向参考文件时只读取当前任务需要的文件；文件名不确定先用 `lark-cli skills list <skill-name>` 列出。

## 执行规则

- 身份敏感操作显式带 `--profile "<profile-name>"`，并按资源类型显式选择 `--as user` 或 `--as bot`。
- 执行命令前先查看对应 `--help`；优先使用官方高层快捷命令。
- 写入前展示真实目标和计划。删除、覆盖、移动、权限变更等高风险操作必须取得明确确认；只有确认后才使用 `--yes`。
- 不输出 App Secret、access token、refresh token 或 device code，不把它们写入 Skill、缓存或项目文件。
- 以进程退出码和 JSON 的 `ok` 字段判断成功；不能只凭自然语言输出或浏览器状态判断。
- 业务 Skill 保留自己的目标缓存、权限范围和业务协议；本 Skill 只负责官方 CLI 安装、配置、授权和通用操作能力。
