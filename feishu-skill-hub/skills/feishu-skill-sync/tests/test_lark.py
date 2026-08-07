import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from feishu_skill_sync import lark  # noqa: E402


class WikiNodeTests(unittest.TestCase):
    def test_update_title_uses_official_wiki_openapi(self) -> None:
        page = lark.PageInfo(
            name="demo-skill",
            version="1.0.0",
            package="demo-skill.zip",
            title="旧标题",
            node_token="wiki-node",
            obj_token="docx-token",
            space_id="space-id",
        )
        with mock.patch.object(lark, "run_cli", return_value={}) as run:
            lark.update_wiki_node_title(page, "新标题", Path("/tmp"))
        run.assert_called_once_with(
            [
                "api",
                "POST",
                "/open-apis/wiki/v2/spaces/space-id/nodes/"
                "wiki-node/update_title",
                "--as",
                "user",
                "--data",
                '{"title": "新标题"}',
                "--format",
                "json",
            ],
            Path("/tmp"),
        )

    def test_raw_wiki_token_is_passed_as_wiki_url(self) -> None:
        payload = {
            "node_token": "ExampleWikiNodeToken0000000001",
            "obj_token": "docx-token",
        }
        with mock.patch.object(lark, "run_cli", return_value=payload) as run:
            self.assertEqual(
                lark.wiki_get_node(
                    "ExampleWikiNodeToken0000000001",
                    Path("/tmp"),
                ),
                payload,
            )
        run.assert_called_once_with(
            [
                "wiki",
                "+node-get",
                "--as",
                "user",
                "--node-token",
                "https://feishu.cn/wiki/ExampleWikiNodeToken0000000001",
                "--format",
                "json",
            ],
            Path("/tmp"),
        )

    def test_obj_token_keeps_explicit_obj_type(self) -> None:
        payload = {
            "node_token": "wiki-token",
            "obj_token": "docx-token",
        }
        with mock.patch.object(lark, "run_cli", return_value=payload) as run:
            lark.wiki_get_node("docx-token", Path("/tmp"), obj_type="docx")
        run.assert_called_once_with(
            [
                "wiki",
                "+node-get",
                "--as",
                "user",
                "--node-token",
                "docx-token",
                "--format",
                "json",
                "--obj-type",
                "docx",
            ],
            Path("/tmp"),
        )

    def test_parent_lookup_uses_wiki_url_for_raw_token(self) -> None:
        parent = {
            "node_token": "parent-token",
            "obj_token": "parent-docx-token",
            "title": "root",
        }
        child = {
            "node_token": "child-token",
            "obj_token": "child-docx-token",
            "title": "skills",
            "parent_node_token": "parent-token",
        }
        with mock.patch.object(lark, "run_cli", return_value=parent) as run:
            self.assertEqual(
                lark.resolve_directory_path(child, Path("/tmp")),
                "root / skills",
            )
        self.assertEqual(
            run.call_args.args[0][5],
            "https://feishu.cn/wiki/parent-token",
        )


class TransportSafetyTests(unittest.TestCase):
    def test_run_cli_failure_redacts_sensitive_json_fields(self) -> None:
        payload = {
            "ok": False,
            "access_token": "secret-access-token",
            "nested": {
                "device_code": "secret-device-code",
                "open_id": "secret-open-id",
            },
        }
        process = mock.Mock(
            returncode=1,
            stdout=json.dumps(payload),
            stderr="refresh_token=secret-refresh-token",
        )
        with (
            mock.patch.object(lark, "resolve_cli", return_value=["lark-cli"]),
            mock.patch.object(lark.subprocess, "run", return_value=process),
        ):
            with self.assertRaises(lark.SyncError) as raised:
                lark.run_cli(["wiki", "+space-list"], Path("/tmp"))

        message = str(raised.exception)
        self.assertIn('"access_token": "***"', message)
        self.assertNotIn("secret-access-token", message)
        self.assertNotIn("secret-device-code", message)
        self.assertNotIn("secret-open-id", message)
        self.assertNotIn("secret-refresh-token", message)

    def test_run_cli_json_failure_omits_unstructured_streams(self) -> None:
        process = mock.Mock(
            returncode=1,
            stdout="",
            stderr="app_secret=secret-app-value",
        )
        with (
            mock.patch.object(lark, "resolve_cli", return_value=["lark-cli"]),
            mock.patch.object(lark.subprocess, "run", return_value=process),
        ):
            with self.assertRaises(lark.SyncError) as raised:
                lark.run_cli_json(["profile", "list"], Path("/tmp"))

        message = str(raised.exception)
        self.assertIn("已省略原始 stdout/stderr", message)
        self.assertNotIn("secret-app-value", message)


