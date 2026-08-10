"""Publish and pull Agent Skill folders through a Feishu Wiki library."""

import argparse
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .core import (
    LibraryInfo,
    SyncError,
    cache_file,
    clear_library_cache,
    compare_versions,
    confirmation_code,
    emit,
    load_library_cache,
    location_label,
    public_library,
    read_skill,
    redact_location_data,
    save_library_cache,
    set_pretty_output,
    validate_version,
)
from .lark import (
    authorize_profile,
    bind_default_library,
    bind_library,
    bind_library_by_path,
    cli_setup_guidance,
    discover_library_candidates,
    find_skill_pages,
    lark_cli_available,
    list_profiles,
    public_profiles,
    public_discovery,
    set_lark_profile,
)
from .operations import (
    plan_publish_skills,
    plan_pull_skills,
    publish_skills,
    pull_skills,
)


def with_workspace(callback: Any) -> Any:
    base = Path.cwd()
    with tempfile.TemporaryDirectory(prefix=".skill-sync-", dir=base) as temp:
        return callback(Path(temp))


def requires_lark_cli(args: argparse.Namespace) -> bool:
    if args.command in {"list", "publish", "pull"}:
        return True
    return args.command == "library" and args.library_command not in {
        "show",
        "clear",
    }


def validate_arguments(args: argparse.Namespace) -> None:
    if getattr(args, "account_label", None) is not None:
        raise SyncError(
            "--account-label 已停用：账号描述或备注不能切换 OAuth 身份。"
            "请删除该参数，并通过 library profiles 选择独立且已授权的 "
            "--lark-profile；若目标 Profile 不存在，先创建 Profile 并完成 OAuth"
        )


def bind_library_selection(args: argparse.Namespace, cwd: Path) -> LibraryInfo:
    if args.root:
        if args.knowledge_base or args.directory_path:
            raise SyncError(
                "--root 不能与 --knowledge-base 或 --directory-path 同时使用"
            )
        return bind_library(args.root, cwd)
    if not args.knowledge_base and not args.directory_path:
        return bind_default_library(cwd)
    if not args.knowledge_base or not args.directory_path:
        raise SyncError(
            "--knowledge-base 与 --directory-path 必须同时提供；"
            "两者都省略时自动选择第一个相关候选"
        )
    return bind_library_by_path(
        args.knowledge_base,
        args.directory_path,
        cwd,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="默认输出紧凑 JSON；仅在人类调试时使用 --pretty。",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="缩进 JSON 输出；默认使用更省 Token 的紧凑 JSON",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="校验并读取一个本地 Skill",
        description="只读校验本地 Skill 的名称、标题和版本。",
    )
    inspect_parser.add_argument("skill_dir", type=Path)

    compare_parser = subparsers.add_parser(
        "compare",
        help="比较两个 SemVer",
        description="比较两个三段式 SemVer；主要用于调试。",
    )
    compare_parser.add_argument("left")
    compare_parser.add_argument("right")

    library_parser = subparsers.add_parser(
        "library",
        help="发现、绑定和查看中央库位置",
        description=(
            "发现知识库中的候选目录，或按“已验证用户 / 知识库 / 目录”"
            "预检并绑定中央库。"
        ),
    )
    library_subparsers = library_parser.add_subparsers(
        dest="library_command",
        required=True,
    )
    library_discover_parser = library_subparsers.add_parser(
        "discover",
        help="只读查找可用知识库和 skills 目录",
        description=(
            "验证当前飞书 user 身份，列出可用知识库并递归查找候选目录；"
            "不缓存、不写入飞书。"
        ),
    )
    library_discover_parser.add_argument(
        "--directory-name",
        default="skills",
        help="要查找的目录名称，默认 skills",
    )
    library_discover_parser.add_argument(
        "--knowledge-base",
        help="只扫描指定知识库；默认扫描当前账号可见的全部知识库",
    )
    library_discover_parser.add_argument(
        "--lark-profile",
        required=True,
        help="明确指定一个已授权为目标账号的 lark-cli profile",
    )

    library_set_parser = library_subparsers.add_parser(
        "set",
        help="预检或确认绑定中央库位置",
        description=(
            "默认绑定 CLI 发现的第一个知识库/Skill 目录候选；"
            "也可指定知识库路径或 --root。无 --confirm 时只预检。"
        ),
    )
    library_set_parser.add_argument(
        "--root",
        help="目标目录 URL 或节点；不能与名称路径参数同时使用",
    )
    library_set_parser.add_argument(
        "--knowledge-base",
        help="discover 返回的知识库名称",
    )
    library_set_parser.add_argument(
        "--directory-path",
        help="discover 返回的完整目录路径",
    )
    library_set_parser.add_argument(
        "--account-label",
        help=(
            "已停用；账号描述不能切换 OAuth 身份。"
            "请使用独立且已授权的 --lark-profile"
        ),
    )
    library_set_parser.add_argument(
        "--lark-profile",
        required=True,
        help="与 discover 相同且已验证为目标账号的 lark-cli profile",
    )
    library_set_parser.add_argument(
        "--confirm",
        metavar="CODE",
        help="用户确认预检计划后返回的确认码",
    )
    library_subparsers.add_parser(
        "show",
        help="验证并显示已缓存位置",
    )
    library_subparsers.add_parser(
        "clear",
        help="清除本地位置缓存，不删除飞书内容",
    )
    library_subparsers.add_parser(
        "profiles",
        help="只读列出 lark-cli profiles 与当前授权用户",
    )
    library_authorize_parser = library_subparsers.add_parser(
        "authorize",
        help="为现有或新 Profile 发起并完成 OAuth",
        description=(
            "目标 Profile 不存在时，使用官方 config init --new "
            "创建独立应用与 Profile；存在时发起 OAuth。"
        ),
    )
    library_authorize_parser.add_argument(
        "--lark-profile",
        required=True,
        help="要授权的独立 Profile 名称；不存在时自动创建",
    )
    library_authorize_parser.add_argument(
        "--device-code",
        help="用户完成网页授权后，传入同一次授权返回的临时代码",
    )

    subparsers.add_parser(
        "list",
        help="列出中央库中的 Skills",
        description="验证缓存身份和位置后，只读列出已发布的 Skills。",
    )

    publish_parser = subparsers.add_parser(
        "publish",
        help="发布或更新一个或多个 Skills",
        description=(
            "每个来源既可为 Skill 目录，也可为含多个 Skills 的父目录。"
            "统一比较全部本地和远端版本，只生成一份确认计划。"
        ),
    )
    publish_parser.add_argument(
        "--source",
        required=True,
        nargs="+",
        type=Path,
        help="一个或多个 Skill 目录或 Skills 父目录",
    )
    publish_parser.add_argument(
        "--notes",
        help="本次所有新版本共用的简要更新要点，最多 500 字",
    )
    publish_parser.add_argument(
        "--confirm",
        metavar="CODE",
        help="用户确认预检计划后返回的确认码",
    )

    pull_parser = subparsers.add_parser(
        "pull",
        help="拉取或更新中央库中的 Skills",
        description=(
            "默认同步中央库全部 Skills；可用 --name 选择一个或多个。"
            "统一比较全部远端和本地版本，只生成一份确认计划。"
        ),
    )
    pull_parser.add_argument(
        "--skills-dir",
        required=True,
        type=Path,
        help=(
            "当前 Agent 自行解析的标准 Skills 根目录；"
            "脚本不提供平台默认路径"
        ),
    )
    pull_parser.add_argument(
        "--name",
        nargs="+",
        help="只同步指定的一个或多个 Skill；省略时同步中央库全部",
    )
    pull_parser.add_argument(
        "--confirm",
        metavar="CODE",
        help="用户确认整份计划后返回的确认码",
    )
    return parser


