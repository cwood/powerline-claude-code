"""Command-line entry point: setup, doctor, statusline capture, hooks.

Run ``powerline-claude-code setup`` once to install the Claude Code hooks
(attention) and the statusLine capture (quota). ``doctor`` reports what's
wired up. The ``statusline`` and ``hook`` subcommands are invoked by Claude
Code itself, not by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import segments as S

# A command is "ours" if it mentions the package under either spelling
# (underscore = current `-m` form, hyphen = the retired bash installer).
_MARKERS = ("powerline_claude_code", "powerline-claude-code")

# Hook event -> the `hook` subcommand it should run. SessionEnd clears the flag
# when Claude exits, so a finished session doesn't linger until the next prompt.
_HOOK_EVENTS = {
    "Notification": "notification",
    "Stop": "stop",
    "UserPromptSubmit": "clear",
    "SessionEnd": "clear",
}

# Keep enough 5h snapshots to derive a burn rate; trims oldest beyond this.
_HISTORY_MAX = 120


def _settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_SETTINGS", Path.home() / ".claude" / "settings.json"))


def _self_cmd(*args: str) -> str:
    """Invoke this package via the exact interpreter that owns it, so the hook
    works regardless of whether the console script is on PATH."""
    return " ".join([shlex.quote(sys.executable), "-m", "powerline_claude_code", *args])


def _has_marker(command: str) -> bool:
    return any(m in (command or "") for m in _MARKERS)


# ---------------------------------------------------------------------------
# Pure settings transforms (unit-tested without touching disk)
# ---------------------------------------------------------------------------


def _matcher_is_ours(matcher: dict) -> bool:
    return any(_has_marker(h.get("command", "")) for h in matcher.get("hooks", []))


def install_hooks(settings: dict, hook_prefix: str) -> dict:
    """Upsert our three hooks, preserving any foreign matchers on each event."""
    hooks = settings.setdefault("hooks", {})
    for event, sub in _HOOK_EVENTS.items():
        kept = [m for m in hooks.get(event, []) if not _matcher_is_ours(m)]
        kept.append(
            {"matcher": "*", "hooks": [{"type": "command", "command": f"{hook_prefix} {sub}"}]}
        )
        hooks[event] = kept
    return settings


def install_statusline(settings: dict, our_cmd: str) -> tuple[dict, str | None]:
    """Point statusLine at our capture command.

    Returns the discovered foreign command to chain, or None if there was no
    statusLine or it was already ours.
    """
    existing = settings.get("statusLine")
    chain = None
    if isinstance(existing, dict):
        cmd = existing.get("command", "")
        if cmd and not _has_marker(cmd):
            chain = cmd
    settings["statusLine"] = {"type": "command", "command": our_cmd}
    return settings, chain


def uninstall_hooks(settings: dict) -> dict:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        kept = [m for m in hooks[event] if not _matcher_is_ours(m)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return settings


def uninstall_statusline(settings: dict, chain: str | None) -> dict:
    existing = settings.get("statusLine")
    if isinstance(existing, dict) and _has_marker(existing.get("command", "")):
        if chain:
            settings["statusLine"] = {"type": "command", "command": chain}
        else:
            settings.pop("statusLine", None)
    return settings


# ---------------------------------------------------------------------------
# Disk IO helpers
# ---------------------------------------------------------------------------


def _load_settings() -> dict:
    path = _settings_path()
    return S._load_json(path) if path.exists() else {}


def _save_settings(settings: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _load_config() -> dict:
    return S._load_json(S.CONFIG_FILE)


def _save_config(config: dict) -> None:
    S._write_json(S.CONFIG_FILE, config)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_setup(args) -> int:
    if args.uninstall:
        return _do_uninstall()

    for d in (S.ATTENTION_DIR, S.CACHE_DIR, S.CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    settings = _load_settings()
    install_hooks(settings, _self_cmd("hook"))
    settings, found_chain = install_statusline(settings, _self_cmd("statusline"))
    _save_settings(settings)

    prev_chain = _load_config().get("chain")
    _save_config({"chain": found_chain if found_chain is not None else prev_chain})

    print(f"Installed hooks + statusLine capture in {_settings_path()}")
    if found_chain:
        print(f"Chained your existing statusLine: {found_chain}")
    print("\nAdd one segment to your powerline tmux theme:")
    print('  { "function": "powerline_claude_code.status", "priority": 30 }')
    print("\nThen check everything with:  powerline-claude-code doctor")
    return 0


def _do_uninstall() -> int:
    settings = _load_settings()
    uninstall_hooks(settings)
    uninstall_statusline(settings, _load_config().get("chain"))
    _save_settings(settings)
    print(f"Removed powerline-claude-code hooks and statusLine from {_settings_path()}")
    return 0


def _record_rate_limits(data: dict) -> None:
    rl = data.get("rate_limits") or {}
    five, week = rl.get("five_hour"), rl.get("seven_day")
    if not five and not week:
        return
    cache = S._load_json(S.RATE_LIMITS_CACHE)
    now = time.time()
    if five:
        cache["five_hour"] = {
            "used_percentage": five.get("used_percentage"),
            "resets_at": five.get("resets_at"),
        }
    if week:
        cache["seven_day"] = {
            "used_percentage": week.get("used_percentage"),
            "resets_at": week.get("resets_at"),
        }
    cache["ts"] = now
    pct = (five or {}).get("used_percentage")
    if pct is not None:
        history = (cache.get("history") or [])
        history.append([now, pct])
        cache["history"] = history[-_HISTORY_MAX:]
    S._write_json(S.RATE_LIMITS_CACHE, cache)


def cmd_statusline(args) -> int:
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw or b"{}")
    except (ValueError, TypeError):
        data = {}
    try:
        _record_rate_limits(data)
    except OSError:
        pass

    chain = _load_config().get("chain")
    if chain:
        try:
            result = subprocess.run(chain, shell=True, input=raw, stdout=subprocess.PIPE)
            sys.stdout.buffer.write(result.stdout)
        except OSError:
            pass
    return 0


def cmd_hook(args) -> int:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return 0
    flag = S.ATTENTION_DIR / pane
    try:
        if args.event == "clear":
            flag.unlink(missing_ok=True)
            return 0
        raw = sys.stdin.buffer.read()
        try:
            data = json.loads(raw or b"{}")
        except (ValueError, TypeError):
            data = {}
        if args.event == "stop":
            reason = "done"
        else:
            nt = data.get("notification_type", "")
            reason = "idle" if "idle" in nt else "permission"
        S.ATTENTION_DIR.mkdir(parents=True, exist_ok=True)
        S._write_json(flag, {"reason": reason, "ts": time.time(), "session": data.get("session_id")})
    except OSError:
        pass
    return 0


def _check(ok: bool, label: str, detail: str = "") -> bool:
    mark = "✓" if ok else "✗"
    suffix = f"  — {detail}" if detail else ""
    print(f"  {mark} {label}{suffix}")
    return ok


def cmd_doctor(args) -> int:
    print("powerline-claude-code doctor\n")
    settings = _load_settings()
    hooks = settings.get("hooks", {})

    hooks_ok = all(
        any(_matcher_is_ours(m) for m in hooks.get(event, [])) for event in _HOOK_EVENTS
    )
    _check(hooks_ok, "hooks installed", "" if hooks_ok else "run: powerline-claude-code setup")

    sl = settings.get("statusLine")
    sl_ok = isinstance(sl, dict) and _has_marker(sl.get("command", ""))
    chain = _load_config().get("chain")
    _check(
        sl_ok,
        "statusLine capture installed",
        f"chains: {chain}" if (sl_ok and chain) else ("" if sl_ok else "run: powerline-claude-code setup"),
    )

    for d in (S.ATTENTION_DIR, S.CACHE_DIR):
        _check(os.access(d, os.W_OK) if d.exists() else True, f"writable: {d}")

    cache = S._load_json(S.RATE_LIMITS_CACHE)
    pct = (cache.get("five_hour") or {}).get("used_percentage")
    if pct is None:
        _check(False, "quota captured", "no rate_limits yet — run a Claude session (Pro/Max)")
    else:
        age = _fmt_age(time.time() - cache.get("ts", 0))
        _check(True, "quota captured", f"5h {pct:.0f}% (updated {age} ago)")

    tmux_ok = _tmux_available()
    _check(tmux_ok, "tmux reachable", "" if tmux_ok else "attention needs a tmux session")
    return 0


def _fmt_age(seconds: float) -> str:
    return S._fmt_duration(seconds) if seconds >= 60 else "<1m"


def _tmux_available() -> bool:
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, timeout=2, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="powerline-claude-code")
    sub = parser.add_subparsers(dest="cmd", required=True)

    setup = sub.add_parser("setup", help="install hooks + statusLine capture")
    setup.add_argument("--uninstall", action="store_true", help="remove them again")
    setup.set_defaults(func=cmd_setup)

    sub.add_parser("doctor", help="report what's wired up").set_defaults(func=cmd_doctor)
    sub.add_parser("statusline", help="(internal) capture rate_limits from stdin").set_defaults(
        func=cmd_statusline
    )

    hook = sub.add_parser("hook", help="(internal) attention flag writer")
    hook.add_argument("event", choices=["notification", "stop", "clear"])
    hook.set_defaults(func=cmd_hook)

    args = parser.parse_args(argv)
    return args.func(args) or 0
