"""Feishu lark-cli transport and knowledge-base discovery."""

import json
import os
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .core import (
    AccountInfo,
    LibraryInfo,
    PageInfo,
    SyncError,
    WIKI_TOKEN_RE,
    parse_page_markdown,
    user_label,
    validate_version,
)


ACTIVE_PROFILE: str | None = None
USE_ACTIVE_PROFILE = object()
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
EXPLICIT_AUTH_SCOPES = (
    "auth:user.id:read",
    "offline_access",
    "docx:document:create",
    "docx:document:readonly",
    "docx:document:write_only",
    "drive:drive.metadata:readonly",
    "drive:file:download",
    "drive:file:upload",
    "wiki:node:retrieve",
    "wiki:node:update",
    "wiki:space:retrieve",
)
REQUIRED_PUBLISH_SCOPES = (
    "docx:document:create",
    "docx:document:readonly",
    "docx:document:write_only",
    "drive:drive.metadata:readonly",
    "drive:file:upload",
    "wiki:node:retrieve",
    "wiki:node:update",
)
LARK_CLI_INSTALL_COMMAND = "npx @larksuite/cli@latest install"


def lark_cli_available() -> bool:
    """Return whether the configured or default lark-cli executable is usable."""
    configured = os.environ.get("LARK_CLI_CMD")
    if configured is not None:
        try:
            command = shlex.split(configured)
        except ValueError as exc:
            raise SyncError("LARK_CLI_CMD 格式无效") from exc
        if not command:
            raise SyncError("LARK_CLI_CMD 不能为空")
        executable = os.path.expanduser(command[0])
        if os.path.isabs(executable) or os.sep in executable:
            return Path(executable).is_file() and os.access(executable, os.X_OK)
        return shutil.which(executable) is not None
    return shutil.which("lark-cli") is not None


def feishu_cli_skill_installed() -> bool:
    """Check common cross-agent locations for the optional helper Skill."""
    candidates: list[Path] = []
    configured_skill = os.environ.get("FEISHU_CLI_SKILL_DIR")
    if configured_skill:
        configured_path = Path(configured_skill).expanduser()
        candidates.append(
            configured_path
            if configured_path.name == "SKILL.md"
            else configured_path / "SKILL.md"
        )
    configured_roots = os.environ.get("AGENT_SKILLS_DIR")
    if configured_roots:
        candidates.extend(
            Path(root).expanduser() / "feishu-cli" / "SKILL.md"
            for root in configured_roots.split(os.pathsep)
            if root
        )
    home = Path.home()
    candidates.extend(
        (
            home / ".agents" / "skills" / "feishu-cli" / "SKILL.md",
            home / ".codex" / "skills" / "feishu-cli" / "SKILL.md",
            home / ".claude" / "skills" / "feishu-cli" / "SKILL.md",
        )
    )
    return any(candidate.is_file() for candidate in candidates)


def cli_setup_guidance() -> dict[str, Any]:
    """Return a non-error handoff when lark-cli is not installed yet."""
    helper_installed = feishu_cli_skill_installed()
    if helper_installed:
        message = (
            "未检测到官方 lark-cli。请调用 $feishu-cli 协作完成安装、"
            "基础配置和 Profile 创建，但不要运行通用 auth login。"
            "完成后原样重试当前同步命令，由 library authorize 使用"
            "真实调用链所需的最小 scope 集合发起唯一一次 OAuth。"
        )
        next_action = (
            "调用 $feishu-cli，仅完成安装和基础配置；"
            "不要授权；完成后原样重试当前同步命令"
        )
    else:
        message = (
            "未检测到官方 lark-cli，也未检测到 feishu-cli Skill。"
            f"请先运行 `{LARK_CLI_INSTALL_COMMAND}`，安装后运行 "
            "`lark-cli config init --new` 完成配置；再回到本 Skill，"
            "由 library authorize 完成同步所需的 OAuth 授权。"
        )
        next_action = "按 message 中的命令安装和配置，然后原样重试当前同步命令"
    return {
        "ok": True,
        "action": "setup_required",
        "ready": False,
        "cli_installed": False,
        "helper_skill_installed": helper_installed,
        "oauth_deferred_to_caller": True,
        "authorization_owner": "feishu-skill-sync",
        "install_command": LARK_CLI_INSTALL_COMMAND,
        "verify_command": "lark-cli --version",
        "message": message,
        "next_action": next_action,
    }