class ProfileTests(unittest.TestCase):
    def tearDown(self) -> None:
        lark.set_lark_profile(None)

    def test_needs_refresh_is_advisory_not_reauthorization(self) -> None:
        profiles = lark.public_profiles(
            [
                {
                    "name": "enterprise",
                    "active": True,
                    "user": "test-user",
                    "tokenStatus": "needs_refresh",
                }
            ]
        )

        self.assertEqual(profiles[0]["token_status"], "needs_refresh")
        self.assertTrue(profiles[0]["token_status_is_advisory"])
        self.assertFalse(profiles[0]["reauthorization_required"])
        self.assertEqual(
            profiles[0]["next_action"],
            "try_read_only_discover",
        )

    def test_selected_profile_is_added_to_cli_command(self) -> None:
        lark.set_lark_profile("enterprise")
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                lark.shutil,
                "which",
                return_value="/usr/local/bin/lark-cli",
            ),
        ):
            self.assertEqual(
                lark.resolve_cli(),
                [
                    "/usr/local/bin/lark-cli",
                    "--profile",
                    "enterprise",
                ],
            )

    def test_conflicting_configured_profile_is_rejected(self) -> None:
        lark.set_lark_profile("enterprise")
        with mock.patch.dict(
            os.environ,
            {"LARK_CLI_CMD": "lark-cli --profile personal"},
            clear=True,
        ):
            with self.assertRaises(lark.SyncError):
                lark.resolve_cli()

    def test_explicit_none_removes_configured_profile(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"LARK_CLI_CMD": "lark-cli --profile existing"},
            clear=True,
        ):
            self.assertEqual(
                lark.resolve_cli(profile_override=None),
                ["lark-cli"],
            )

    def test_missing_profile_starts_official_new_app_setup(self) -> None:
        with (
            mock.patch.object(lark, "list_profiles", return_value=[]),
            mock.patch.object(
                lark,
                "exec_new_profile_setup",
                side_effect=RuntimeError("exec-called"),
            ) as setup,
        ):
            with self.assertRaisesRegex(RuntimeError, "exec-called"):
                lark.authorize_profile(
                    "new-enterprise-profile",
                    None,
                    Path("/tmp"),
                )
        setup.assert_called_once_with("new-enterprise-profile")

    def test_new_profile_setup_streams_official_init_without_old_profile(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {"LARK_CLI_CMD": "lark-cli --profile existing"},
                clear=True,
            ),
            mock.patch.object(
                lark.os,
                "execvpe",
                side_effect=RuntimeError("exec-called"),
            ) as execute,
        ):
            with self.assertRaisesRegex(RuntimeError, "exec-called"):
                lark.exec_new_profile_setup("new-enterprise-profile")
        executable, command, _environment = execute.call_args.args
        self.assertEqual(executable, "lark-cli")
        self.assertEqual(
            command,
            [
                "lark-cli",
                "config",
                "init",
                "--new",
                "--name",
                "new-enterprise-profile",
                "--force-init",
            ],
        )
        self.assertNotIn("existing", command)

    def test_authorize_profile_creates_missing_profile_and_starts_oauth(self) -> None:
        oauth_payload = {
            "verification_uri_complete": "https://example.invalid/verify",
            "device_code": "temporary-device-code",
            "user_code": "ABCD-EFGH",
            "expires_in": 600,
        }
        with (
            mock.patch.object(
                lark,
                "list_profiles",
                return_value=[{"name": "new-enterprise-profile"}],
            ),
            mock.patch.object(
                lark,
                "run_cli_json",
                return_value=oauth_payload,
            ) as run,
        ):
            result = lark.authorize_profile(
                "new-enterprise-profile",
                None,
                Path("/tmp"),
            )
        self.assertEqual(result["action"], "oauth_authorization_required")
        self.assertEqual(result["device_code"], "temporary-device-code")
        self.assertEqual(
            result["recommended_permissions"],
            {
                "scopes": list(lark.EXPLICIT_AUTH_SCOPES),
            },
        )
        self.assertEqual(
            result["authorization_mode"],
            "explicit_minimum_scopes",
        )
        self.assertEqual(
            result["scope_source"],
            "feishu_skill_sync_call_chain",
        )
        run.assert_called_once_with(
            [
                "auth",
                "login",
                "--scope",
                " ".join(lark.EXPLICIT_AUTH_SCOPES),
                "--no-wait",
                "--json",
            ],
            Path("/tmp"),
        )

    def test_explicit_minimum_scopes_are_stable_and_unique(self) -> None:
        self.assertEqual(
            lark.EXPLICIT_AUTH_SCOPES,
            (
                "auth:user.id:read",
                "offline_access",
                "docx:document:create",
                "docx:document:readonly",
                "docx:document:write_only",
                "drive:drive.metadata:readonly",
                "drive:file:download",
                "drive:file:upload",
                "wiki:wiki:readonly",
                "wiki:node:retrieve",
                "wiki:node:update",
                "wiki:space:retrieve",
            ),
        )
        self.assertEqual(
            len(lark.EXPLICIT_AUTH_SCOPES),
            len(set(lark.EXPLICIT_AUTH_SCOPES)),
        )

    def test_wiki_discovery_compatibility_scope_is_read_only(self) -> None:
        self.assertIn("wiki:wiki:readonly", lark.EXPLICIT_AUTH_SCOPES)
        self.assertIn("wiki:space:retrieve", lark.EXPLICIT_AUTH_SCOPES)
        self.assertNotIn("wiki:wiki", lark.EXPLICIT_AUTH_SCOPES)

    def test_completed_authorization_reports_minimum_scope_contract(self) -> None:
        account = lark.AccountInfo(
            label="",
            name="test-user",
            open_id="ou_test",
            tenant_key="tenant-test",
            brand="feishu",
            lark_profile="enterprise",
        )
        with (
            mock.patch.object(
                lark,
                "list_profiles",
                return_value=[{"name": "enterprise"}],
            ),
            mock.patch.object(lark, "complete_device_authorization") as complete,
            mock.patch.object(lark, "current_account", return_value=account),
        ):
            result = lark.authorize_profile(
                "enterprise",
                "temporary-device-code",
                Path("/tmp"),
            )

        complete.assert_called_once_with("temporary-device-code", Path("/tmp"))
        self.assertTrue(result["permissions_complete"])
        self.assertEqual(
            result["authorized_scopes"],
            list(lark.EXPLICIT_AUTH_SCOPES),
        )
        self.assertEqual(
            result["scope_source"],
            "feishu_skill_sync_call_chain",
        )
        self.assertNotIn("authorized_domains", result)

    def test_device_authorization_rejects_multiple_missing_scopes(self) -> None:
        payload = {
            "event": "authorization_complete",
            "missing": [
                "drive:file:upload",
                "wiki:space:retrieve",
            ],
        }
        process = mock.Mock(
            returncode=3,
            stdout=json.dumps(payload),
            stderr="authorization completed with optional scopes missing",
        )
        with (
            mock.patch.object(lark, "resolve_cli", return_value=["lark-cli"]),
            mock.patch.object(lark.subprocess, "run", return_value=process),
        ):
            with self.assertRaisesRegex(
                lark.SyncError,
                "drive:file:upload.*wiki:space:retrieve",
            ):
                lark.complete_device_authorization(
                    "temporary-device-code",
                    Path("/tmp"),
                )

    def test_device_authorization_rejects_required_missing_scope(self) -> None:
        payload = {
            "event": "authorization_complete",
            "missing": ["drive:file:upload"],
        }
        process = mock.Mock(
            returncode=3,
            stdout=json.dumps(payload),
            stderr="authorization completed with required scope missing",
        )
        with (
            mock.patch.object(lark, "resolve_cli", return_value=["lark-cli"]),
            mock.patch.object(lark.subprocess, "run", return_value=process),
        ):
            with self.assertRaisesRegex(
                lark.SyncError,
                "drive:file:upload",
            ):
                lark.complete_device_authorization(
                    "temporary-device-code",
                    Path("/tmp"),
                )

    def test_device_authorization_rejects_missing_scope_even_on_zero_exit(self) -> None:
        payload = {
            "event": "authorization_complete",
            "missing": ["offline_access"],
        }
        process = mock.Mock(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with (
            mock.patch.object(lark, "resolve_cli", return_value=["lark-cli"]),
            mock.patch.object(lark.subprocess, "run", return_value=process),
        ):
            with self.assertRaisesRegex(lark.SyncError, "offline_access"):
                lark.complete_device_authorization(
                    "temporary-device-code",
                    Path("/tmp"),
                )

    def test_publish_permission_check_reports_reauthorization_before_write(self) -> None:
        with mock.patch.object(
            lark,
            "run_cli_json",
            side_effect=lark.SyncError("missing scope"),
        ):
            with self.assertRaisesRegex(
                lark.SyncError,
                "docx:document:create",
            ):
                lark.ensure_publish_permissions(Path("/tmp"))

    def test_publish_permission_check_accepts_all_required_scopes(self) -> None:
        with mock.patch.object(
            lark,
            "run_cli_json",
            return_value={
                "ok": True,
                "granted": [
                    *lark.REQUIRED_PUBLISH_SCOPES,
                ],
                "missing": None,
            },
        ) as run:
            permissions = lark.ensure_publish_permissions(Path("/tmp"))
        self.assertEqual(
            permissions["scopes"],
            list(lark.REQUIRED_PUBLISH_SCOPES),
        )
        run.assert_called_once_with(
            [
                "auth",
                "check",
                "--scope",
                " ".join(lark.REQUIRED_PUBLISH_SCOPES),
                "--json",
            ],
            Path("/tmp"),
        )

    def test_discovery_displays_profile_user_and_default_candidate(self) -> None:
        account = lark.AccountInfo(
            label="本地备注",
            name="示例用户",
            open_id="ou_sample_12345678",
            tenant_key="tenant-secret",
            brand="feishu",
            lark_profile="profile-secret",
        )
        public = lark.public_discovery(
            account,
            [{"title": "示例知识库", "match_kind": "related", "candidate_count": 1}],
            [
                {
                    "knowledge_base": "示例知识库",
                    "knowledge_base_match_kind": "related",
                    "directory_path": "共享 / skills",
                    "match_kind": "exact",
                }
            ],
            "skills",
        )
        self.assertEqual(
            public["display"],
            "Profile：profile-secret\n"
            "OAuth 用户：示例用户（ou_s…5678）\n"
            "目前位置：示例知识库 / 共享 / skills",
        )
        self.assertEqual(
            public["default_candidate"],
            public["candidates"][0],
        )
        self.assertNotIn("authorization", public)
        self.assertNotIn("tenant-secret", str(public))
        self.assertIn("profile-secret", str(public))
        self.assertNotIn("本地备注", str(public))

    def test_common_knowledge_and_skill_names_are_ranked(self) -> None:
        self.assertEqual(
            lark.knowledge_base_match_kind("Agent 技能库"),
            "exact",
        )
        self.assertEqual(
            lark.knowledge_base_match_kind("技能库"),
            "exact",
        )
        self.assertEqual(
            lark.knowledge_base_match_kind("Product Wiki"),
            "generic",
        )
        self.assertEqual(
            lark.directory_match_kind("skills", "skills"),
            "exact",
        )
        self.assertEqual(
            lark.directory_match_kind("Agent Skills", "skills"),
            "related",
        )
        self.assertEqual(
            lark.directory_match_kind("搭建技能", "skills"),
            "related",
        )
        self.assertIsNone(
            lark.directory_match_kind("普通页面", "skills"),
        )

    def test_default_binding_uses_first_dynamic_candidate(self) -> None:
        account = lark.AccountInfo(
            label="示例用户",
            name="示例用户",
            open_id="open-id",
            tenant_key="tenant-key",
            brand="feishu",
            lark_profile="profile",
        )
        candidates = [
            {
                "knowledge_base": "Agent 库",
                "directory_path": "搭建技能",
                "_node": {"node_token": "first"},
            },
            {
                "knowledge_base": "其他库",
                "directory_path": "skills",
                "_node": {"node_token": "second"},
            },
        ]
        expected = object()
        with (
            mock.patch.object(
                lark,
                "discover_library_candidates",
                return_value=(account, [], candidates),
            ),
            mock.patch.object(
                lark,
                "library_from_node",
                return_value=expected,
            ) as build,
        ):
            self.assertIs(
                lark.bind_default_library(Path("/tmp")),
                expected,
            )
        build.assert_called_once_with(
            candidates[0]["_node"],
            account,
            "Agent 库",
            "搭建技能",
        )


if __name__ == "__main__":
    unittest.main()
