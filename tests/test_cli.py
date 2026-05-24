import io
import json
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from powerline_claude_code import cli
from powerline_claude_code import segments as S


class HookSettingsTests(unittest.TestCase):
    def test_install_preserves_foreign_and_is_idempotent(self):
        settings = {
            "hooks": {
                "Stop": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]
            }
        }
        prefix = "py -m powerline_claude_code hook"
        cli.install_hooks(settings, prefix)
        stop = settings["hooks"]["Stop"]
        self.assertEqual(len(stop), 2)
        self.assertTrue(any(m["matcher"] == "Bash" for m in stop))
        self.assertTrue(any(cli._matcher_is_ours(m) for m in stop))
        for event in ("Notification", "UserPromptSubmit"):
            self.assertEqual(len(settings["hooks"][event]), 1)

        # Re-running must not stack duplicate ours-matchers.
        cli.install_hooks(settings, prefix)
        self.assertEqual(len(settings["hooks"]["Stop"]), 2)

    def test_uninstall_hooks_keeps_foreign(self):
        settings = {}
        cli.install_hooks(settings, "py -m powerline_claude_code hook")
        settings["hooks"]["Stop"].insert(
            0, {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
        )
        cli.uninstall_hooks(settings)
        self.assertEqual(
            settings["hooks"], {"Stop": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]}
        )


class StatusLineSettingsTests(unittest.TestCase):
    def test_chains_foreign_then_leaves_ours(self):
        settings = {"statusLine": {"type": "command", "command": "my-bar --opt"}}
        settings, chain = cli.install_statusline(settings, "OURS -m powerline_claude_code statusline")
        self.assertEqual(chain, "my-bar --opt")
        self.assertIn("powerline_claude_code", settings["statusLine"]["command"])

        # Second pass: already ours, nothing new to chain.
        settings, chain = cli.install_statusline(settings, "OURS -m powerline_claude_code statusline")
        self.assertIsNone(chain)

    def test_uninstall_restores_chain(self):
        settings = {"statusLine": {"type": "command", "command": "x -m powerline_claude_code statusline"}}
        cli.uninstall_statusline(settings, "my-bar")
        self.assertEqual(settings["statusLine"], {"type": "command", "command": "my-bar"})

    def test_uninstall_drops_when_no_chain(self):
        settings = {"statusLine": {"type": "command", "command": "x -m powerline_claude_code statusline"}}
        cli.uninstall_statusline(settings, None)
        self.assertNotIn("statusLine", settings)


class RecordRateLimitsTests(unittest.TestCase):
    def test_writes_cache_and_history(self):
        with TemporaryDirectory() as d:
            cache = Path(d) / "rl.json"
            with mock.patch.object(S, "RATE_LIMITS_CACHE", cache):
                cli._record_rate_limits(
                    {"rate_limits": {"five_hour": {"used_percentage": 47, "resets_at": 123}}}
                )
                cli._record_rate_limits(
                    {"rate_limits": {"five_hour": {"used_percentage": 49, "resets_at": 123}}}
                )
            data = json.loads(cache.read_text())
        self.assertEqual(data["five_hour"]["used_percentage"], 49)
        self.assertEqual(len(data["history"]), 2)

    def test_no_rate_limits_writes_nothing(self):
        with TemporaryDirectory() as d:
            cache = Path(d) / "rl.json"
            with mock.patch.object(S, "RATE_LIMITS_CACHE", cache):
                cli._record_rate_limits({"cost": {"total_cost_usd": 1}})
            self.assertFalse(cache.exists())


class HookCommandTests(unittest.TestCase):
    def _run_hook(self, event, stdin, tmp, pane="%5"):
        fake = io.TextIOWrapper(io.BytesIO(stdin))
        with mock.patch.object(S, "ATTENTION_DIR", Path(tmp)), mock.patch.dict(
            "os.environ", {"TMUX_PANE": pane}
        ), mock.patch.object(cli.sys, "stdin", fake):
            cli.cmd_hook(Namespace(event=event))

    def test_stop_writes_done(self):
        with TemporaryDirectory() as d:
            self._run_hook("stop", b"{}", d)
            meta = json.loads((Path(d) / "%5").read_text())
            self.assertEqual(meta["reason"], "done")

    def test_notification_permission_vs_idle(self):
        with TemporaryDirectory() as d:
            self._run_hook("notification", json.dumps({"notification_type": "permission_prompt"}).encode(), d)
            self.assertEqual(json.loads((Path(d) / "%5").read_text())["reason"], "permission")
            self._run_hook("notification", json.dumps({"notification_type": "idle_prompt"}).encode(), d)
            self.assertEqual(json.loads((Path(d) / "%5").read_text())["reason"], "idle")

    def test_clear_removes_flag(self):
        with TemporaryDirectory() as d:
            (Path(d) / "%5").write_text("{}")
            self._run_hook("clear", b"", d)
            self.assertFalse((Path(d) / "%5").exists())

    def test_no_tmux_pane_is_noop(self):
        with TemporaryDirectory() as d:
            fake = io.TextIOWrapper(io.BytesIO(b"{}"))
            with mock.patch.object(S, "ATTENTION_DIR", Path(d)), mock.patch.dict(
                "os.environ", {}, clear=True
            ), mock.patch.object(cli.sys, "stdin", fake):
                cli.cmd_hook(Namespace(event="stop"))
            self.assertEqual(list(Path(d).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
