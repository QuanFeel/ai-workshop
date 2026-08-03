import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from feishu_skill_sync import operations  # noqa: E402
from feishu_skill_sync.core import PageInfo  # noqa: E402


def make_skill(
    root: Path,
    name: str,
    version: str = "1.0.0",
    title: str | None = None,
) -> Path:
    skill = root / name
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: demo\n"
        f'metadata: {{"version":"{version}"}}\n'
        "---\n\n"
        f"# {title or name}\n",
        encoding="utf-8",
    )
    return skill


class SyncOperationTests(unittest.TestCase):
    def test_resolve_local_skills_accepts_skill_and_container_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            second = make_skill(root, "second-skill")
            first = make_skill(root, "first-skill")
            (root / "not-a-skill").mkdir()
            self.assertEqual(
                operations.resolve_local_skills([root]),
                [first.resolve(), second.resolve()],
            )
            self.assertEqual(
                operations.resolve_local_skills([second]),
                [second.resolve()],
            )

    def test_publish_builds_one_plan_for_any_number_of_skills(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_skill(root, "first-skill")
            make_skill(root, "second-skill")
            plans = [
                {
                    "planned_action": "create",
                    "requires_confirmation": True,
                    "name": "first-skill",
                },
                {
                    "planned_action": "skipped_equal",
                    "requires_confirmation": False,
                    "name": "second-skill",
                },
            ]
            with mock.patch.object(
                operations,
                "plan_publish",
                side_effect=plans,
            ), mock.patch.object(
                operations,
                "ensure_publish_permissions",
                return_value={"scopes": ["drive:file:upload"]},
            ) as check_permissions:
                plan = operations.plan_publish_skills(
                    "root",
                    [root],
                    "同步",
                    root,
                )
            self.assertEqual(plan["planned_action"], "publish")
            self.assertEqual(plan["total"], 2)
            self.assertEqual(plan["write_count"], 1)
            self.assertTrue(plan["requires_confirmation"])
            self.assertEqual(len(plan["items"]), 2)
            self.assertEqual(
                plan["permissions"]["scopes"],
                ["drive:file:upload"],
            )
            check_permissions.assert_called_once_with(root)

    def test_publish_returns_one_top_level_verified_flag(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill = make_skill(root, "demo-skill")
            published = {
                "action": "created",
            }
            with (
                mock.patch.object(
                    operations,
                    "ensure_publish_permissions",
                    return_value={"scopes": ["drive:file:upload"]},
                ),
                mock.patch.object(
                    operations,
                    "resolve_local_skills",
                    return_value=[skill],
                ),
                mock.patch.object(
                    operations,
                    "publish",
                    return_value=published,
                ),
            ):
                result = operations.publish_skills(
                    "root",
                    [root],
                    "同步",
                    root,
                )
        self.assertTrue(result["verified"])

    def test_publish_is_not_verified_when_remote_is_newer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill = make_skill(root, "demo-skill")
            blocked = {"action": "blocked_remote_newer"}
            with (
                mock.patch.object(
                    operations,
                    "ensure_publish_permissions",
                    return_value={"scopes": ["drive:file:upload"]},
                ),
                mock.patch.object(
                    operations,
                    "resolve_local_skills",
                    return_value=[skill],
                ),
                mock.patch.object(
                    operations,
                    "publish",
                    return_value=blocked,
                ),
            ):
                result = operations.publish_skills(
                    "root",
                    [root],
                    "同步",
                    root,
                )
        self.assertFalse(result["verified"])

    def test_publish_verification_rejects_missing_zip_attachment(self) -> None:
        page = PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="Demo",
            node_token="node",
            obj_token="doc",
            space_id="space",
        )
        content = (
            "## 同步信息\n\n"
            "- name: `demo-skill`\n"
            "- version: `1.0.0`\n"
            "- package: `demo-skill.zip`\n\n"
            "## Skill 说明\n"
        )
        with (
            mock.patch.object(operations, "fetch_doc", return_value=content),
            mock.patch.object(
                operations,
                "wiki_get_node",
                return_value={"title": "Demo"},
            ),
            mock.patch.object(operations, "package_node", return_value=None),
            mock.patch.object(operations.time, "sleep"),
        ):
            with self.assertRaisesRegex(
                operations.SyncError,
                "不得报告成功",
            ):
                operations.verify_published_skill(
                    page,
                    "1.0.0",
                    "demo-skill.zip",
                    "Demo",
                    Path("/tmp"),
                )

    def test_publish_verification_requires_document_and_zip(self) -> None:
        page = PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="Demo",
            node_token="node",
            obj_token="doc",
            space_id="space",
        )
        content = (
            "## 同步信息\n\n"
            "- name: `demo-skill`\n"
            "- version: `1.0.0`\n"
            "- package: `demo-skill.zip`\n\n"
            "## Skill 说明\n"
        )
        with (
            mock.patch.object(operations, "fetch_doc", return_value=content),
            mock.patch.object(
                operations,
                "wiki_get_node",
                return_value={"title": "Demo"},
            ),
            mock.patch.object(
                operations,
                "package_node",
                return_value={
                    "title": "demo-skill.zip",
                    "obj_token": "file-token",
                },
            ),
        ):
            verification = operations.verify_published_skill(
                page,
                "1.0.0",
                "demo-skill.zip",
                "Demo",
                Path("/tmp"),
            )
        self.assertIsNone(verification)

    def test_publish_verification_rejects_page_title_mismatch(self) -> None:
        page = PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="新标题",
            node_token="node",
            obj_token="doc",
            space_id="space",
        )
        with (
            mock.patch.object(
                operations,
                "wiki_get_node",
                return_value={"title": "旧标题"},
            ),
            mock.patch.object(operations.time, "sleep"),
        ):
            with self.assertRaisesRegex(
                operations.SyncError,
                "页面标题不一致",
            ):
                operations.verify_published_skill(
                    page,
                    "1.0.0",
                    "demo-skill.zip",
                    "新标题",
                    Path("/tmp"),
                )

    def test_equal_version_with_title_drift_plans_title_repair(self) -> None:
        page = PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="旧标题",
            node_token="node",
            obj_token="doc",
            space_id="space",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill = make_skill(root, "demo-skill", title="新标题")
            with (
                mock.patch.object(operations, "find_skill_page", return_value=page),
                mock.patch.object(
                    operations,
                    "package_node",
                    return_value={
                        "title": "demo-skill.zip",
                        "obj_token": "file-token",
                    },
                ),
            ):
                plan = operations.plan_publish("root", skill, None, root)
        self.assertEqual(plan["planned_action"], "repair_title")
        self.assertTrue(plan["requires_confirmation"])
        self.assertEqual(plan["previous_page_title"], "旧标题")
        self.assertEqual(plan["page_title"], "新标题")
        self.assertTrue(plan["title_change"])

    def test_equal_version_title_repair_updates_and_verifies_node(self) -> None:
        page = PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="旧标题",
            node_token="node",
            obj_token="doc",
            space_id="space",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill = make_skill(root, "demo-skill", title="新标题")
            with (
                mock.patch.object(operations, "find_skill_page", return_value=page),
                mock.patch.object(
                    operations,
                    "package_node",
                    return_value={
                        "title": "demo-skill.zip",
                        "obj_token": "file-token",
                    },
                ),
                mock.patch.object(operations, "update_wiki_node_title") as update,
                mock.patch.object(operations, "verify_published_skill") as verify,
            ):
                result = operations.publish(
                    "root",
                    skill,
                    None,
                    None,
                    root,
                )
        update.assert_called_once_with(page, "新标题", root)
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[0].title, "新标题")
        self.assertEqual(result["action"], "repaired_title")
        self.assertEqual(result["previous_page_title"], "旧标题")

    def test_pull_defaults_to_all_and_accepts_selected_names(self) -> None:
        pages = [
            PageInfo(
                name="first-skill",
                version="1.0.0",
                package="first-skill.zip",
                title="First",
                node_token="node-1",
                obj_token="doc-1",
                space_id="space",
            ),
            PageInfo(
                name="second-skill",
                version="2.0.0",
                package="second-skill.zip",
                title="Second",
                node_token="node-2",
                obj_token="doc-2",
                space_id="space",
            ),
        ]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with mock.patch.object(
                operations,
                "find_skill_pages",
                return_value=pages,
            ):
                all_plan = operations.plan_pull_skills(
                    "root",
                    None,
                    root,
                    root,
                )
                one_plan = operations.plan_pull_skills(
                    "root",
                    ["second-skill"],
                    root,
                    root,
                )
        self.assertEqual(all_plan["planned_action"], "pull")
        self.assertEqual(all_plan["total"], 2)
        self.assertEqual(all_plan["write_count"], 2)
        self.assertTrue(all_plan["requires_confirmation"])
        self.assertEqual(one_plan["total"], 1)
        self.assertEqual(one_plan["items"][0]["name"], "second-skill")

    def test_pull_verifies_coze_generated_package_before_install(self) -> None:
        page = PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="Demo",
            node_token="node",
            obj_token="doc",
            space_id="space",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "source"
            source_root.mkdir()
            source = make_skill(source_root, "demo-skill")
            work = root / "work"
            work.mkdir()
            operations.safe_zip_skill(source, work / "demo-skill.zip")
            skills_dir = root / "skills"
            with (
                mock.patch.object(
                    operations,
                    "package_node",
                    return_value={
                        "title": "demo-skill.zip",
                        "obj_token": "file-token",
                    },
                ),
                mock.patch.object(operations, "run_cli", return_value={}),
            ):
                result = operations.pull_page(
                    page,
                    skills_dir,
                    work,
                )
        self.assertEqual(result["action"], "installed")
        self.assertEqual(result["version"], "1.0.0")

    def test_pull_rejects_attachment_name_mismatch(self) -> None:
        page = PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="Demo",
            node_token="node",
            obj_token="doc",
            space_id="space",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with mock.patch.object(
                operations,
                "package_node",
                return_value={
                    "title": "demo-skill-1.0.0.zip",
                    "obj_token": "file-token",
                },
            ):
                with self.assertRaisesRegex(
                    operations.SyncError,
                    "附件包名不一致",
                ):
                    operations.pull_page(page, root / "skills", root)

    def test_pull_returns_one_top_level_verified_flag(self) -> None:
        page = PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="Demo",
            node_token="node",
            obj_token="doc",
            space_id="space",
        )
        installed = {
            "action": "installed",
        }
        with (
            mock.patch.object(
                operations,
                "select_pull_pages",
                return_value=[page],
            ),
            mock.patch.object(
                operations,
                "pull_page",
                return_value=installed,
            ),
        ):
            result = operations.pull_skills(
                "root",
                ["demo-skill"],
                Path("/tmp/skills"),
                Path("/tmp"),
            )
        self.assertTrue(result["verified"])


if __name__ == "__main__":
    unittest.main()
