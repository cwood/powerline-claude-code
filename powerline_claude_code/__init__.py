"""Powerline segments for Claude Code: subscription quota + tmux attention.

Segment functions are re-exported here so powerline themes can reference them
as ``powerline_claude_code.status`` (and ``.quota`` / ``.attention`` / ``.usage``).
"""

from .segments import attention, quota, status, usage

__all__ = ["status", "quota", "attention", "usage"]
