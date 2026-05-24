"""Powerline segments for Claude Code.

Public segment functions (reference these from your powerline tmux theme):

    status     mode-switching: subscription quota by default, but when an agent
               needs you it takes over and cycles through the waiting tmux
               windows (one per refresh). This is the recommended one slot.
    quota      subscription rate-limit usage (5h/7d %, time to reset, burn ETA).
    attention  just the cycling attention indicator.
    usage      rolling token volume from transcripts, for API-billing users.

Quota data is captured from Claude Code's statusLine into
``~/.cache/powerline-claude-code/rate_limits.json`` (see the ``statusline``
CLI command); attention flags are written by the ``hook`` CLI command. Run
``powerline-claude-code setup`` once to wire both up.
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
CACHE_DIR = Path.home() / ".cache" / "powerline-claude-code"
CONFIG_DIR = Path.home() / ".config" / "powerline-claude-code"
RATE_LIMITS_CACHE = CACHE_DIR / "rate_limits.json"
USAGE_CACHE = CACHE_DIR / "usage-v2.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Attention reasons → default glyph and the generic powerline group to fall
# back to when the user hasn't defined the claude_code:* group.
_REASON_FALLBACK = {
    "done": "information:additional",
    "permission": "warning",
    "idle": "warning",
}

# Rough per-token USD weights for the "cost" usage metric (Opus 4 list prices).
# Only used by the opt-in cost view; documented as an estimate.
_COST = {"in": 15e-6, "out": 75e-6, "cc": 18.75e-6, "cr": 1.5e-6}


def _now() -> float:
    """Indirection so tests can pin the clock."""
    return time.time()


def _load_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return "<1m"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ---------------------------------------------------------------------------
# tmux + attention flags
# ---------------------------------------------------------------------------


def _read_panes() -> dict[str, dict]:
    """Map tmux pane_id -> pane info for every live pane.

    ``active``/``attached`` together identify the window currently on screen
    (active window of a session a client is attached to); ``created`` is the
    session's creation epoch, used to prune flags left over from before a
    tmux server restart.
    """
    try:
        out = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{pane_id}\t#{session_name}\t#{window_index}\t#{window_name}"
                "\t#{window_active}\t#{session_attached}\t#{session_created}",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}

    panes: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 7:
            panes[parts[0]] = {
                "session": parts[1],
                "window": parts[2],
                "window_name": parts[3],
                "active": parts[4] == "1",
                "attached": int(parts[5]) if parts[5].isdigit() else 0,
                "created": int(parts[6]) if parts[6].isdigit() else 0,
            }
    return panes


def _current_pane_id() -> str | None:
    return os.environ.get("TMUX_PANE")


def _label(pane: dict[str, str], label_format: str) -> str:
    try:
        return label_format.format(
            session=pane.get("session", ""),
            window=pane.get("window", ""),
            window_name=pane.get("window_name", ""),
        )
    except (KeyError, IndexError):
        return f"{pane.get('session', '')}:{pane.get('window_name', '')}"


def _flag_meta() -> dict[str, dict]:
    """Pane id -> {reason, ts} for every attention flag on disk.

    Our hook writes JSON atomically, so empty or unparseable files (e.g.
    leftovers from the old touch-based hook) are ignored rather than shown.
    """
    if not ATTENTION_DIR.is_dir():
        return {}
    flags: dict[str, dict] = {}
    for p in ATTENTION_DIR.iterdir():
        if not p.is_file():
            continue
        try:
            mtime = p.stat().st_mtime
            txt = p.read_text().strip()
            meta = json.loads(txt) if txt else None
        except (OSError, json.JSONDecodeError):
            meta = None
        if not isinstance(meta, dict):
            continue
        flags[p.name] = {"reason": meta.get("reason") or "permission", "ts": meta.get("ts") or mtime}
    return flags


def _attention_part(
    cycle_seconds: float,
    glyphs: dict[str, str],
    show_self: bool,
    show_count: bool,
    label_format: str,
    pulse: bool,
    attached_only: bool = True,
) -> dict | None:
    flags = _flag_meta()
    if not flags:
        return None

    live = _read_panes()
    # Prune flags whose pane is gone, or that predate the current tmux server
    # (pane ids reset on restart, so an old %0 file would shadow a new %0).
    server_start = min((info["created"] for info in live.values()), default=0)
    for pane in list(flags):
        orphan = pane not in live or (server_start and flags[pane]["ts"] < server_start)
        if orphan:
            try:
                (ATTENTION_DIR / pane).unlink(missing_ok=True)
            except OSError:
                pass
            del flags[pane]
    if not flags:
        return None

    if attached_only:
        # Only the session(s) you're currently in — ignore detached sessions.
        flags = {p: m for p, m in flags.items() if live.get(p, {}).get("attached")}
        if not flags:
            return None

    if not show_self:
        # Hide whatever's on screen: the active window of any attached session,
        # plus $TMUX_PANE when powerline happens to have it.
        drop = {p for p, info in live.items() if info["active"] and info["attached"]}
        me = _current_pane_id()
        if me:
            drop.add(me)
        flags = {p: m for p, m in flags.items() if p not in drop}
        if not flags:
            return None

    # Freshest first, collapsing panes that resolve to the same window label.
    items = sorted(
        (
            (meta["ts"], _label(live[pane], label_format), meta["reason"])
            for pane, meta in flags.items()
        ),
        key=lambda x: x[0],
        reverse=True,
    )
    seen: set[str] = set()
    uniq = [it for it in items if not (it[1] in seen or seen.add(it[1]))]

    total = len(uniq)
    idx = int(_now() // cycle_seconds) % total
    _, label, reason = uniq[idx]

    glyph = glyphs.get(reason, glyphs["permission"])
    count = f" ({idx + 1}/{total})" if show_count and total > 1 else ""
    group = f"claude_code:attention_{reason}"
    groups = [group]
    if pulse and int(_now()) % 2 == 0:
        groups.insert(0, f"{group}_pulse")
    groups += [_REASON_FALLBACK.get(reason, "warning"), "background"]

    return {
        "contents": f"{glyph} {label}{count}",
        "highlight_groups": groups,
        "divider_highlight_group": "claude_code:divider",
    }


# ---------------------------------------------------------------------------
# Subscription quota (from the statusLine rate_limits capture)
# ---------------------------------------------------------------------------


def _burn_eta(history: list, pct_now: float, now: float, window: int) -> str | None:
    """Estimate time until the 5h window hits 100% at the recent burn rate.

    history is a list of [epoch, used_percentage] snapshots. Returns a
    formatted duration, or None when not meaningfully rising.
    """
    pts = sorted([p for p in history if p[0] >= now - window])
    if len(pts) < 2:
        return None
    (t0, p0), (t1, p1) = pts[0], pts[-1]
    minutes = (t1 - t0) / 60.0
    if minutes <= 0:
        return None
    rate = (p1 - p0) / minutes  # percent per minute
    if rate <= 0.05:
        return None
    remaining = (100 - pct_now) / rate
    if remaining <= 0:
        return None
    return _fmt_duration(remaining * 60)


def _quota_part(
    glyph: str,
    show_week: bool,
    show_reset: bool,
    show_eta: bool,
    warn: float,
    crit: float,
    eta_window: int,
) -> dict | None:
    cache = _load_json(RATE_LIMITS_CACHE)
    five = cache.get("five_hour") or {}
    pct = five.get("used_percentage")
    if pct is None:
        return None

    parts = [f"5h {pct:.0f}%"]
    now = _now()
    if show_reset and five.get("resets_at"):
        remaining = five["resets_at"] - now
        parts.append(_fmt_duration(remaining) if remaining > 0 else "reset")
    if show_eta:
        eta = _burn_eta(cache.get("history") or [], pct, now, eta_window)
        if eta:
            parts.append(f"~{eta} to cap")
    if show_week:
        week = (cache.get("seven_day") or {}).get("used_percentage")
        if week is not None:
            parts.append(f"7d {week:.0f}%")

    if pct >= crit:
        group, fallback = "claude_code:quota_crit", "critical:failure"
    elif pct >= warn:
        group, fallback = "claude_code:quota_warn", "warning"
    else:
        group, fallback = "claude_code:quota", "information:additional"

    return {
        "contents": f"{glyph} {' · '.join(parts)}",
        "highlight_groups": [group, fallback, "background"],
        "divider_highlight_group": "claude_code:divider",
    }


# ---------------------------------------------------------------------------
# Token volume from transcripts (opt-in, API-billing oriented)
# ---------------------------------------------------------------------------

# Component order in a bucket: input, output, cache_creation, cache_read.
_METRIC_COMPONENTS = {"new": (0, 1), "context": (0, 1, 2), "billable": (0, 1, 2, 3)}


def _parse_iso(ts: str) -> float | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


def _file_buckets(path: Path) -> dict[str, list]:
    """{epoch_minute_str: [input, output, cache_creation, cache_read]}."""
    buckets: dict[str, list] = {}
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
                comp = [
                    int(u.get("input_tokens", 0) or 0),
                    int(u.get("output_tokens", 0) or 0),
                    int(u.get("cache_creation_input_tokens", 0) or 0),
                    int(u.get("cache_read_input_tokens", 0) or 0),
                ]
                if not any(comp):
                    continue
                minute = str(int(ts // 60))
                acc = buckets.setdefault(minute, [0, 0, 0, 0])
                for i in range(4):
                    acc[i] += comp[i]
    except OSError:
        pass
    return buckets


def _all_buckets(retention_seconds: int = 7200) -> dict[int, list]:
    """Per-minute token components across transcripts modified recently.

    Per-file results are mtime-cached so repeated status refreshes only
    re-parse transcripts that actually changed.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}
    if not PROJECTS_DIR.is_dir():
        return {}

    cache = _load_json(USAGE_CACHE)
    cutoff = _now() - retention_seconds
    fresh: dict = {}
    changed = False

    for jsonl in PROJECTS_DIR.rglob("*.jsonl"):
        key = str(jsonl)
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        cached = cache.get(key)
        if cached and cached.get("mtime") == mtime:
            fresh[key] = cached
            continue
        fresh[key] = {"mtime": mtime, "buckets": _file_buckets(jsonl)}
        changed = True

    if changed or len(fresh) != len(cache):
        try:
            _write_json(USAGE_CACHE, fresh)
        except OSError:
            pass

    agg: dict[int, list] = {}
    for entry in fresh.values():
        for minute_str, comp in (entry.get("buckets") or {}).items():
            try:
                m = int(minute_str)
            except (TypeError, ValueError):
                continue
            acc = agg.setdefault(m, [0, 0, 0, 0])
            for i in range(4):
                acc[i] += int(comp[i])
    return agg


