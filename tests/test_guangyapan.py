import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from local_video.guangyapan import Guangyapan


class GuangyapanTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.installation = self.root / "skill"
        self.cli = self.installation / "bin" / "guangyapan.mjs"
        self.instructions = self.installation / "references" / "skill.md"
        for path in (self.cli, self.instructions):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        self.transport = Mock()
        self.adapter = Guangyapan(
            self.root / "data",
            installation=self.installation,
            node=self.root / "node.exe",
            transport=self.transport,
        )

    def prepare(self, **overrides):
        return {
            "command": "prepare",
            "version": 1,
            "code": "OK",
            "data": {
                "instructions_version": "1.0.3+28",
                "instructions_path": str(self.instructions),
                "cli_path": str(self.cli),
                **overrides,
            },
        }

    def test_status_uses_x2lt_identity_and_isolated_environment(self):
        self.transport.side_effect = [
            self.prepare(),
            {"code": "OK", "data": {"logged_in": True, "account_id": "acct"}},
        ]
        with patch.dict("os.environ", {
            "GUANGYAPAN_DRIVE_CREDENTIAL_FILE": "foreign-credentials",
            "GUANGYAPAN_DRIVE_STATE_ROOT": "foreign-state",
            "NODE_OPTIONS": "--require foreign.js",
        }):
            self.assertEqual(self.adapter.status()["account_id"], "acct")
        args, kwargs = self.transport.call_args
        self.assertEqual(args[0][2:7], ["login-status", "--skill-version", "1.0.3+28", "--agent-name", "X2LT"])
        self.assertEqual(Path(args[0][-1]), self.root / "data" / "guangyapan" / "agent-profile")
        self.assertEqual(Path(kwargs["env"]["GUANGYAPAN_DRIVE_STATE_ROOT"]), self.root / "data" / "guangyapan" / "state")
        self.assertNotIn("GUANGYAPAN_DRIVE_CREDENTIAL_FILE", kwargs["env"])
        self.assertNotIn("NODE_OPTIONS", kwargs["env"])

    def test_refresh_keeps_identical_payload_and_request_id(self):
        receipt = {"code": "SUBMIT_PENDING", "batch_id": "active"}
        self.transport.side_effect = [
            self.prepare(), {"code": "SKILL_RELOAD_REQUIRED"}, self.prepare(), receipt,
        ]
        self.assertEqual(self.adapter.submit({
            "id": "upload", "type": "upload", "local_path": "staged", "remote_parent_id": "1",
        }, "stable-request", "stable-task"), receipt)
        calls = self.transport.call_args_list
        self.assertEqual(calls[1].kwargs["stdin"], calls[3].kwargs["stdin"])
        self.assertEqual(json.loads(calls[3].kwargs["stdin"])["request_id"], "stable-request")

    def test_rejects_unexpected_executable_from_prepare(self):
        self.transport.return_value = self.prepare(cli_path=str(self.root / "injected.mjs"))
        with self.assertRaisesRegex(ValueError, "入口"):
            self.adapter.login()
        self.assertEqual(self.transport.call_count, 1)

    def test_list_failure_conceals_provider_details(self):
        self.transport.side_effect = [self.prepare(), {
            "code": "READ_PARTIAL", "data": {"items": [
                {"state": "FAILED", "code": "API_ERROR", "message": "internal credential"},
            ]},
        }]
        with self.assertRaises(ValueError) as caught:
            self.adapter.list("")
        self.assertNotIn("internal credential", str(caught.exception))

    def test_transport_parses_official_json_even_with_exit_one(self):
        from local_video.guangyapan import _invoke
        process = Mock()
        process.stdout = io.BytesIO(b'{"version":1,"code":"READ_PARTIAL"}\n')
        process.wait.return_value = 1
        with patch("local_video.guangyapan.subprocess.Popen", return_value=process):
            self.assertEqual(_invoke(["node"], cwd=self.root, env={}, timeout=2)["code"], "READ_PARTIAL")

    def test_subprocess_can_receive_utf8_json_on_windows_stdin(self):
        from local_video.guangyapan import _invoke
        script = (
            "import sys,json;"
            "value=json.load(sys.stdin.buffer);"
            "print(json.dumps(dict(version=1,code='OK',value=value)));"
            "sys.exit(1)"
        )
        result = _invoke(
            [sys.executable, "-c", script], cwd=self.root, env=dict(os.environ), timeout=5,
            stdin=json.dumps({"name": "测试.mp4"}, ensure_ascii=False).encode("utf-8"),
        )
        self.assertEqual(result["value"], {"name": "测试.mp4"})

    def test_mkdir_returns_reported_id_after_batch_status(self):
        self.transport.side_effect = [
            self.prepare(), {"code": "SUBMIT_PENDING", "batch_id": "active"},
            {"code": "OK", "batch_id": "active", "batch_state": "SUCCEEDED", "data": {"items": [
                {"operation_id": "folder", "state": "SUCCEEDED", "result": {"fileId": "folder-id"}},
            ]}},
        ]
        self.assertEqual(self.adapter.mkdir("parent", "archive"), "folder-id")
        self.assertEqual(self.transport.call_args.args[0][2], "status")
