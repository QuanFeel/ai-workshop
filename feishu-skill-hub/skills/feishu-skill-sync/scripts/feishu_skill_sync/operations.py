"""Version-gated publish and pull operations."""

import shutil
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .core import (
    PageInfo,
    SkillInfo,
    SyncError,
    build_page_markdown,
    build_update_history,
    compare_versions,
    extract_skill_zip,
    extract_update_history,
    normalize_update_notes,
    package_filename,
    parse_page_markdown,
    read_skill,
    safe_zip_skill,
)
from .lark import (
    create_doc,
    ensure_publish_permissions,
    fetch_doc,
    find_key,
    find_skill_page,
    find_skill_pages,
    package_node,
    root_info,
    run_cli,
    update_wiki_node_title,
    wiki_get_node,
)


def verify_published_skill(
    page: PageInfo,
    expected_version: str,
    expected_package: str,
    expected_title: str,
    cwd: Path,
) -> None:
    """Verify title, document metadata, and ZIP before reporting success."""
    last_error = ""
    for attempt in range(3):
        try:
            node = wiki_get_node(page.node_token, cwd)
            actual_title = str(node.get("title") or "")
            if actual_title != expected_title:
                raise SyncError(
                    f"发布后页面标题不一致：{actual_title} != {expected_title}"
                )
            name, version, package = parse_page_markdown(
                fetch_doc(page.obj_token, cwd)
            )
            if name != page.name:
                raise SyncError(
                    f"发布后文档 name 不一致：{name} != {page.name}"
                )
            if version != expected_version:
                raise SyncError(
                    f"发布后文档版本不一致：{version} != {expected_version}"
                )
            if package != expected_package:
                raise SyncError(
                    f"发布后文档包名不一致：{package} != {expected_package}"
                )
            file_node = package_node(page, cwd)
            file_title = str(file_node.get("title") or "") if file_node else ""
            if not file_node or file_title != expected_package:
                raise SyncError(f"发布后找不到 ZIP 附件：{expected_package}")
            return
        except SyncError as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise SyncError(
        f"发布未通过完整性验证，不得报告成功：{last_error}"
    )


def resolve_local_skills(sources: list[Path]) -> list[Path]:
    """Resolve Skill folders from one or more Skill/container paths."""
    resolved: dict[Path, Path] = {}
    for source in sources:
        path = source.expanduser().resolve()
        if not path.is_dir():
            raise SyncError(f"同步来源不存在或不是目录: {path}")
        if (path / "SKILL.md").is_file():
            resolved[path] = path
            continue
        for child in path.iterdir():
            if child.is_dir() and (child / "SKILL.md").is_file():
                resolved[child.resolve()] = child.resolve()
    if not resolved:
        joined = "、".join(str(source) for source in sources)
        raise SyncError(f"同步来源中找不到 Skill: {joined}")
    skill_dirs = sorted(resolved.values(), key=lambda path: path.name)
    names: dict[str, Path] = {}
    for skill_dir in skill_dirs:
        info = read_skill(skill_dir)
        previous = names.get(info.name)
        if previous and previous != skill_dir:
            raise SyncError(
                f"同步来源包含重名 Skill: {info.name}（{previous}、{skill_dir}）"
            )
        names[info.name] = skill_dir
    return skill_dirs


