"""Powerline segments for Claude Code.

Two segments:
    - `attention`: highlights tmux windows where Claude Code is waiting on you
      (paired with hooks that write flag files under ~/.claude/attention/).
    - `usage`: rolling token usage over configurable windows (default 5m/30m/1h),
      sourced from Claude Code's local transcript JSONL files. Highlights when
      the short-window rate spikes above the long-window baseline.

Configure in your powerline tmux theme like:

    { "function": "powerline_claude_code.attention", "priority": 30 }
    { "function": "powerline_claude_code.usage",     "priority": 25 }
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

ATTENTION_DIR = Path.home() / ".claude" / "attention"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
USAGE_CACHE_DIR = Path.home() / ".cache" / "powerline-claude-code"


def _read_panes() -> dict[str, str]:
    """Map tmux pane_id -> 'session:window' label for every live pane."""
    try:
        out = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{pane_id} #{session_name}:#{window_index}",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}

    mapping: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) == 2:
            mapping[parts[0]] = parts[1]
    return mapping


def _current_pane_id() -> str | None:
    return os.environ.get("TMUX_PANE")


def _flag_panes() -> list[str]:
    """Pane ids that have an attention flag on disk."""
    if not ATTENTION_DIR.is_dir():
        return []
    return [p.name for p in ATTENTION_DIR.iterdir() if p.is_file()]


def _cleanup_stale(flagged: list[str], live: dict[str, str]) -> list[str]:
    """Remove flag files for panes that no longer exist; return surviving ids."""
    surviving = []
    for pane_id in flagged:
        if pane_id in live:
            surviving.append(pane_id)
            continue
        try:
            (ATTENTION_DIR / pane_id).unlink(missing_ok=True)
        except OSError:
            pass
    return surviving


def attention(
    pl,
    glyph: str = "⚠",
    show_self: bool = False,
    format: str = "{glyph} {windows}",
    separator: str = ",",
) -> list[dict] | None:
    """Render windows needing attention.

    Args:
        glyph: Icon prefix (default ⚠).
        show_self: Include the current pane in the output (default False —
            you don't need a reminder about the window you're already in).
        format: Format string with {glyph} and {windows} placeholders.
        separator: Joiner between window labels.
    """
    flagged = _flag_panes()
    if not flagged:
        return None

    live = _read_panes()
    flagged = _cleanup_stale(flagged, live)
    if not flagged:
        return None

    if not show_self:
        self_pane = _current_pane_id()
        if self_pane:
            flagged = [p for p in flagged if p != self_pane]
        if not flagged:
            return None

    # Stable, unique label list (multiple panes in the same window collapse).
    labels = sorted({live[p] for p in flagged if p in live})
    if not labels:
        return None

    return [
        {
            "contents": format.format(glyph=glyph, windows=separator.join(labels)),
            "highlight_groups": ["claude_code:attention", "warning", "background"],
            "divider_highlight_group": "claude_code:divider",
        }
    ]


# ---------------------------------------------------------------------------
# Token usage segment
# ---------------------------------------------------------------------------


def _parse_iso(ts: str) -> float | None:
    """Parse '2026-05-10T04:03:37.060Z' → epoch seconds."""
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


def _file_buckets(path: Path) -> dict[str, int]:
    """Return {epoch_minute_str: tokens} for assistant messages in a JSONL file.

    Keys are str (not int) so the dict round-trips cleanly through JSON for
    caching.
    """
    buckets: dict[str, int] = {}
    try:
        with path.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                ts = _parse_iso(rec.get("timestamp", ""))
                if ts is None:
                    continue
                u = (rec.get("message") or {}).get("usage") or {}
                # Sum every billable token component — gives a faithful "work
                # done" number that responds to long contexts (cache reads
                # dominate) as well as raw output.
                tokens = (
                    int(u.get("input_tokens", 0) or 0)
                    + int(u.get("output_tokens", 0) or 0)
                    + int(u.get("cache_creation_input_tokens", 0) or 0)
                    + int(u.get("cache_read_input_tokens", 0) or 0)
                )
                if not tokens:
                    continue
                minute = str(int(ts // 60))
                buckets[minute] = buckets.get(minute, 0) + tokens
    except OSError:
        pass
    return buckets


def _all_buckets(retention_seconds: int = 7200) -> dict[int, int]:
    """Aggregate per-minute token totals across every transcript modified
    within `retention_seconds` (default 2h, gives headroom over the 1h window).

    Per-file results are cached in ~/.cache/powerline-claude-code/usage.json
    keyed by mtime; only files whose mtime changed since the last call get
    re-parsed. Stale entries (file gone, or older than retention) are dropped.
    """
    cache_path = USAGE_CACHE_DIR / "usage.json"
    try:
        USAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}

    cache: dict = {}
    try:
        with cache_path.open() as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        cache = {}

    if not PROJECTS_DIR.is_dir():
        return {}

    cutoff = time.time() - retention_seconds
    new_cache: dict = {}
    changed = False

    for jsonl in PROJECTS_DIR.rglob("*.jsonl"):
        path_str = str(jsonl)
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        cached = cache.get(path_str)
        if cached and cached.get("mtime") == mtime:
            new_cache[path_str] = cached
            continue
        new_cache[path_str] = {"mtime": mtime, "buckets": _file_buckets(jsonl)}
        changed = True

    if changed or len(new_cache) != len(cache):
        try:
            with cache_path.open("w") as f:
                json.dump(new_cache, f)
        except OSError:
            pass

    agg: dict[int, int] = {}
    for entry in new_cache.values():
        for minute_str, tok in (entry.get("buckets") or {}).items():
            try:
                m = int(minute_str)
            except (TypeError, ValueError):
                continue
            agg[m] = agg.get(m, 0) + int(tok)
    return agg


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def usage(
    pl,
    windows=(5, 30, 60),
    spike_ratio: float = 2.0,
    glyph: str = "Σ",
    show_zero: bool = False,
) -> list[dict] | None:
    """Render rolling Claude Code token usage.

    Args:
        windows: tuple of minute windows (default 5/30/60). Order matters —
            first is "short" (used for spike detection), last is "long" (used
            as baseline rate).
        spike_ratio: short-window per-minute rate must exceed long-window rate
            by this factor to trigger the spike highlight (default 2.0).
        glyph: prefix icon (default 'Σ').
        show_zero: render the segment even when no usage has occurred in the
            longest window (default False — hide on quiet shells).

    Output format: ``Σ 5m:25k 30m:120k 1h:180k`` (windows >= 60 collapse to
    "1h", "2h" etc).
    """
    if not windows:
        return None

    buckets = _all_buckets()
    if not buckets and not show_zero:
        return None

    now_minute = int(time.time() // 60)
    parts = []
    rates: dict[int, float] = {}
    any_nonzero = False

    for w in windows:
        cutoff_min = now_minute - w
        total = sum(t for m, t in buckets.items() if m > cutoff_min)
        if total:
            any_nonzero = True
        rates[w] = total / w if w > 0 else 0
        label = f"{w // 60}h" if w >= 60 else f"{w}m"
        parts.append(f"{label}:{_format_tokens(total)}")

    if not any_nonzero and not show_zero:
        return None

    short_w = windows[0]
    long_w = windows[-1]
    is_spike = (
        rates.get(long_w, 0) > 0
        and rates.get(short_w, 0) > rates[long_w] * spike_ratio
    )

    primary = "claude_code:usage_spike" if is_spike else "claude_code:usage"
    fallback = "warning" if is_spike else "information:additional"

    return [
        {
            "contents": f"{glyph} {' '.join(parts)}",
            "highlight_groups": [primary, fallback, "background"],
            "divider_highlight_group": "claude_code:divider",
        }
    ]
