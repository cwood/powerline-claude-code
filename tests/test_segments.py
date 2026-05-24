import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from powerline_claude_code import segments as S


class FormatTests(unittest.TestCase):
    def test_fmt_duration(self):
        self.assertEqual(S._fmt_duration(30), "<1m")
        self.assertEqual(S._fmt_duration(60), "1m")
        self.assertEqual(S._fmt_duration(47 * 60), "47m")
        self.assertEqual(S._fmt_duration(2 * 3600 + 13 * 60), "2h13m")
        self.assertEqual(S._fmt_duration(2 * 3600 + 3 * 60), "2h03m")

    def test_fmt_tokens(self):
        self.assertEqual(S._fmt_tokens(950), "950")
        self.assertEqual(S._fmt_tokens(12_300), "12.3k")
        self.assertEqual(S._fmt_tokens(1_500_000), "1.5M")


class MetricTests(unittest.TestCase):
    def test_file_buckets_and_metrics(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "t.jsonl"
            rec = {
                "type": "assistant",
                "timestamp": "2026-05-23T00:00:30.000Z",
                "message": {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 1000,
                        "cache_read_input_tokens": 5000,
                    }
                },
            }
            # A non-assistant line must be ignored.
            path.write_text(json.dumps(rec) + "\n" + json.dumps({"type": "user"}) + "\n")
            buckets = S._file_buckets(path)
            self.assertEqual(list(buckets.values()), [[100, 50, 1000, 5000]])

        comp = [100, 50, 1000, 5000]
        self.assertEqual(S._metric_value(comp, "new"), 150)
        self.assertEqual(S._metric_value(comp, "context"), 1150)
        self.assertEqual(S._metric_value(comp, "billable"), 6150)
        cost = S._metric_value(comp, "cost")
        self.assertAlmostEqual(
            cost, 100 * 15e-6 + 50 * 75e-6 + 1000 * 18.75e-6 + 5000 * 1.5e-6
        )


class BurnEtaTests(unittest.TestCase):
    def test_rising_yields_eta(self):
        now = 10_000.0
        # 40% -> 50% over 20 minutes => 0.5%/min; 50% left => 100 min => 1h40m.
        history = [[now - 1200, 40.0], [now, 50.0]]
        self.assertEqual(S._burn_eta(history, 50.0, now, 1800), "1h40m")

    def test_flat_or_falling_is_none(self):
        now = 10_000.0
        self.assertIsNone(S._burn_eta([[now - 600, 50.0], [now, 50.0]], 50.0, now, 1800))
        self.assertIsNone(S._burn_eta([[now - 600, 60.0], [now, 50.0]], 50.0, now, 1800))
        self.assertIsNone(S._burn_eta([[now, 50.0]], 50.0, now, 1800))


class QuotaTests(unittest.TestCase):
    def test_quota_render_with_reset(self):
        with TemporaryDirectory() as d:
            cache = Path(d) / "rl.json"
            now = 1_000_000.0
            cache.write_text(
                json.dumps(
                    {
                        "five_hour": {"used_percentage": 47.0, "resets_at": now + 2 * 3600 + 13 * 60},
                        "seven_day": {"used_percentage": 41.0, "resets_at": now + 99999},
                        "ts": now,
                        "history": [[now - 1200, 40.0], [now, 47.0]],
                    }
                )
            )
            with mock.patch.object(S, "RATE_LIMITS_CACHE", cache), mock.patch.object(
                S, "_now", return_value=now
            ):
                part = S._quota_part("◧", True, True, True, 70.0, 90.0, 1800)
        self.assertIn("5h 47%", part["contents"])
        self.assertIn("2h13m", part["contents"])
        self.assertIn("to cap", part["contents"])
        self.assertIn("7d 41%", part["contents"])
        self.assertEqual(part["highlight_groups"][0], "claude_code:quota")

    def test_quota_thresholds(self):
        with TemporaryDirectory() as d:
            cache = Path(d) / "rl.json"
            cache.write_text(json.dumps({"five_hour": {"used_percentage": 95.0}, "ts": 0}))
            with mock.patch.object(S, "RATE_LIMITS_CACHE", cache):
                part = S._quota_part("◧", False, False, False, 70.0, 90.0, 1800)
        self.assertEqual(part["highlight_groups"][0], "claude_code:quota_crit")

    def test_quota_absent(self):
        with TemporaryDirectory() as d:
            with mock.patch.object(S, "RATE_LIMITS_CACHE", Path(d) / "missing.json"):
                self.assertIsNone(S._quota_part("◧", False, True, True, 70.0, 90.0, 1800))