def plan_publish(
    root: str,
    skill_dir: Path,
    notes: str | None,
    cwd: Path,
) -> dict[str, Any]:
    info = read_skill(skill_dir)
    notes = normalize_update_notes(notes)
    expected_package = package_filename(info.name)
    page = find_skill_page(root, info.name, cwd)
    if not page:
        return {
            "planned_action": "create",
            "requires_confirmation": True,
            "name": info.name,
            "local_version": info.version,
            "remote_version": None,
            "page_title": info.title,
            "previous_page_title": None,
            "title_change": False,
            "package": expected_package,
            "update_notes": notes or "首次发布",
        }
    relation = compare_versions(info.version, page.version)
    if relation > 0 and not notes:
        raise SyncError(
            f"发布新版本 {info.version} 时必须使用 --notes 提供简要更新要点"
        )
    existing_package = package_node(page, cwd)
    title_change = page.title != info.title
    package_ready = bool(
        existing_package
        and page.package == expected_package
        and str(existing_package.get("title") or "") == expected_package
    )
    if relation < 0:
        action = "blocked_remote_newer"
        requires_confirmation = False
    elif relation == 0 and package_ready and not title_change:
        action = "skipped_equal"
        requires_confirmation = False
    elif relation == 0 and package_ready:
        action = "repair_title"
        requires_confirmation = True
    elif relation == 0 and existing_package:
        action = "normalize_package_name"
        requires_confirmation = True
    elif relation == 0:
        action = "repair_missing_package"
        requires_confirmation = True
    else:
        action = "update"
        requires_confirmation = True
    return {
        "planned_action": action,
        "requires_confirmation": requires_confirmation,
        "name": info.name,
        "local_version": info.version,
        "remote_version": page.version,
        "page_title": info.title,
        "previous_page_title": page.title,
        "title_change": title_change,
        "package": expected_package,
        "previous_package": page.package,
        "previous_file_title": (
            str(existing_package.get("title") or "")
            if existing_package
            else None
        ),
        "update_notes": notes,
    }


def plan_publish_skills(
    root: str,
    sources: list[Path],
    notes: str | None,
    cwd: Path,
) -> dict[str, Any]:
    items = [
        {
            "skill_dir": str(skill_dir),
            **plan_publish(root, skill_dir, notes, cwd),
        }
        for skill_dir in resolve_local_skills(sources)
    ]
    plan = {
        "planned_action": "publish",
        "requires_confirmation": any(
            item["requires_confirmation"] for item in items
        ),
        "total": len(items),
        "write_count": sum(
            bool(item["requires_confirmation"]) for item in items
        ),
        "items": items,
    }
    if plan["requires_confirmation"]:
        plan["permissions"] = ensure_publish_permissions(cwd)
    return plan


