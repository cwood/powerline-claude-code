# powerline-claude-code

[Powerline](https://powerline.readthedocs.io) segments for
[Claude Code](https://claude.com/claude-code), rendered in your **tmux status
bar** (not Claude's own status row):

- **`status`** — one mode-switching segment. Normally shows your subscription
  quota; when an agent needs you it takes over and **cycles** through the tmux
  windows that are waiting:

  ```
  quiet:    ◧ 5h 47% · 2h13m · ~1h to cap
  waiting:  ✓ 0:nvim (1/2)      ← green, "done, your move"
            ⚠ work:docs (2/2)   ← yellow, "needs a decision"
  ```

`status` is the recommended single slot. `quota`, `attention`, and `usage` are
also exported if you'd rather place them separately.

## Why a capture step

The real `/usage` numbers (`rate_limits`) are delivered **only** to a Claude
Code `statusLine` command — never to hooks, and nowhere on disk. So this plugin
installs a tiny statusLine that snapshots `rate_limits` to a cache the segment
reads. Attention is driven by hooks. `setup` wires up both:

```
Claude Code ─statusLine JSON─▶ powerline-claude-code statusline ─▶ ~/.cache/.../rate_limits.json ─┐
Claude Code ─Notification/Stop hook─▶ powerline-claude-code hook ─▶ ~/.claude/attention/$TMUX_PANE ┤
                                                                                                   ▼
                                                          powerline  status  segment  (tmux bar)
```

If you already have a statusLine (e.g. ccstatusline), setup **chains** it —
your existing line keeps rendering while we siphon the quota data.

## Install

```bash
pipx inject powerline-status \
    git+https://github.com/cwood/powerline-claude-code@master \
    --force --pip-args='--upgrade'

powerline-claude-code setup     # installs hooks + statusLine capture
powerline-claude-code doctor    # ✓/✗ on every moving part
```

`setup` is idempotent and preserves any other hooks/statusLine you have.
Quota shows up after your next Claude Code turn (Pro/Max only).

## Configure

Add one segment to your powerline tmux theme JSON:

```json
{ "function": "powerline_claude_code.status", "priority": 30 }
```

### `status` arguments

| Arg | Default | Meaning |
| --- | --- | --- |
| `cycle_seconds` | `4` | Seconds each waiting window shows before cycling. |
| `pulse` | `false` | Alternate `<group>`/`<group>_pulse` each second so it throbs (works where terminal blink doesn't). Pair with `set -g status-interval 1`. |
| `show_self` | `false` | Include the pane you're already in. |
| `show_count` | `true` | Append `(n/total)` when several windows wait. |
| `attached_only` | `true` | Only alert for windows in a session you're attached to; ignore detached tmux sessions. |
| `label_format` | `{session}:{window_name}` | `str.format` over `session`, `window` (index), `window_name`. |
| `done_glyph` / `permission_glyph` / `idle_glyph` | `✓` / `⚠` / `⏾` | Per-reason glyphs. |
| `quota_glyph` | `◧` | Quota prefix. |
| `show_week` | `false` | Append the 7-day window. |
| `show_reset` | `true` | Append time until the 5h window resets. |
| `show_eta` | `true` | Append burn-rate ETA to the cap (hidden when not rising). |
| `warn` / `crit` | `70` / `90` | 5h % thresholds for the warn/crit highlight groups. |
| `base` | `quota` | What to show when idle: `quota` or `usage`. |

## Highlight groups

Define these in your tmux colorscheme. Reason = color is the whole point —
background-color the chip so a waiting window is unmissable:

```json
"claude_code:attention_done":             { "fg": "black",  "bg": "green",        "attrs": ["bold"] },
"claude_code:attention_permission":       { "fg": "black",  "bg": "yellow",       "attrs": ["bold"] },
"claude_code:attention_permission_pulse": { "fg": "yellow", "bg": "darkestyellow","attrs": ["bold"] },
"claude_code:attention_idle":             { "fg": "black",  "bg": "darkyellow" },
"claude_code:quota":      { "fg": "gray10", "bg": "gray4" },
"claude_code:quota_warn": { "fg": "black",  "bg": "yellow" },
"claude_code:quota_crit": { "fg": "white",  "bg": "red", "attrs": ["bold"] },
"claude_code:divider":    { "fg": "gray6",  "bg": "gray4" }
```

Color names come from your powerline color palette. Want it to flash without
`pulse`? Add `"attrs": ["blink"]` to a group (ignored by alacritty/ghostty —
use `pulse` there). Each group falls back to a generic powerline group then
`background`, so undefined ones still render legibly.

`attention_idle` / `attention_done` / `attention_permission` map to the `Stop`,
`Notification(idle_prompt)`, and `Notification(permission_prompt)` hooks
respectively.

## Token volume (opt-in, for API billing)

`usage` reports raw token volume from transcripts — useful on API billing, not
subscriptions:

```json
{ "function": "powerline_claude_code.usage", "args": { "metric": "new" }, "priority": 25 }
```

| `metric` | Sums |
| --- | --- |
| `new` *(default)* | `input + output` — what you'd recognize from Claude Code's own counters |
| `context` | `+ cache_creation` |
| `billable` | all four components — large, dominated by `cache_read` (the same context re-read every turn) |
| `cost` | rough USD estimate from per-token weights |

Other args: `windows` (default `[5, 30, 60]` minutes), `spike_ratio` (`2.0`),
`glyph` (`Σ`), `show_zero` (`false`).

## Uninstall

```bash
powerline-claude-code setup --uninstall   # removes our hooks, restores any chained statusLine
pipx uninject powerline-status powerline-claude-code
```