def set_lark_profile(profile: str | None) -> None:
    global ACTIVE_PROFILE
    ACTIVE_PROFILE = profile.strip() if profile else None
    if profile is not None and not ACTIVE_PROFILE:
        raise SyncError("lark-cli profile 不能为空")


def json_objects(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    values: list[Any] = []
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        values.append(value)
    return values


SENSITIVE_CLI_KEYS = (
    "access_token",
    "refresh_token",
    "device_code",
    "user_code",
    "app_secret",
    "tenant_key",
    "open_id",
)


def redact_cli_value(value: Any) -> Any:
    """Remove credentials and identity fingerprints from CLI error details."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            result[str(key)] = (
                "***"
                if any(name in normalized for name in SENSITIVE_CLI_KEYS)
                else redact_cli_value(child)
            )
        return result
    if isinstance(value, list):
        return [redact_cli_value(child) for child in value]
    return value


def cli_failure_detail(process: Any, payload: Any = None) -> dict[str, Any]:
    """Return useful failure metadata without echoing raw CLI streams."""
    detail: dict[str, Any] = {"returncode": process.returncode}
    if payload is not None:
        detail["payload"] = redact_cli_value(payload)
    else:
        detail["message"] = (
            "lark-cli 未返回可用的 JSON 错误详情；"
            "为避免泄露凭据，已省略原始 stdout/stderr"
        )
    return detail


def resolve_cli(
    profile_override: str | None | object = USE_ACTIVE_PROFILE,
) -> list[str]:
    configured = os.environ.get("LARK_CLI_CMD")
    if configured:
        try:
            command = shlex.split(configured)
        except ValueError as exc:
            raise SyncError("LARK_CLI_CMD 格式无效") from exc
        if not command:
            raise SyncError("LARK_CLI_CMD 不能为空")
    else:
        direct = shutil.which("lark-cli")
        if not direct:
            raise SyncError(
                "未检测到 lark-cli。请当前 Agent 先安装官方飞书 CLI："
                f"{LARK_CLI_INSTALL_COMMAND}；"
                "安装后运行 lark-cli config init --new 完成配置，再重试本次同步"
            )
        command = [direct]
    explicit_override = profile_override is not USE_ACTIVE_PROFILE
    selected_profile = (
        ACTIVE_PROFILE
        if profile_override is USE_ACTIVE_PROFILE
        else profile_override
    )
    if "--profile" in command:
        index = command.index("--profile")
        if explicit_override and selected_profile is None:
            del command[index : index + 2]
        elif selected_profile:
            configured_profile = (
                command[index + 1] if index + 1 < len(command) else ""
            )
            if configured_profile != selected_profile:
                raise SyncError(
                    "LARK_CLI_CMD 中的 --profile 与本次选择不一致"
                )
    elif selected_profile:
        command.extend(["--profile", str(selected_profile)])
    return command


def run_cli(args: list[str], cwd: Path) -> dict[str, Any]:
    command = [*resolve_cli(), *args]
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    values = json_objects(process.stdout) + json_objects(process.stderr)
    payload = next(
        (value for value in reversed(values) if isinstance(value, dict) and "ok" in value),
        None,
    )
    if process.returncode != 0 or not payload or payload.get("ok") is not True:
        detail = cli_failure_detail(process, payload)
        raise SyncError(f"lark-cli 调用失败: {json.dumps(detail, ensure_ascii=False)}")
    return payload


def run_cli_json(
    args: list[str],
    cwd: Path,
    *,
    profile_override: str | None | object = USE_ACTIVE_PROFILE,
) -> Any:
    command = [*resolve_cli(profile_override), *args]
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    values = json_objects(process.stdout) + json_objects(process.stderr)
    if process.returncode != 0 or not values:
        detail = cli_failure_detail(process, values[0] if values else None)
        raise SyncError(
            f"lark-cli 调用失败: {json.dumps(detail, ensure_ascii=False)}"
        )
    return values[0]


def complete_device_authorization(device_code: str, cwd: Path) -> dict[str, Any]:
    """Complete OAuth only when all dynamically requested scopes were granted."""
    command = [
        *resolve_cli(),
        "auth",
        "login",
        "--device-code",
        device_code,
        "--json",
    ]
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    values = json_objects(process.stdout) + json_objects(process.stderr)
    payload = next(
        (
            value
            for value in values
            if isinstance(value, dict)
            and value.get("event") == "authorization_complete"
        ),
        None,
    )
    missing = sorted(
        str(scope)
        for scope in (payload.get("missing") or [])
    ) if payload else []
    if missing:
        raise SyncError(
            "飞书未完整授予本次 OAuth 权限："
            f"{'、'.join(missing)}。"
            "请先在应用后台开通全部缺失权限，再对同一 Profile 重新运行 "
            "library authorize；不要按功能逐项重复授权"
        )
    if process.returncode != 0:
        detail = cli_failure_detail(process, payload)
        raise SyncError(
            f"lark-cli 调用失败: {json.dumps(detail, ensure_ascii=False)}"
        )
    if payload is None:
        raise SyncError("lark-cli 未返回完整的 OAuth 授权结果")
    return payload


def list_profiles(cwd: Path) -> list[dict[str, Any]]:
    payload = run_cli_json(
        ["profile", "list"],
        cwd,
        profile_override=None,
    )
    if not isinstance(payload, list) or not all(
        isinstance(profile, dict) for profile in payload
    ):
        raise SyncError("无法读取 lark-cli profiles")
    return payload


def public_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for profile in profiles:
        token_status = str(
            profile.get("tokenStatus")
            or profile.get("token_status")
            or ""
        )
        item: dict[str, Any] = {
            "name": str(profile.get("name") or ""),
            "active": bool(profile.get("active")),
            "user": str(profile.get("user") or ""),
            "token_status": token_status,
        }
        if token_status == "needs_refresh":
            item.update(
                {
                    "token_status_is_advisory": True,
                    "reauthorization_required": False,
                    "next_action": "try_read_only_discover",
                }
            )
        public.append(item)
    return public


def profile_name(profile: dict[str, Any]) -> str:
    return str(profile.get("name") or "")


def validate_profile_name(target_profile: str) -> str:
    if not PROFILE_NAME_RE.fullmatch(target_profile):
        raise SyncError(
            "Profile 名称只能使用 1-64 位英文字母、数字、点、"
            "下划线或连字符，且必须以字母或数字开头"
        )
    return target_profile


def exec_new_profile_setup(target_profile: str) -> None:
    """Replace this process with lark-cli's streamed new-app setup."""
    target_profile = validate_profile_name(target_profile)
    command = [
        *resolve_cli(profile_override=None),
        "config",
        "init",
        "--new",
        "--name",
        target_profile,
        "--force-init",
    ]
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    os.execvpe(command[0], command, env)


def authorization_result(payload: Any, profile: str) -> dict[str, Any]:
    verification_url = str(
        find_key(
            payload,
            {
                "verification_uri_complete",
                "verification_url",
                "verification_uri",
            },
        )
        or ""
    )
    device_code = str(find_key(payload, {"device_code"}) or "")
    if not verification_url or not device_code:
        raise SyncError("lark-cli 未返回完整的 OAuth 验证地址和临时代码")
    return {
        "ok": True,
        "action": "oauth_authorization_required",
        "profile": profile,
        "verification_url": verification_url,
        "user_code": str(find_key(payload, {"user_code"}) or ""),
        "device_code": device_code,
        "expires_in": find_key(payload, {"expires_in"}),
        "recommended_permissions": {
            "scopes": list(EXPLICIT_AUTH_SCOPES),
        },
        "authorization_mode": "explicit_minimum_scopes",
        "scope_source": "feishu_skill_sync_call_chain",
        "message": (
            "原样展示 verification_url 并等待用户选择目标飞书身份。"
            "用户确认完成授权后，使用本次 device_code 完成登录；"
            "本次只请求同步脚本真实调用链所需的最小权限集合，"
            "并包含 offline_access；"
            "不得立即阻塞轮询、重新发起授权或缓存该临时代码"
        ),
    }


def authorize_profile(
    target_profile: str,
    device_code: str | None,
    cwd: Path,
) -> dict[str, Any]:
    target_profile = validate_profile_name(target_profile)
    profiles = list_profiles(cwd)
    if target_profile not in {profile_name(profile) for profile in profiles}:
        if device_code:
            raise SyncError(f"要完成授权的 Profile 不存在: {target_profile}")
        exec_new_profile_setup(target_profile)
        raise SyncError("新 Profile 创建流程未能启动")
    if device_code:
        complete_device_authorization(device_code, cwd)
        account = current_account(cwd)
        return {
            "ok": True,
            "action": "oauth_authorized",
            "profile": target_profile,
            "display": (
                f"Profile：{target_profile}\n"
                f"OAuth 用户：{user_label(account.name, account.open_id)}"
            ),
            "permissions_complete": True,
            "authorized_scopes": list(EXPLICIT_AUTH_SCOPES),
            "scope_source": "feishu_skill_sync_call_chain",
            "next_action": (
                "运行 library discover --lark-profile <本 Profile> "
                "查找该身份下的知识库与 Skill 目录"
            ),
        }
    payload = run_cli_json(
        [
            "auth",
            "login",
            "--scope",
            " ".join(EXPLICIT_AUTH_SCOPES),
            "--no-wait",
            "--json",
        ],
        cwd,
    )
    return authorization_result(payload, target_profile)


def ensure_publish_permissions(cwd: Path) -> dict[str, Any]:
    """Fail before remote writes when any publishing permission is missing."""
    scope_text = " ".join(REQUIRED_PUBLISH_SCOPES)
    try:
        payload = run_cli_json(
            ["auth", "check", "--scope", scope_text, "--json"],
            cwd,
        )
    except SyncError as exc:
        raise SyncError(
            "发布所需的飞书权限未一次性授予，写入尚未开始。"
            "请对当前 Profile 重新运行 library authorize；"
            "它只会申请同步真实调用链所需权限（包括 offline_access 和 "
            f"{scope_text}）"
        ) from exc
    if not isinstance(payload, dict):
        raise SyncError("无法确认飞书发布权限")
    missing = payload.get("missing") or []
    granted = set(payload.get("granted") or [])
    if missing or not set(REQUIRED_PUBLISH_SCOPES).issubset(granted):
        raise SyncError(
            "缺少发布所需权限，写入尚未开始："
            f"{'、'.join(str(scope) for scope in missing) or scope_text}。"
            "请重新运行 library authorize 一次性完成授权"
        )
    return {
        "scopes": list(REQUIRED_PUBLISH_SCOPES),
    }


def current_account(cwd: Path, label: str | None = None) -> AccountInfo:
    effective = run_cli_json(["whoami"], cwd)
    if not isinstance(effective, dict):
        raise SyncError("无法读取当前 lark-cli profile")
    lark_profile = str(effective.get("profile") or "")
    if not lark_profile:
        raise SyncError("当前 lark-cli 身份缺少 profile")
    payload = run_cli_json(
        ["auth", "status", "--verify", "--json"],
        cwd,
    )
    if not isinstance(payload, dict):
        raise SyncError("无法读取当前飞书登录账号")
    identities = payload.get("identities")
    user = identities.get("user") if isinstance(identities, dict) else None
    if (
        not isinstance(user, dict)
        or user.get("available") is not True
        or user.get("verified") is not True
    ):
        raise SyncError(
            "当前飞书 user 身份未授权。请先运行 lark-cli auth login，"
            "并让用户完成授权"
        )
    name = str(user.get("userName") or "未命名用户")
    open_id = str(user.get("openId") or "")
    if not open_id:
        raise SyncError("当前飞书登录状态缺少 user Open ID，无法安全绑定账号")
    identity = run_cli_json(
        [
            "api",
            "GET",
            "/open-apis/authen/v1/user_info",
            "--as",
            "user",
            "--jq",
            "{name: .data.name, open_id: .data.open_id, "
            "tenant_key: .data.tenant_key}",
        ],
        cwd,
    )
    if not isinstance(identity, dict):
        raise SyncError("无法读取当前飞书用户的租户指纹")
    identity_open_id = str(identity.get("open_id") or "")
    tenant_key = str(identity.get("tenant_key") or "")
    if not tenant_key:
        raise SyncError("当前飞书用户信息缺少 tenant_key，无法安全绑定账号")
    if identity_open_id and identity_open_id != open_id:
        raise SyncError("飞书授权状态与用户信息不一致，请重新登录后再绑定")
    confirmed_label = (label or name).strip()
    if not confirmed_label:
        raise SyncError("飞书账号备注不能为空")
    return AccountInfo(
        label=confirmed_label,
        name=name,
        open_id=open_id,
        tenant_key=tenant_key,
        brand=str(payload.get("brand") or "feishu"),
        lark_profile=lark_profile,
    )


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def find_key(value: Any, names: set[str]) -> Any:
    for item in walk(value):
        if isinstance(item, dict):
            for name in names:
                if name in item and item[name] not in (None, ""):
                    return item[name]
    return None


def find_node_dict(payload: dict[str, Any]) -> dict[str, Any]:
    for item in walk(payload):
        if isinstance(item, dict) and item.get("node_token") and item.get("obj_token"):
            return item
    raise SyncError("lark-cli 返回中找不到 Wiki node")


def extract_node_token(payload: dict[str, Any], cwd: Path) -> tuple[str, str]:
    node_token = find_key(payload, {"node_token", "wiki_token"})
    doc_id = find_key(payload, {"document_id", "doc_id", "obj_token"})
    if node_token:
        return str(node_token), str(doc_id or "")
    for item in walk(payload):
        if isinstance(item, str):
            match = WIKI_TOKEN_RE.search(item)
            if match:
                return match.group(1), str(doc_id or "")
    if doc_id:
        resolved = wiki_get_node(str(doc_id), cwd, obj_type="docx")
        return str(resolved["node_token"]), str(resolved["obj_token"])
    raise SyncError("创建成功但无法解析 Wiki node_token")


def wiki_get_node(token: str, cwd: Path, obj_type: str | None = None) -> dict[str, Any]:
    token = token.strip()
    node_reference = token
    if not obj_type and not re.match(r"^https?://", token, re.IGNORECASE):
        node_reference = f"https://feishu.cn/wiki/{token}"
    args = [
        "wiki",
        "+node-get",
        "--as",
        "user",
        "--node-token",
        node_reference,
        "--format",
        "json",
    ]
    if obj_type:
        args.extend(["--obj-type", obj_type])
    return find_node_dict(run_cli(args, cwd))


def update_wiki_node_title(page: PageInfo, title: str, cwd: Path) -> None:
    """Update a Wiki node title through the official raw OpenAPI endpoint."""
    run_cli(
        [
            "api",
            "POST",
            (
                f"/open-apis/wiki/v2/spaces/{page.space_id}/nodes/"
                f"{page.node_token}/update_title"
            ),
            "--as",
            "user",
            "--data",
            json.dumps({"title": title}, ensure_ascii=False),
            "--format",
            "json",
        ],
        cwd,
    )


def list_nodes(space_id: str, cwd: Path, parent: str | None = None) -> list[dict[str, Any]]:
    args = [
        "wiki",
        "+node-list",
        "--as",
        "user",
        "--space-id",
        space_id,
        "--page-all",
        "--page-limit",
        "20",
        "--format",
        "json",
    ]
    if parent:
        args.extend(["--parent-node-token", parent])
    payload = run_cli(args, cwd)
    for item in walk(payload):
        if isinstance(item, list) and all(isinstance(node, dict) for node in item):
            if item and all("node_token" in node for node in item):
                return item
    return []


def list_spaces(cwd: Path) -> list[dict[str, Any]]:
    payload = run_cli(
        [
            "wiki",
            "+space-list",
            "--as",
            "user",
            "--page-all",
            "--page-limit",
            "20",
            "--format",
            "json",
        ],
        cwd,
    )
    for item in walk(payload):
        if isinstance(item, list) and all(isinstance(space, dict) for space in item):
            if item and all("space_id" in space and "name" in space for space in item):
                return item
    return []


def scan_wiki_nodes(
    roots: list[dict[str, Any]],
    cwd: Path,
) -> list[tuple[dict[str, Any], str]]:
    found: list[tuple[dict[str, Any], str]] = []
    stack: list[tuple[dict[str, Any], list[str], int]] = [
        (node, [str(node.get("title") or "未命名目录")], 0)
        for node in reversed(roots)
    ]
    seen: set[str] = set()
    while stack:
        node, titles, depth = stack.pop()
        token = str(node.get("node_token") or "")
        if not token or token in seen:
            continue
        if depth > 32:
            raise SyncError("知识库目录层级超过 32 层，停止自动发现")
        seen.add(token)
        found.append((node, " / ".join(titles)))
        if not node.get("has_child"):
            continue
        space_id = str(node.get("space_id") or "")
        if not space_id:
            raise SyncError("知识库节点缺少 space_id，无法继续发现目录")
        children = list_nodes(space_id, cwd, token)
        for child in reversed(children):
            child_title = str(child.get("title") or "未命名目录")
            stack.append((child, [*titles, child_title], depth + 1))
    return found


def directory_match_kind(title: str, directory_name: str) -> str | None:
    if title.casefold() == directory_name.casefold():
        return "exact"
    if directory_name.casefold() != "skills":
        return None
    normalized = re.sub(r"[\s_-]+", "", title.casefold())
    return (
        "related"
        if "skill" in normalized or "技能" in normalized
        else None
    )


def knowledge_base_match_kind(title: str) -> str:
    normalized = re.sub(r"[\s_-]+", "", title.casefold())
    exact_names = {
        "技能库",
        "skill库",
        "skills库",
        "agent库",
        "agents库",
        "智能体库",
        "agent技能库",
    }
    if normalized in exact_names:
        return "exact"
    related_terms = (
        "技能",
        "skill",
        "agent",
        "智能体",
    )
    if any(term in normalized for term in related_terms):
        return "related"
    generic_terms = (
        "知识",
        "knowledge",
        "wiki",
        "资料库",
        "文档库",
    )
    return "generic" if any(term in normalized for term in generic_terms) else "other"


def discover_library_candidates(
    cwd: Path,
    directory_name: str = "skills",
    knowledge_base: str | None = None,
) -> tuple[AccountInfo, list[dict[str, Any]], list[dict[str, Any]]]:
    directory_name = directory_name.strip()
    if not directory_name:
        raise SyncError("目录名称不能为空")
    account = current_account(cwd)
    sources: list[tuple[str, list[dict[str, Any]]]] = []

    personal_roots = list_nodes("my_library", cwd)
    if personal_roots and (knowledge_base is None or knowledge_base == "我的知识库"):
        sources.append(("我的知识库", personal_roots))

    for space in list_spaces(cwd):
        title = str(space.get("name") or "")
        if not title or (knowledge_base is not None and title != knowledge_base):
            continue
        sources.append(
            (
                title,
                list_nodes(str(space.get("space_id") or ""), cwd),
            )
        )

    if knowledge_base is not None and not any(
        title == knowledge_base for title, _ in sources
    ):
        raise SyncError(f"当前账号下找不到知识库: {knowledge_base!r}")
    sources.sort(
        key=lambda item: {
            "exact": 0,
            "related": 1,
            "generic": 2,
            "other": 3,
        }[knowledge_base_match_kind(item[0])]
    )

    knowledge_bases: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for title, roots in sources:
        matches: list[dict[str, Any]] = []
        for node, directory_path in scan_wiki_nodes(roots, cwd):
            match_kind = directory_match_kind(
                str(node.get("title") or ""),
                directory_name,
            )
            if node.get("obj_type") == "docx" and match_kind:
                matches.append(
                    {
                        "knowledge_base": title,
                        "knowledge_base_match_kind": knowledge_base_match_kind(title),
                        "directory_path": directory_path,
                        "match_kind": match_kind,
                        "_node": node,
                    }
                )
        matches.sort(key=lambda item: item["match_kind"] != "exact")
        knowledge_bases.append(
            {
                "title": title,
                "match_kind": knowledge_base_match_kind(title),
                "candidate_count": len(matches),
            }
        )
        candidates.extend(matches)
    return account, knowledge_bases, candidates


def public_discovery(
    account: AccountInfo,
    knowledge_bases: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    directory_name: str,
) -> dict[str, Any]:
    public_candidates = [
        {
            "knowledge_base": candidate["knowledge_base"],
            "knowledge_base_match_kind": candidate.get(
                "knowledge_base_match_kind",
                knowledge_base_match_kind(candidate["knowledge_base"]),
            ),
            "directory_path": candidate["directory_path"],
            "match_kind": candidate.get("match_kind", "exact"),
        }
        for candidate in candidates
    ]
    default_candidate = public_candidates[0] if public_candidates else None
    display = (
        f"Profile：{account.lark_profile}\n"
        f"OAuth 用户：{user_label(account.name, account.open_id)}"
    )
    if default_candidate:
        display += (
            f"\n目前位置：{default_candidate['knowledge_base']} / "
            f"{default_candidate['directory_path']}"
        )
    return {
        "ok": True,
        "action": "library_candidates",
        "display": display,
        "directory_name": directory_name,
        "knowledge_bases": knowledge_bases,
        "default_candidate": default_candidate,
        "candidates": public_candidates,
        "next_action": (
            "确认 Profile、OAuth 用户和默认位置；用户可确认或直接指定其他候选。"
            "若账号不对，必须切换或新建独立 Profile 后重新 discover，"
            "不得改写账号备注继续绑定"
            if default_candidate
            else (
                "未找到 Skill 目录；请让用户指定或先创建目录。"
                "若账号不对，必须切换或新建独立 Profile 后重新 discover"
            )
        ),
    }


def bind_library_by_path(
    knowledge_base: str,
    directory_path: str,
    cwd: Path,
    account_label: str | None = None,
) -> LibraryInfo:
    account, _, candidates = discover_library_candidates(
        cwd,
        directory_name=directory_path.rsplit(" / ", 1)[-1],
        knowledge_base=knowledge_base,
    )
    matches = [
        candidate
        for candidate in candidates
        if candidate["knowledge_base"] == knowledge_base
        and candidate["directory_path"] == directory_path
    ]
    if not matches:
        raise SyncError(
            f"知识库 {knowledge_base!r} 中找不到目录路径: {directory_path!r}"
        )
    if len(matches) > 1:
        raise SyncError(
            "存在多个相同知识库名称和目录路径，请改用 --root 指定准确节点"
        )
    confirmed_account = AccountInfo(
        label=(account_label or account.name).strip(),
        name=account.name,
        open_id=account.open_id,
        tenant_key=account.tenant_key,
        brand=account.brand,
        lark_profile=account.lark_profile,
    )
    if not confirmed_account.label:
        raise SyncError("飞书账号备注不能为空")
    node = matches[0]["_node"]
    return library_from_node(
        node,
        confirmed_account,
        knowledge_base,
        directory_path,
    )


def bind_default_library(
    cwd: Path,
    account_label: str | None = None,
) -> LibraryInfo:
    account, _, candidates = discover_library_candidates(cwd)
    if not candidates:
        raise SyncError(
            "当前用户可见知识库中找不到 skills 或常见 Skill 库目录；"
            "请指定目录或先创建目录"
        )
    selected = candidates[0]
    confirmed_account = AccountInfo(
        label=(account_label or account.name).strip(),
        name=account.name,
        open_id=account.open_id,
        tenant_key=account.tenant_key,
        brand=account.brand,
        lark_profile=account.lark_profile,
    )
    return library_from_node(
        selected["_node"],
        confirmed_account,
        selected["knowledge_base"],
        selected["directory_path"],
    )


def fetch_doc(doc_token: str, cwd: Path) -> str:
    payload = run_cli(
        [
            "docs",
            "+fetch",
            "--as",
            "user",
            "--doc",
            doc_token,
            "--doc-format",
            "markdown",
            "--detail",
            "simple",
            "--scope",
            "full",
            "--format",
            "json",
        ],
        cwd,
    )
    content = find_key(payload, {"content", "markdown"})
    if not isinstance(content, str):
        raise SyncError("文档读取成功但返回中找不到正文")
    return content


def normalize_root(root: str) -> str:
    root = root.strip()
    match = WIKI_TOKEN_RE.search(root)
    return match.group(1) if match else root


def root_info(root: str, cwd: Path) -> dict[str, Any]:
    return wiki_get_node(normalize_root(root), cwd)


def resolve_space_title(space_id: str, cwd: Path) -> str:
    for space in list_spaces(cwd):
        if str(space.get("space_id") or "") == space_id:
            return str(space.get("name") or "")
    personal_roots = list_nodes("my_library", cwd)
    if any(str(node.get("space_id") or "") == space_id for node in personal_roots):
        return "我的知识库"
    raise SyncError(
        "可以访问目标目录，但无法识别其所属知识库名称；"
        "请确认当前飞书账号和知识库权限后重试"
    )


def resolve_directory_path(node: dict[str, Any], cwd: Path) -> str:
    titles: list[str] = []
    current = node
    seen: set[str] = set()
    for _ in range(32):
        token = str(current.get("node_token") or "")
        if not token or token in seen:
            break
        seen.add(token)
        titles.append(str(current.get("title") or "未命名目录"))
        parent = str(current.get("parent_node_token") or "")
        if not parent:
            break
        current = wiki_get_node(parent, cwd)
    else:
        raise SyncError("知识库目录层级过深，无法生成安全路径")
    return " / ".join(reversed(titles))


def library_from_node(
    node: dict[str, Any],
    account: AccountInfo,
    space_title: str,
    directory_path: str,
    requested_root: str | None = None,
) -> LibraryInfo:
    node_token = str(node.get("node_token") or "")
    space_id = str(node.get("space_id") or "")
    if not node_token or not space_id:
        raise SyncError("知识库节点缺少 node_token 或 space_id")
    requested_root = requested_root or node_token
    return LibraryInfo(
        root=node_token,
        title=str(node.get("title") or "未命名知识库节点"),
        node_token=node_token,
        space_id=space_id,
        account_label=account.label,
        account_name=account.name,
        account_open_id=account.open_id,
        account_tenant_key=account.tenant_key,
        brand=account.brand,
        lark_profile=account.lark_profile,
        space_title=space_title,
        directory_path=directory_path,
        obj_token=str(node.get("obj_token") or ""),
        url=str(node.get("url") or (requested_root if "://" in requested_root else "")),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def bind_library(
    root: str,
    cwd: Path,
    account_label: str | None = None,
) -> LibraryInfo:
    account = current_account(cwd, account_label)
    node = root_info(root, cwd)
    space_id = str(node.get("space_id") or "")
    return library_from_node(
        node,
        account,
        resolve_space_title(space_id, cwd),
        resolve_directory_path(node, cwd),
        root,
    )


def find_skill_pages(root: str, cwd: Path) -> list[PageInfo]:
    root_node = root_info(root, cwd)
    space_id = str(root_node["space_id"])
    pages: list[PageInfo] = []
    for node in list_nodes(space_id, cwd, str(root_node["node_token"])):
        if node.get("obj_type") != "docx":
            continue
        try:
            content = fetch_doc(str(node["obj_token"]), cwd)
            name, version, package = parse_page_markdown(content)
        except SyncError:
            continue
        pages.append(
            PageInfo(
                name=name,
                version=version,
                package=package,
                title=str(node.get("title") or name),
                node_token=str(node["node_token"]),
                obj_token=str(node["obj_token"]),
                space_id=space_id,
                url=str(node.get("url") or ""),
            )
        )
    return pages


def find_skill_page(root: str, name: str, cwd: Path) -> PageInfo | None:
    matches = [page for page in find_skill_pages(root, cwd) if page.name == name]
    if len(matches) > 1:
        raise SyncError(f"中央库存在多个同名 Skill 页面: {name}")
    return matches[0] if matches else None


def create_doc(
    cwd: Path,
    title: str,
    content: str,
    parent_token: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    content_file = cwd / "_page.md"
    content_file.write_text(content, encoding="utf-8")
    args = [
        "docs",
        "+create",
        "--as",
        "user",
        "--title",
        title,
        "--doc-format",
        "markdown",
        "--content",
        f"@{content_file.name}",
        "--format",
        "json",
    ]
    if parent_token:
        args.extend(["--parent-token", parent_token])
    payload = run_cli(args, cwd)
    node_token, doc_id = extract_node_token(payload, cwd)
    return payload, node_token, doc_id


def is_skill_package_title(name: str, title: str) -> bool:
    if title == f"{name}.zip":
        return True
    match = re.fullmatch(rf"{re.escape(name)}-(.+)\.zip", title)
    if not match:
        return False
    try:
        validate_version(match.group(1))
    except SyncError:
        return False
    return True


def package_node(page: PageInfo, cwd: Path) -> dict[str, Any] | None:
    files = [
        node
        for node in list_nodes(page.space_id, cwd, page.node_token)
        if node.get("obj_type") == "file"
        and is_skill_package_title(page.name, str(node.get("title") or ""))
    ]
    if len(files) > 1:
        titles = [str(node.get("title") or "") for node in files]
        raise SyncError(f"页面下存在多个 Skill 包，请先人工处理: {titles}")
    return files[0] if files else None