def publish(
    root: str,
    skill_dir: Path,
    title: str | None,
    notes: str | None,
    cwd: Path,
) -> dict[str, Any]:
    info = read_skill(skill_dir)
    notes = normalize_update_notes(notes)
    expected_package = package_filename(info.name)
    expected_title = title or info.title
    page = find_skill_page(root, info.name, cwd)
    repair_missing_package = False
    normalize_package_name = False
    relation: int | None = None
    if page:
        title_change = page.title != expected_title
        relation = compare_versions(info.version, page.version)
        if relation > 0 and not notes:
            raise SyncError(
                f"发布新版本 {info.version} 时必须使用 --notes 提供简要更新要点"
            )
        if relation == 0:
            existing_package = package_node(page, cwd)
            package_ready = (
                existing_package
                and page.package == expected_package
                and str(existing_package.get("title") or "") == expected_package
            )
            if package_ready and not title_change:
                return {
                    "action": "skipped_equal",
                    "name": info.name,
                    "local_version": info.version,
                    "remote_version": page.version,
                    "package": expected_package,
                    "page": asdict(page),
                }
            if package_ready:
                update_wiki_node_title(page, expected_title, cwd)
                updated_page = replace(page, title=expected_title)
                verify_published_skill(
                    updated_page,
                    info.version,
                    expected_package,
                    expected_title,
                    cwd,
                )
                return {
                    "action": "repaired_title",
                    "name": info.name,
                    "local_version": info.version,
                    "remote_version": page.version,
                    "package": expected_package,
                    "previous_page_title": page.title,
                    "page": asdict(updated_page),
                }
            if existing_package:
                normalize_package_name = True
            else:
                repair_missing_package = True
        if relation < 0:
            return {
                "action": "blocked_remote_newer",
                "name": info.name,
                "local_version": info.version,
                "remote_version": page.version,
                "package": expected_package,
                "page": asdict(page),
            }

    zip_path = cwd / expected_package
    safe_zip_skill(skill_dir, zip_path)
    source_text = (skill_dir.resolve() / "SKILL.md").read_text(encoding="utf-8")
    previous_content = fetch_doc(page.obj_token, cwd) if page else ""
    previous_history = extract_update_history(
        previous_content,
        page.version if page else info.version,
    )
    history_notes = (
        notes
        if page and relation is not None and relation > 0
        else "首次发布"
        if not page
        else None
    )
    update_history = build_update_history(
        info.version,
        history_notes,
        previous_history,
    )
    page_markdown = build_page_markdown(info, source_text, update_history)

    if not page:
        _, node_token, doc_id = create_doc(
            cwd,
            title or info.title,
            page_markdown,
            parent_token=str(root_info(root, cwd)["node_token"]),
        )
        node = wiki_get_node(node_token, cwd)
        page = PageInfo(
            name=info.name,
            version=info.version,
            package=expected_package,
            title=title or info.title,
            node_token=node_token,
            obj_token=doc_id or str(node["obj_token"]),
            space_id=str(node["space_id"]),
        )
        upload = run_cli(
            [
                "drive",
                "+upload",
                "--as",
                "user",
                "--wiki-token",
                node_token,
                "--file",
                zip_path.name,
                "--name",
                zip_path.name,
                "--format",
                "json",
            ],
            cwd,
        )
        verify_published_skill(
            page,
            info.version,
            expected_package,
            expected_title,
            cwd,
        )
        return {
            "action": "created",
            "skill": asdict(info),
            "package": expected_package,
            "page": asdict(page),
            "file_token": find_key(upload, {"file_token", "token"}) or "",
        }

    file_node = package_node(page, cwd)
    if file_node:
        upload = run_cli(
            [
                "drive",
                "+upload",
                "--as",
                "user",
                "--file-token",
                str(file_node["obj_token"]),
                "--file",
                zip_path.name,
                "--name",
                zip_path.name,
                "--format",
                "json",
            ],
            cwd,
        )
    else:
        upload = run_cli(
            [
                "drive",
                "+upload",
                "--as",
                "user",
                "--wiki-token",
                page.node_token,
                "--file",
                zip_path.name,
                "--name",
                zip_path.name,
                "--format",
                "json",
            ],
            cwd,
        )
    page_file = cwd / "_page.md"
    page_file.write_text(page_markdown, encoding="utf-8")
    run_cli(
        [
            "docs",
            "+update",
            "--as",
            "user",
            "--doc",
            page.obj_token,
            "--command",
            "overwrite",
            "--doc-format",
            "markdown",
            "--content",
            f"@{page_file.name}",
            "--format",
            "json",
        ],
        cwd,
    )
    previous_page_title = page.title
    if page.title != expected_title:
        update_wiki_node_title(page, expected_title, cwd)
        page = replace(page, title=expected_title)
    verify_published_skill(
        page,
        info.version,
        expected_package,
        expected_title,
        cwd,
    )
    return {
        "action": (
            "repaired_missing_package"
            if repair_missing_package
            else "normalized_package_name"
            if normalize_package_name
            else "updated"
        ),
        "skill": asdict(info),
        "package": expected_package,
        "previous_package": page.package,
        "previous_file_title": (
            str(file_node.get("title") or "") if file_node else None
        ),
        "previous_version": page.version,
        "previous_page_title": previous_page_title,
        "page_title": expected_title,
        "page_node_token": page.node_token,
        "file_token": find_key(upload, {"file_token", "token"}) or str(
            file_node.get("obj_token", "") if file_node else ""
        ),
    }


def publish_skills(
    root: str,
    sources: list[Path],
    notes: str | None,
    cwd: Path,
) -> dict[str, Any]:
    permissions = ensure_publish_permissions(cwd)
    results = [
        publish(root, skill_dir, None, notes, cwd)
        for skill_dir in resolve_local_skills(sources)
    ]
    return {
        "action": "published",
        "verified": not any(
            result.get("action") == "blocked_remote_newer"
            for result in results
        ),
        "total": len(results),
        "permissions": permissions,
        "results": results,
    }


def install_extracted(extracted: Path, skills_dir: Path, name: str) -> None:
    skills_dir.mkdir(parents=True, exist_ok=True)
    target = skills_dir / name
    backup_root = Path(tempfile.mkdtemp(prefix=f".{name}.sync-", dir=skills_dir))
    backup = backup_root / name
    if target.exists():
        target.rename(backup)
    try:
        shutil.move(str(extracted), str(target))
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        if backup.exists():
            backup.rename(target)
        raise
    finally:
        if backup_root.exists():
            shutil.rmtree(backup_root)


