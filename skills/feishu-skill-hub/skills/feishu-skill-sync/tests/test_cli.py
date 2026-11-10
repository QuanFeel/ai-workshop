import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SKILL_DIR = Path(__file__).resolve().parents[1]
ENTRY_POINT = SKILL_DIR / "scripts" / "skill_sync.py"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from feishu_skill_sync import cli as cli_module  # noqa: E402


class CliSmokeTests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ENTRY_POINT), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=env,
        )

    def missing_cli_environment(self, home: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment["HOME"] = home
        environment["PATH"] = ""
        environment.pop("LARK_CLI_CMD", None)
        environment.pop("AGENT_SKILLS_DIR", None)
        return environment

    def test_compare(self) -> None:
        result = self.run_cli("compare", "1.2.0", "1.1.9")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["relation"], "greater")

    def test_invalid_compare(self) -> None:
        result = self.run_cli("compare", "1.2", "1.1.9")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_inspect_entry_skill(self) -> None:
        result = self.run_cli("inspect", str(SKILL_DIR))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["skill"]["name"], "feishu-skill-sync")

    def test_discover_requires_explicit_profile(self) -> None:
        result = self.run_cli("library", "discover")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--lark-profile", result.stderr)

    def test_authorize_supports_new_profile_and_oauth_completion(self) -> None:
        result = self.run_cli("library", "authorize", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--lark-profile", result.stdout)
        self.assertIn("--device-code", result.stdout)

    def test_library_profiles_explains_needs_refresh_without_reauth(self) -> None:
        stdout = io.StringIO()
        profiles = [
            {
                "name": "enterprise",
                "active": True,
                "user": "test-user",
                "tokenStatus": "needs_refresh",
            }
        ]
        with (
            mock.patch.object(
                sys,
                "argv",
                [str(ENTRY_POINT), "library", "profiles"],
            ),
            mock.patch.object(
                cli_module,
                "lark_cli_available",
                return_value=True,
            ),
            mock.patch.object(
                cli_module,
                "with_workspace",
                return_value=profiles,
            ),
            redirect_stdout(stdout),
        ):
            result = cli_module.main()

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        policy = payload["token_status_policy"]["needs_refresh"]
        self.assertFalse(policy["reauthorization_required"])
        self.assertEqual(policy["next_action"], "try_read_only_discover")
        self.assertFalse(payload["profiles"][0]["reauthorization_required"])
        self.assertIn("不得据此运行 library authorize", payload["next_action"])

    def test_sync_commands_accept_one_or_many_without_separate_modes(self) -> None:
        publish = self.run_cli("publish", "--help")
        pull = self.run_cli("pull", "--help")
        self.assertEqual(publish.returncode, 0, publish.stderr)
        self.assertEqual(pull.returncode, 0, pull.stderr)
        self.assertIn("--source SOURCE [SOURCE ...]", publish.stdout)
        self.assertNotIn("--skill-dir", publish.stdout)
        self.assertIn("--skills-dir", pull.stdout)
        self.assertIn("当前 Agent 自行解析", pull.stdout)
        self.assertIn("--name NAME [NAME ...]", pull.stdout)
        self.assertNotEqual(
            self.run_cli("publish-many", "--help").returncode,
            0,
        )
        self.assertNotEqual(
            self.run_cli("pull-many", "--help").returncode,
            0,
        )

    def test_account_label_cannot_masquerade_as_profile_switch(self) -> None:
        result = self.run_cli(
            "library",
            "set",
            "--root",
            "dummy-node",
            "--lark-profile",
            "personal-profile",
            "--account-label",
            "示例账号备注",
        )
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("--account-label 已停用", payload["error"])
        self.assertIn("--lark-profile", payload["error"])

    def test_library_show_reads_cache_without_lark_cli(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cache = Path(raw) / "library.json"
            cache.write_text(
                json.dumps(
                    {
                        "schema_version": 5,
                        "root": "node",
                        "title": "skills",
                        "node_token": "node",
                        "space_id": "space",
                        "account_label": "示例用户",
                        "account_name": "示例用户",
                        "account_open_id": "open-id",
                        "account_tenant_key": "tenant-key",
                        "brand": "feishu",
                        "lark_profile": "cached-profile",
                        "space_title": "示例知识库",
                        "directory_path": "skills",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["SKILL_SYNC_CACHE_FILE"] = str(cache)
            environment["LARK_CLI_CMD"] = "/definitely/missing/lark-cli"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ENTRY_POINT),
                    "library",
                    "show",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["source"], "local_cache")
        self.assertFalse(payload["remote_verified"])

    def test_missing_cli_returns_install_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            result = self.run_cli(
                "library",
                "profiles",
                env=self.missing_cli_environment(home),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "setup_required")
        self.assertFalse(payload["ready"])
        self.assertNotIn("error", payload)
        self.assertNotIn("verified", payload)
        self.assertTrue(payload["oauth_deferred_to_caller"])
        self.assertEqual(payload["authorization_owner"], "feishu-skill-sync")
        self.assertEqual(
            set(payload),
            {
                "ok",
                "action",
                "ready",
                "cli_installed",
                "oauth_deferred_to_caller",
                "authorization_owner",
                "install_command",
                "verify_command",
                "message",
                "next_action",
            },
        )
        self.assertEqual(
            payload["install_command"],
            "npx @larksuite/cli@latest install",
        )
        self.assertIn("未检测到官方 lark-cli", payload["message"])
        self.assertIn("lark-cli config init --new", payload["message"])

    def test_all_remote_commands_return_setup_guidance_without_cli(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            environment = self.missing_cli_environment(home)
            commands = (
                ("library", "profiles"),
                ("library", "authorize", "--lark-profile", "test-profile"),
                ("library", "discover", "--lark-profile", "test-profile"),
                ("list",),
                ("publish", "--source", str(SKILL_DIR), "--notes", "test"),
                ("pull", "--skills-dir", str(Path(home) / "skills")),
            )
            for command in commands:
                with self.subTest(command=command):
                    result = self.run_cli(*command, env=environment)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertTrue(payload["ok"])
                    self.assertEqual(payload["action"], "setup_required")
                    self.assertFalse(payload["ready"])
                    self.assertFalse(payload["cli_installed"])


if __name__ == "__main__":
    unittest.main()