def require_confirmation(
    operation: str,
    plan: dict[str, Any],
    library: LibraryInfo,
    confirmation: str | None,
) -> bool:
    include_identity = operation == "library_set"
    public_target = public_library(
        library,
        include_identity=include_identity,
    )
    if not plan["requires_confirmation"]:
        emit(
            {
                "ok": True,
                "action": plan["planned_action"],
                "operation": operation,
                "library": public_target,
                "plan": plan,
            }
        )
        return False
    expected = confirmation_code(library, operation, plan)
    if confirmation is None:
        emit(
            {
                "ok": True,
                "action": "confirmation_required",
                "operation": operation,
                "library": public_target,
                "plan": plan,
                "confirmation_code": expected,
                "message": (
                    "首次绑定原样展示 library.display；缓存复用时只展示位置。"
                    "再按需补充 Skill、版本和动作。"
                    "用户回复“确认”即可执行；修改则重新预检"
                ),
                "reply_options": ["确认", "修改：<直接说明修改项>"],
            }
        )
        return False
    if confirmation != expected:
        raise SyncError("确认码与当前目标位置或计划不一致，请重新预检并让用户确认")
    return True


def main() -> int:
    args = build_parser().parse_args()
    set_pretty_output(args.pretty)
    try:
        validate_arguments(args)
        if requires_lark_cli(args) and not lark_cli_available():
            emit(cli_setup_guidance())
            return 0
        if args.command == "inspect":
            emit({"ok": True, "skill": asdict(read_skill(args.skill_dir))})
        elif args.command == "compare":
            relation = compare_versions(args.left, args.right)
            emit(
                {
                    "ok": True,
                    "left": validate_version(args.left),
                    "right": validate_version(args.right),
                    "result": relation,
                    "relation": "greater" if relation > 0 else "less" if relation < 0 else "equal",
                }
            )
        elif args.command == "library":
            if args.library_command == "discover":
                set_lark_profile(args.lark_profile)
                account, knowledge_bases, candidates = with_workspace(
                    lambda cwd: discover_library_candidates(
                        cwd,
                        args.directory_name,
                        args.knowledge_base,
                    )
                )
                emit(
                    public_discovery(
                        account,
                        knowledge_bases,
                        candidates,
                        args.directory_name,
                    )
                )
            elif args.library_command == "set":
                set_lark_profile(args.lark_profile)
                library = with_workspace(
                    lambda cwd: bind_library_selection(args, cwd)
                )
                previous_location: str | None = None
                if cache_file().is_file():
                    try:
                        previous_location = location_label(load_library_cache())
                    except SyncError:
                        previous_location = "现有缓存不可读或需要重新绑定"
                plan = {
                    "planned_action": "cache_library_location",
                    "requires_confirmation": True,
                    "previous_location": previous_location,
                }
                if require_confirmation(
                    "library_set",
                    plan,
                    library,
                    args.confirm,
                ):
                    save_library_cache(library)
                    emit(
                        {
                            "ok": True,
                            "action": "library_cached",
                            "library": public_library(library),
                            "previous_location": previous_location,
                        }
                    )
            elif args.library_command == "show":
                library = load_library_cache()
                emit(
                    {
                        "ok": True,
                        "action": "library_cached",
                        "library": public_library(library),
                        "source": "local_cache",
                        "remote_verified": False,
                    }
                )
            elif args.library_command == "clear":
                emit(
                    {
                        "ok": True,
                        "action": "library_cache_cleared",
                        "removed": clear_library_cache(),
                        "cache_file": str(cache_file()),
                    }
                )
            elif args.library_command == "profiles":
                profiles = with_workspace(list_profiles)
                emit(
                    {
                        "ok": True,
                        "action": "lark_profiles",
                        "profiles": public_profiles(profiles),
                        "token_status_policy": {
                            "needs_refresh": {
                                "token_status_is_advisory": True,
                                "reauthorization_required": False,
                                "next_action": "try_read_only_discover",
                            }
                        },
                        "next_action": (
                            "选择最接近目标账号的已有 Profile，并先运行只读的 "
                            "library discover。token_status=needs_refresh 只表示令牌状态提示，"
                            "不得据此运行 library authorize；应先让 lark-cli 在真实请求中"
                            "处理刷新。只有 discover 明确返回 Token 失效、OAuth 未完成或"
                            "缺少必需权限时，才对同一 Profile 运行 library authorize。"
                            "只有用户确认现有候选都不是目标身份时才创建新 Profile，"
                            "不要反复让用户核对已排除的 Profile。"
                            "用户给出的账号描述不得写入 --account-label，"
                            "也不能代替授权切换"
                        ),
                    }
                )
            elif args.library_command == "authorize":
                set_lark_profile(args.lark_profile)
                result = with_workspace(
                    lambda cwd: authorize_profile(
                        args.lark_profile,
                        args.device_code,
                        cwd,
                    )
                )
                emit(result)
            else:
                raise SyncError(f"未知 library 命令: {args.library_command}")
        elif args.command == "list":
            library = load_library_cache()
            set_lark_profile(library.lark_profile)
            pages = with_workspace(
                lambda cwd: find_skill_pages(library.root, cwd)
            )
            emit(
                {
                    "ok": True,
                    "library": public_library(library, include_identity=False),
                    "count": len(pages),
                    "skills": [
                        {
                            "name": page.name,
                            "version": page.version,
                            "package": page.package,
                            "title": page.title,
                        }
                        for page in pages
                    ],
                }
            )
        elif args.command == "publish":
            library = load_library_cache()
            set_lark_profile(library.lark_profile)
            plan = with_workspace(
                lambda cwd: plan_publish_skills(
                    library.root,
                    args.source,
                    args.notes,
                    cwd,
                )
            )
            if require_confirmation(
                "publish",
                plan,
                library,
                args.confirm,
            ):
                result = with_workspace(
                    lambda cwd: publish_skills(
                        library.root,
                        args.source,
                        args.notes,
                        cwd,
                    )
                )
                emit(
                    {
                        "ok": True,
                        **redact_location_data(result),
                        "library": public_library(
                            library,
                            include_identity=False,
                        ),
                    }
                )
        elif args.command == "pull":
            library = load_library_cache()
            set_lark_profile(library.lark_profile)
            plan = with_workspace(
                lambda cwd: plan_pull_skills(
                    library.root,
                    args.name,
                    args.skills_dir,
                    cwd,
                )
            )
            if require_confirmation(
                "pull",
                plan,
                library,
                args.confirm,
            ):
                result = with_workspace(
                    lambda cwd: pull_skills(
                        library.root,
                        args.name,
                        args.skills_dir,
                        cwd,
                    )
                )
                emit(
                    {
                        "ok": True,
                        **redact_location_data(result),
                        "library": public_library(
                            library,
                            include_identity=False,
                        ),
                    }
                )
        else:
            raise SyncError(f"未知命令: {args.command}")
        return 0
    except (SyncError, OSError, zipfile.BadZipFile) as exc:
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
