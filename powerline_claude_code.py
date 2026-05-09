"""Powerline segment that surfaces tmux windows where Claude Code is waiting.

Coordination model:
    - Claude Code hooks (Stop, Notification) write flag files to
      ~/.claude/attention/<TMUX_PANE>.
    - UserPromptSubmit removes the flag.
    - This segment reads the flag dir, maps each pane id back to a window
      via `tmux list-panes`, and renders a comma-separated list of window
      indices that need attention.

Configure in your powerline tmux theme like:

    {
        "function": "powerline_claude_code.attention",
        "priority": 30
    }
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ATTENTION_DIR = Path.home() / ".claude" / "attention"


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