class AttentionTests(unittest.TestCase):
    def _flag(self, d, pane, reason, ts):
        (Path(d) / pane).write_text(json.dumps({"reason": reason, "ts": ts}))

    def test_render_cycle_glyph_label(self):
        glyphs = {"done": "✓", "permission": "⚠", "idle": "⏾"}
        live = {
            "%1": {"session": "0", "window": "1", "window_name": "nvim"},
            "%2": {"session": "work", "window": "3", "window_name": "docs"},
        }
        with TemporaryDirectory() as d:
            self._flag(d, "%1", "done", 100)
            self._flag(d, "%2", "permission", 50)
            with mock.patch.object(S, "ATTENTION_DIR", Path(d)), mock.patch.object(
                S, "_read_panes", return_value=live
            ), mock.patch.object(S, "_current_pane_id", return_value=None), mock.patch.object(
                S, "_now", return_value=0.0
            ):
                # idx = (0 // 4) % 2 = 0 -> freshest (%1, done).
                part = S._attention_part(4, glyphs, False, True, "{session}:{window_name}", False)
        self.assertEqual(part["contents"], "✓ 0:nvim (1/2)")
        self.assertEqual(part["highlight_groups"][0], "claude_code:attention_done")

    def test_cycle_advances(self):
        glyphs = {"done": "✓", "permission": "⚠", "idle": "⏾"}
        live = {
            "%1": {"session": "0", "window": "1", "window_name": "nvim"},
            "%2": {"session": "work", "window": "3", "window_name": "docs"},
        }
        with TemporaryDirectory() as d:
            self._flag(d, "%1", "done", 100)
            self._flag(d, "%2", "permission", 50)
            with mock.patch.object(S, "ATTENTION_DIR", Path(d)), mock.patch.object(
                S, "_read_panes", return_value=live
            ), mock.patch.object(S, "_current_pane_id", return_value=None), mock.patch.object(
                S, "_now", return_value=4.0
            ):
                part = S._attention_part(4, glyphs, False, True, "{session}:{window_name}", False)
        self.assertEqual(part["contents"], "⚠ work:docs (2/2)")

    def test_pulse_group(self):
        glyphs = {"done": "✓", "permission": "⚠", "idle": "⏾"}
        live = {"%1": {"session": "0", "window": "1", "window_name": "nvim"}}
        with TemporaryDirectory() as d:
            self._flag(d, "%1", "permission", 100)
            with mock.patch.object(S, "ATTENTION_DIR", Path(d)), mock.patch.object(
                S, "_read_panes", return_value=live
            ), mock.patch.object(S, "_current_pane_id", return_value=None), mock.patch.object(
                S, "_now", return_value=0.0
            ):
                part = S._attention_part(4, glyphs, False, True, "{window_name}", True)
        self.assertEqual(part["highlight_groups"][0], "claude_code:attention_permission_pulse")

    def test_stale_flag_removed(self):
        glyphs = {"done": "✓", "permission": "⚠", "idle": "⏾"}
        with TemporaryDirectory() as d:
            self._flag(d, "%9", "done", 100)  # pane not in live
            with mock.patch.object(S, "ATTENTION_DIR", Path(d)), mock.patch.object(
                S, "_read_panes", return_value={}
            ), mock.patch.object(S, "_current_pane_id", return_value=None):
                part = S._attention_part(4, glyphs, False, True, "{window_name}", False)
            self.assertIsNone(part)
            self.assertFalse((Path(d) / "%9").exists())

    def test_self_excluded(self):
        glyphs = {"done": "✓", "permission": "⚠", "idle": "⏾"}
        live = {"%1": {"session": "0", "window": "1", "window_name": "nvim"}}
        with TemporaryDirectory() as d:
            self._flag(d, "%1", "done", 100)
            with mock.patch.object(S, "ATTENTION_DIR", Path(d)), mock.patch.object(
                S, "_read_panes", return_value=live
            ), mock.patch.object(S, "_current_pane_id", return_value="%1"):
                self.assertIsNone(
                    S._attention_part(4, glyphs, False, True, "{window_name}", False)
                )


if __name__ == "__main__":
    unittest.main()
