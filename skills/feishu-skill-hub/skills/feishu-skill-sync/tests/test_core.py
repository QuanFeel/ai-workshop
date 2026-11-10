import os
import stat
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from feishu_skill_sync import core  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_semver_comparison(self) -> None:
        self.assertGreater(core.compare_versions("1.2.0", "1.1.9"), 0)
        self.assertEqual(core.compare_versions("1.2.0", "1.2.0"), 0)
        self.assertLess(core.compare_versions("1.2.0-beta.1", "1.2.0"), 0)

    def test_invalid_version_is_rejected(self) -> None:
        with self.assertRaises(core.SyncError):
            core.validate_version("1.2")


class PackageTests(unittest.TestCase):
    def make_skill(self, root: Path) -> Path:
        skill = root / "demo-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "tests").mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: demo-skill\n"
            "description: demo\n"
            'metadata: {"version":"1.2.3"}\n'
            "---\n\n"
            "# 演示 Skill\n",
            encoding="utf-8",
        )
        (skill / "scripts" / "run.py").write_text(
            'print("ok")\n',
            encoding="utf-8",
        )
        (skill / "tests" / "test_demo.py").write_text(
            "raise AssertionError('must not ship')\n",
            encoding="utf-8",
        )
        return skill

    def test_reads_multiline_yaml_metadata_version(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skill = Path(raw) / "demo-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: demo-skill\n"
                "description: demo\n"
                "metadata:\n"
                "  short-description: demo\n"
                '  version: "1.2.4"\n'
                "---\n\n"
                "# 演示 Skill\n",
                encoding="utf-8",
            )
            info = core.read_skill(skill)
            self.assertEqual(info.version, "1.2.4")
            self.assertEqual(info.version_source, "metadata.version")

    def test_rejects_versions_outside_metadata(self) -> None:
        legacy_documents = {
            "top-level version": (
                "---\n"
                "name: demo-skill\n"
                "description: demo\n"
                "version: 1.2.3\n"
                "---\n\n"
                "# 演示 Skill\n"
            ),
            "body version": (
                "---\n"
                "name: demo-skill\n"
                "description: demo\n"
                "---\n\n"
                "# 演示 Skill\n\n"
                "- 当前版本：`1.2.3`\n"
            ),
        }
        for label, document in legacy_documents.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as raw:
                skill = Path(raw) / "demo-skill"
                skill.mkdir()
                (skill / "SKILL.md").write_text(document, encoding="utf-8")
                with self.assertRaisesRegex(core.SyncError, "metadata.version"):
                    core.read_skill(skill)

    def test_package_excludes_tests_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill = self.make_skill(root)
            archive = root / "demo-skill.zip"
            core.safe_zip_skill(skill, archive)
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
            self.assertFalse(any("/tests/" in name for name in names))
            extracted = core.extract_skill_zip(
                archive,
                root / "extract",
                "demo-skill",
            )
            self.assertEqual(core.read_skill(extracted).version, "1.2.3")

    def test_unsafe_zip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape.txt", "bad")
            with self.assertRaises(core.SyncError):
                core.extract_skill_zip(
                    archive,
                    root / "extract",
                    "demo-skill",
                )


class CacheTests(unittest.TestCase):
    def test_cache_round_trip_and_permissions(self) -> None:
        previous = os.environ.get(core.CACHE_ENV)
        try:
            with tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / "library.json"
                os.environ[core.CACHE_ENV] = str(path)
                library = core.LibraryInfo(
                    root="node",
                    title="skills",
                    node_token="node",
                    space_id="space",
                    account_label="个人飞书 用户",
                    account_name="用户",
                    account_open_id="open-id",
                    account_tenant_key="tenant-key",
                    brand="feishu",
                    lark_profile="personal",
                    space_title="知识库",
                    directory_path="skills",
                )
                core.save_library_cache(library)
                self.assertEqual(core.load_library_cache(), library)
                self.assertEqual(
                    stat.S_IMODE(path.stat().st_mode),
                    0o600,
                )
                self.assertEqual(
                    core.load_library_cache().lark_profile,
                    "personal",
                )
        finally:
            if previous is None:
                os.environ.pop(core.CACHE_ENV, None)
            else:
                os.environ[core.CACHE_ENV] = previous

    def test_confirmation_code_is_bound_to_cli_profile(self) -> None:
        library = core.LibraryInfo(
            root="node",
            title="skills",
            node_token="node",
            space_id="space",
            account_label="飞书 用户",
            account_name="用户",
            account_open_id="open-id",
            account_tenant_key="tenant-key",
            brand="feishu",
            lark_profile="personal",
            space_title="知识库",
            directory_path="skills",
        )
        plan = {
            "planned_action": "cache_library_location",
            "requires_confirmation": True,
        }
        self.assertNotEqual(
            core.confirmation_code(library, "library_set", plan),
            core.confirmation_code(
                replace(library, lark_profile="enterprise"),
                "library_set",
                plan,
            ),
        )

    def test_public_library_displays_verified_profile_and_user(self) -> None:
        library = core.LibraryInfo(
            root="node",
            title="skills",
            node_token="node",
            space_id="space",
            account_label="企业飞书 用户",
            account_name="verified-user",
            account_open_id="open-id-value",
            account_tenant_key="tenant-key-value",
            brand="feishu",
            lark_profile="personal-profile",
            space_title="知识库",
            directory_path="skills",
        )
        public = core.public_library(library)
        self.assertEqual(
            public,
            {
                "display": (
                    "Profile：personal-profile\n"
                    "OAuth 用户：verified-user（open…alue）\n"
                    "目前位置：知识库 / skills"
                )
            },
        )
        self.assertNotIn("企业飞书 用户", public["display"])
        self.assertNotIn("tenant", public["display"])
        self.assertIn("personal-profile", public["display"])

    def test_cached_library_hides_identity(self) -> None:
        library = core.LibraryInfo(
            root="node",
            title="skills",
            node_token="node",
            space_id="space",
            account_label="用户",
            account_name="verified-user",
            account_open_id="open-id-value",
            account_tenant_key="tenant-key-value",
            brand="feishu",
            lark_profile="profile-secret",
            space_title="知识库",
            directory_path="skills",
        )
        public = core.public_library(library, include_identity=False)
        self.assertEqual(
            public,
            {
                "display": "目前位置：知识库 / skills",
                "cache_reused": True,
            },
        )
        self.assertNotIn("verified-user", str(public))
        self.assertNotIn("profile-secret", str(public))


if __name__ == "__main__":
    unittest.main()