def _metric_value(comp: list, metric: str) -> float:
    if metric == "cost":
        return (
            comp[0] * _COST["in"]
            + comp[1] * _COST["out"]
            + comp[2] * _COST["cc"]
            + comp[3] * _COST["cr"]
        )
    return sum(comp[i] for i in _METRIC_COMPONENTS.get(metric, _METRIC_COMPONENTS["new"]))


def _fmt_metric(value: float, metric: str) -> str:
    return f"${value:.2f}" if metric == "cost" else _fmt_tokens(int(value))


def _usage_part(
    metric: str,
    windows: tuple,
    spike_ratio: float,
    glyph: str,
    show_zero: bool,
) -> dict | None:
    if not windows:
        return None
    buckets = _all_buckets()
    if not buckets and not show_zero:
        return None

    now_minute = int(_now() // 60)
    parts = []
    rates: dict[int, float] = {}
    any_nonzero = False
    for w in windows:
        cutoff = now_minute - w
        total = sum(
            _metric_value(comp, metric) for m, comp in buckets.items() if m > cutoff
        )
        if total:
            any_nonzero = True
        rates[w] = total / w if w > 0 else 0
        label = f"{w // 60}h" if w >= 60 else f"{w}m"
        parts.append(f"{label}:{_fmt_metric(total, metric)}")

    if not any_nonzero and not show_zero:
        return None

    short, long = windows[0], windows[-1]
    spike = rates.get(long, 0) > 0 and rates.get(short, 0) > rates[long] * spike_ratio
    group = "claude_code:usage_spike" if spike else "claude_code:usage"
    fallback = "warning" if spike else "information:additional"

    return {
        "contents": f"{glyph} {' '.join(parts)}",
        "highlight_groups": [group, fallback, "background"],
        "divider_highlight_group": "claude_code:divider",
    }


# ---------------------------------------------------------------------------
# Public segment functions
# ---------------------------------------------------------------------------


def status(
    pl,
    cycle_seconds: float = 4,
    pulse: bool = False,
    show_self: bool = False,
    show_count: bool = True,
    attached_only: bool = True,
    label_format: str = "{session}:{window_name}",
    done_glyph: str = "✓",
    permission_glyph: str = "⚠",
    idle_glyph: str = "⏾",
    base: str = "quota",
    quota_glyph: str = "◧",
    show_week: bool = False,
    show_reset: bool = True,
    show_eta: bool = True,
    warn: float = 70.0,
    crit: float = 90.0,
) -> list[dict] | None:
    """Combined segment: attention takes over when present, else the base view.

    Args:
        cycle_seconds: seconds each waiting window is shown before cycling.
        pulse: alternate between the reason group and ``<group>_pulse`` each
            second so the segment throbs even on terminals that ignore blink.
        show_self: include the current pane among waiting windows.
        show_count: append ``(n/total)`` when more than one window waits.
        attached_only: only alert for windows in a session you're attached to;
            ignore detached sessions (default True).
        label_format: ``str.format`` template over ``session``, ``window``
            (index) and ``window_name``.
        base: what to show when nothing needs attention — ``"quota"`` or
            ``"usage"``.
        show_week, show_reset, show_eta, warn, crit: passed to the quota view.
    """
    glyphs = {"done": done_glyph, "permission": permission_glyph, "idle": idle_glyph}
    part = _attention_part(
        cycle_seconds, glyphs, show_self, show_count, label_format, pulse, attached_only
    )
    if part:
        return [part]
    if base == "usage":
        part = _usage_part("new", (5, 30, 60), 2.0, "Σ", False)
    else:
        part = _quota_part(quota_glyph, show_week, show_reset, show_eta, warn, crit, 1800)
    return [part] if part else None


def quota(
    pl,
    glyph: str = "◧",
    show_week: bool = False,
    show_reset: bool = True,
    show_eta: bool = True,
    warn: float = 70.0,
    crit: float = 90.0,
    eta_window: int = 1800,
) -> list[dict] | None:
    """Subscription rate-limit usage: 5h %, time to reset, burn ETA."""
    part = _quota_part(glyph, show_week, show_reset, show_eta, warn, crit, eta_window)
    return [part] if part else None


def attention(
    pl,
    cycle_seconds: float = 4,
    pulse: bool = False,
    show_self: bool = False,
    show_count: bool = True,
    attached_only: bool = True,
    label_format: str = "{session}:{window_name}",
    done_glyph: str = "✓",
    permission_glyph: str = "⚠",
    idle_glyph: str = "⏾",
) -> list[dict] | None:
    """Cycling indicator for tmux windows where Claude Code is waiting on you."""
    glyphs = {"done": done_glyph, "permission": permission_glyph, "idle": idle_glyph}
    part = _attention_part(
        cycle_seconds, glyphs, show_self, show_count, label_format, pulse, attached_only
    )
    return [part] if part else None


def usage(
    pl,
    metric: str = "new",
    windows: tuple = (5, 30, 60),
    spike_ratio: float = 2.0,
    glyph: str = "Σ",
    show_zero: bool = False,
) -> list[dict] | None:
    """Rolling token volume from transcripts (API-billing oriented).

    Args:
        metric: ``new`` (input+output, default), ``context`` (+cache_creation),
            ``billable`` (all four — large, cache-read dominated) or ``cost``
            (estimated USD).
        windows: minute windows; first is the spike numerator, last the
            denominator/baseline.
        spike_ratio: highlight when short-window rate exceeds long by this.
    """
    part = _usage_part(metric, windows, spike_ratio, glyph, show_zero)
    return [part] if part else None
