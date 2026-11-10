"""Core models, version rules, cache handling, and ZIP safety."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WIKI_TOKEN_RE = re.compile(r"/wiki/([A-Za-z0-9_-]+)")
IGNORED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".DS_Store",
    "tests",
}
CACHE_ENV = "SKILL_SYNC_CACHE_FILE"
CACHE_SCHEMA_VERSION = 5
PRETTY_OUTPUT = False


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillInfo:
    name: str
    version: str
    title: str
    description: str
    path: str
    version_source: str


@dataclass(frozen=True)
class PageInfo:
    name: str
    version: str
    package: str
    title: str
    node_token: str
    obj_token: str
    space_id: str
    url: str = ""


@dataclass(frozen=True)
class AccountInfo:
    label: str
    name: str
    open_id: str
    tenant_key: str
    brand: str
    lark_profile: str


@dataclass(frozen=True)
class LibraryInfo:
    root: str
    title: str
    node_token: str
    space_id: str
    account_label: str
    account_name: str
    account_open_id: str
    account_tenant_key: str
    brand: str
    lark_profile: str
    space_title: str
    directory_path: str
    obj_token: str = ""
    url: str = ""
    updated_at: str = ""


def emit(value: Any) -> None:
    if PRETTY_OUTPUT:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )


def set_pretty_output(enabled: bool) -> None:
    global PRETTY_OUTPUT
    PRETTY_OUTPUT = enabled


def cache_file() -> Path:
    configured = os.environ.get(CACHE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return base / "feishu-skill-sync" / "library.json"


def mask_identifier(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-4:]}"


def confirmation_code(
    library: LibraryInfo,
    operation: str,
    plan: dict[str, Any],
) -> str:
    plan_text = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source = (
        f"{library.account_open_id}\0{library.account_tenant_key}\0"
        f"{library.lark_profile}\0"
        f"{library.node_token}\0"
        f"{library.space_id}\0{operation}\0{plan_text}"
    ).encode()
    return hashlib.sha256(source).hexdigest()[:12]


def location_label(library: LibraryInfo) -> str:
    return f"{library.space_title} / {library.directory_path}"


def user_label(name: str, open_id: str) -> str:
    return f"{name}（{mask_identifier(open_id)}）"


def confirmation_display(library: LibraryInfo) -> str:
    return (
        f"Profile：{library.lark_profile}\n"
        f"OAuth 用户：{user_label(library.account_name, library.account_open_id)}\n"
        f"目前位置：{location_label(library)}"
    )


def public_library(
    library: LibraryInfo,
    include_identity: bool = True,
) -> dict[str, Any]:
    if include_identity:
        return {"display": confirmation_display(library)}
    return {
        "display": f"目前位置：{location_label(library)}",
        "cache_reused": True,
    }


def load_library_cache() -> LibraryInfo:
    path = cache_file()
    if not path.is_file():
        raise SyncError(
            "尚未缓存中央库位置。请先让用户选择并确认知识库位置，"
            "再运行 library set 绑定；不要把 token 写入 Skill 文档或脚本"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"中央库位置缓存无法读取: {path}") from exc
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise SyncError(
            "中央库位置缓存协议已升级，请重新选择已授权的 CLI Profile 并绑定；"
            "旧账号标签不能作为 OAuth 身份证明"
        )
    required = {
        "root",
        "title",
        "node_token",
        "space_id",
        "account_label",
        "account_name",
        "account_open_id",
        "account_tenant_key",
        "brand",
        "lark_profile",
        "space_title",
        "directory_path",
    }
    if not required.issubset(payload) or not all(payload.get(key) for key in required):
        raise SyncError("中央库位置缓存缺少必要字段，请重新绑定")
    return LibraryInfo(
        root=str(payload["root"]),
        title=str(payload["title"]),
        node_token=str(payload["node_token"]),
        space_id=str(payload["space_id"]),
        account_label=str(payload["account_label"]),
        account_name=str(payload["account_name"]),
        account_open_id=str(payload["account_open_id"]),
        account_tenant_key=str(payload["account_tenant_key"]),
        brand=str(payload["brand"]),
        lark_profile=str(payload["lark_profile"]),
        space_title=str(payload["space_title"]),
        directory_path=str(payload["directory_path"]),
        obj_token=str(payload.get("obj_token") or ""),
        url=str(payload.get("url") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def save_library_cache(library: LibraryInfo) -> Path:
    path = cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        **asdict(library),
    }
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path


def clear_library_cache() -> bool:
    path = cache_file()
    if not path.exists():
        return False
    path.unlink()
    return True


def redact_location_data(value: Any) -> Any:
    private_keys = {
        "root",
        "node_token",
        "obj_token",
        "space_id",
        "file_token",
        "page_node_token",
        "account_open_id",
        "account_tenant_key",
        "url",
    }
    if isinstance(value, dict):
        return {
            key: (
                mask_identifier(str(child))
                if key in private_keys and child not in (None, "")
                else redact_location_data(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_location_data(child) for child in value]
    return value


def strip_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    match = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not match:
        return "", text
    return match.group(1), text[match.end() :]


def parse_frontmatter_lines(frontmatter: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def parse_metadata_version(frontmatter: str) -> str:
    """Read metadata.version from inline JSON or an indented YAML mapping."""
    lines = frontmatter.splitlines()
    entries: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() == "metadata":
            entries.append((index, value.strip()))
    if len(entries) > 1:
        raise SyncError("metadata 在 frontmatter 中只能出现一次")
    if not entries:
        return ""

    index, raw = entries[0]
    if raw:
        try:
            metadata = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SyncError(
                "metadata 必须是单行 JSON 对象或 YAML 多行映射"
            ) from exc
        if not isinstance(metadata, dict):
            raise SyncError("metadata 必须是对象")
        value = metadata.get("version")
        return "" if value is None else str(value)

    child_indent: int | None = None
    versions: list[str] = []
    for line in lines[index + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        if child_indent is None:
            child_indent = indent
        if indent != child_indent or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        if key.strip() == "version":
            versions.append(strip_scalar(value))
    if len(versions) > 1:
        raise SyncError("metadata.version 在 frontmatter 中只能出现一次")
    return versions[0] if versions else ""


def validate_version(version: str) -> str:
    version = version.strip().lstrip("v")
    if not SEMVER_RE.fullmatch(version):
        raise SyncError(f"版本必须是三段式 SemVer，实际为: {version!r}")
    return version


def prerelease_key(value: str | None) -> tuple[int, tuple[tuple[int, Any], ...]]:
    if value is None:
        return (1, ())
    parts: list[tuple[int, Any]] = []
    for item in value.split("."):
        if item.isdigit():
            parts.append((0, int(item)))
        else:
            parts.append((1, item))
    return (0, tuple(parts))


def version_key(version: str) -> tuple[int, int, int, tuple[int, tuple[tuple[int, Any], ...]]]:
    match = SEMVER_RE.fullmatch(validate_version(version))
    assert match
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        prerelease_key(match.group("pre")),
    )


def compare_versions(left: str, right: str) -> int:
    left_key = version_key(left)
    right_key = version_key(right)
    return (left_key > right_key) - (left_key < right_key)


def package_filename(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise SyncError(f"无效的 Skill name: {name!r}")
    return f"{name}.zip"


def read_skill(skill_dir: Path) -> SkillInfo:
    skill_dir = skill_dir.expanduser().resolve()
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        raise SyncError(f"找不到 SKILL.md: {skill_file}")
    text = skill_file.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    fields = parse_frontmatter_lines(frontmatter)
    name = strip_scalar(fields.get("name", ""))
    if not name or not NAME_RE.fullmatch(name):
        raise SyncError(f"无效的 Skill name: {name!r}")
    if skill_dir.name != name:
        raise SyncError(f"Skill 文件夹名必须等于 name：{skill_dir.name!r} != {name!r}")

    source = ""
    version = parse_metadata_version(frontmatter)
    if version:
        source = "metadata.version"
    if not version:
        raise SyncError(
            "缺少必需的 metadata.version；不接受顶层 version 或正文版本。"
            "请在 frontmatter 添加，例如 "
            'metadata: {"version":"1.0.0"} 或 metadata:\n  version: "1.0.0"'
        )
    version = validate_version(version)

    title_match = re.search(r"(?m)^#\s+(.+?)\s*$", body)
    title = title_match.group(1).strip() if title_match else name
    description = strip_scalar(fields.get("description", ""))
    return SkillInfo(
        name=name,
        version=version,
        title=title,
        description=description,
        path=str(skill_dir),
        version_source=source,
    )


def normalize_update_notes(notes: str | None) -> str:
    if not notes:
        return ""
    normalized = " ".join(notes.split())
    if len(normalized) > 500:
        raise SyncError("更新要点不能超过 500 个字符")
    return normalized


def extract_update_history(text: str, current_version: str) -> str:
    page_head = text.split("## Skill 说明", 1)[0]
    current_match = re.search(
        r"(?ms)^## 本次更新[ \t]*\r?\n(.*?)(?=^##\s+|\Z)",
        page_head,
    )
    if current_match:
        current = current_match.group(1)
        date_match = re.search(
            r"(?m)^-\s*更新日期：`?([^`\r\n]+)`?\s*$",
            current,
        )
        notes_match = re.search(r"(?m)^-\s*更新要点：(.+?)\s*$", current)
        if not notes_match:
            return ""
        date = (
            date_match.group(1).strip()
            if date_match
            else datetime.now().astimezone().date().isoformat()
        )
        return f"- `{current_version}`（{date}）：{notes_match.group(1).strip()}"

    history_pattern = re.compile(
        r"(?ms)^## 版本更新[ \t]*\r?\n(.*?)(?=^##\s+|\Z)"
    )
    head_history = history_pattern.search(page_head)
    if head_history:
        return head_history.group(1).strip()
    history_matches = list(history_pattern.finditer(text))
    return history_matches[-1].group(1).strip() if history_matches else ""


def build_update_history(
    version: str,
    notes: str | None,
    previous_history: str = "",
) -> str:
    notes = normalize_update_notes(notes)
    if not notes:
        return previous_history or (
            f"- `{version}`（{datetime.now().astimezone().date().isoformat()}）："
            "版本记录初始化"
        )
    entry = (
        f"- `{version}`（{datetime.now().astimezone().date().isoformat()}）：{notes}"
    )
    return f"{entry}\n{previous_history}".rstrip()


def build_page_markdown(
    info: SkillInfo,
    source_text: str,
    update_history: str,
) -> str:
    package = package_filename(info.name)
    return (
        "## 同步信息\n\n"
        f"- name: `{info.name}`\n"
        f"- version: `{info.version}`\n"
        f"- package: `{package}`\n\n"
        "## Skill 说明\n\n"
        f"{source_text.rstrip()}\n\n"
        "## 版本更新\n\n"
        f"{update_history.rstrip()}\n"
    )


def parse_page_markdown(text: str) -> tuple[str, str, str]:
    head = text.split("## Skill 说明", 1)[0]
    name_match = re.search(r"(?m)^-\s*name:\s*`([^`]+)`\s*$", head)
    version_match = re.search(r"(?m)^-\s*version:\s*`([^`]+)`\s*$", head)
    package_match = re.search(r"(?m)^-\s*package:\s*`([^`]+)`\s*$", head)
    if not (name_match and version_match and package_match):
        raise SyncError("知识库页面缺少同步信息字段")
    return (
        name_match.group(1),
        validate_version(version_match.group(1)),
        package_match.group(1),
    )


def safe_zip_skill(skill_dir: Path, output: Path) -> None:
    skill_dir = skill_dir.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(skill_dir.rglob("*")):
            relative = path.relative_to(skill_dir.parent)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            if path.is_symlink():
                raise SyncError(f"为保证跨 Agent 可移植性，不打包符号链接: {path}")
            if path.is_dir():
                continue
            archive.write(path, relative.as_posix())


def validate_zip_member(info: zipfile.ZipInfo) -> None:
    path = Path(info.filename)
    if path.is_absolute() or ".." in path.parts:
        raise SyncError(f"ZIP 包含不安全路径: {info.filename}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise SyncError(f"ZIP 包含符号链接: {info.filename}")


def extract_skill_zip(zip_path: Path, destination: Path, expected_name: str) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if not infos:
            raise SyncError("ZIP 为空")
        for info in infos:
            validate_zip_member(info)
        roots = {Path(info.filename).parts[0] for info in infos if Path(info.filename).parts}
        if roots != {expected_name}:
            raise SyncError(f"ZIP 顶层目录必须且只能是 {expected_name!r}，实际为 {sorted(roots)!r}")
        archive.extractall(destination)
    extracted = destination / expected_name
    read_skill(extracted)
    return extracted