def plan_pull_page(page: PageInfo, skills_dir: Path) -> dict[str, Any]:
    target = skills_dir.expanduser().resolve() / page.name
    if not target.exists():
        return {
            "planned_action": "install",
            "requires_confirmation": True,
            "name": page.name,
            "local_version": None,
            "remote_version": page.version,
            "target": str(target),
        }
    local_info = read_skill(target)
    relation = compare_versions(page.version, local_info.version)
    return {
        "planned_action": "update" if relation > 0 else "skipped_not_newer",
        "requires_confirmation": relation > 0,
        "name": page.name,
        "local_version": local_info.version,
        "remote_version": page.version,
        "target": str(target),
    }


def select_pull_pages(
    root: str,
    names: list[str] | None,
    cwd: Path,
) -> list[PageInfo]:
    pages = sorted(find_skill_pages(root, cwd), key=lambda page: page.name)
    if not pages:
        raise SyncError("中央库中找不到可拉取的 Skill")
    if not names:
        return pages
    requested = list(dict.fromkeys(names))
    by_name = {page.name: page for page in pages}
    missing = [name for name in requested if name not in by_name]
    if missing:
        raise SyncError(f"中央库不存在 Skill: {'、'.join(missing)}")
    return [by_name[name] for name in requested]


def plan_pull_skills(
    root: str,
    names: list[str] | None,
    skills_dir: Path,
    cwd: Path,
) -> dict[str, Any]:
    pages = select_pull_pages(root, names, cwd)
    items = [plan_pull_page(page, skills_dir) for page in pages]
    return {
        "planned_action": "pull",
        "requires_confirmation": any(
            item["requires_confirmation"] for item in items
        ),
        "total": len(items),
        "write_count": sum(
            bool(item["requires_confirmation"]) for item in items
        ),
        "items": items,
    }


def pull_page(page: PageInfo, skills_dir: Path, cwd: Path) -> dict[str, Any]:
    name = page.name
    expected_package = package_filename(name)
    target = skills_dir.expanduser().resolve() / name
    local_info: SkillInfo | None = None
    if target.exists():
        local_info = read_skill(target)
        relation = compare_versions(page.version, local_info.version)
        if relation <= 0:
            return {
                "action": "skipped_not_newer",
                "name": name,
                "local_version": local_info.version,
                "remote_version": page.version,
            }
    if page.package != expected_package:
        raise SyncError(
            f"页面包名不一致：{page.package} != {expected_package}"
        )
    file_node = package_node(page, cwd)
    if not file_node:
        raise SyncError(f"知识库页面下找不到包: {page.package}")
    file_title = str(file_node.get("title") or "")
    if file_title != expected_package:
        raise SyncError(
            f"附件包名不一致：{file_title} != {expected_package}"
        )
    zip_path = cwd / expected_package
    run_cli(
        [
            "drive",
            "+download",
            "--as",
            "user",
            "--file-token",
            str(file_node["obj_token"]),
            "--output",
            zip_path.name,
            "--overwrite",
            "--format",
            "json",
        ],
        cwd,
    )
    extract_dir = cwd / f"_extract_{name}"
    extract_dir.mkdir()
    extracted = extract_skill_zip(zip_path, extract_dir, name)
    downloaded = read_skill(extracted)
    if downloaded.version != page.version:
        raise SyncError(
            f"页面版本 {page.version} 与 ZIP 内版本 {downloaded.version} 不一致"
        )
    install_extracted(extracted, skills_dir.expanduser().resolve(), name)
    installed = read_skill(target)
    if installed.version != page.version:
        raise SyncError(
            f"安装后版本 {installed.version} 与页面版本 "
            f"{page.version} 不一致"
        )
    return {
        "action": "installed" if local_info is None else "updated",
        "name": name,
        "previous_version": local_info.version if local_info else None,
        "version": downloaded.version,
        "target": str(skills_dir.expanduser().resolve() / name),
    }


def pull_skills(
    root: str,
    names: list[str] | None,
    skills_dir: Path,
    cwd: Path,
) -> dict[str, Any]:
    pages = select_pull_pages(root, names, cwd)
    results = [pull_page(page, skills_dir, cwd) for page in pages]
    return {
        "action": "pulled",
        "verified": True,
        "total": len(results),
        "results": results,
    }
