# powerline-claude-code

[Powerline](https://powerline.readthedocs.io) segments for
[Claude Code](https://claude.com/claude-code):

- `attention` — highlights tmux windows where Claude Code is waiting for you
  (finished a turn, blocked on a permission prompt, etc.)
- `usage` — rolling token usage over configurable windows (default 5m / 30m /
  1h), with automatic spike highlight when the short window exceeds the long
  window baseline by 2× (configurable)

## How it works

```
Claude Code  ──Notification/Stop hook──▶  ~/.claude/attention/$TMUX_PANE
            ◀─UserPromptSubmit hook────  (file removed)

powerline    ──reads dir + tmux list-panes──▶  "⚠ work:2,side:5"
```

The hooks write a flag file keyed by tmux pane id; the segment maps those
ids back to `session:window` labels via `tmux list-panes -a`, drops stale
entries, and (by default) hides the current window from the list — you don't
need to be reminded about the pane you're already looking at.

## Install

```bash
pipx inject powerline-status \
    git+https://github.com/cwood/powerline-claude-code@master \
    --force --pip-args='--upgrade'
```

Then run the setup script once to install the hooks:

```bash
~/.local/pipx/venvs/powerline-status/lib/python*/site-packages/scripts/setup-hooks.sh
# or, if you have the repo cloned:
./scripts/setup-hooks.sh
```

The script is idempotent — re-running replaces this plugin's hooks but
leaves any other Claude Code hooks alone.

## Configure

Add to your powerline tmux theme JSON:

```json
{
    "function": "powerline_claude_code.attention",
    "priority": 30
}
```

Optional segment args:

| Arg         | Default            | Meaning |
| ----------- | ------------------ | ------- |
| `glyph`     | `⚠`                | Icon prefix |
| `show_self` | `false`            | Include the current pane |
| `format`    | `{glyph} {windows}`| Format string |
| `separator` | `,`                | Joiner between window labels |

Example with a different glyph and only window indices (drop the session prefix
by post-processing if you prefer — open an issue if you want a built-in flag):

```json
{
    "function": "powerline_claude_code.attention",
    "args": { "glyph": "●", "show_self": true }
}
```

## Usage segment

```json
{ "function": "powerline_claude_code.usage", "priority": 25 }
```

| Arg          | Default        | Meaning |
| ------------ | -------------- | ------- |
| `windows`    | `[5, 30, 60]`  | Minute windows to display. First = short (spike numerator), last = long (spike denominator). |
| `spike_ratio`| `2.0`          | Trigger spike highlight when short rate / long rate > this. |
| `glyph`      | `Σ`            | Prefix icon. |
| `show_zero`  | `false`        | Render with all-zero windows when no usage detected. |

Reads `~/.claude/projects/*.jsonl` and sums `input_tokens + output_tokens +
cache_creation_input_tokens + cache_read_input_tokens` from `type:"assistant"`
records. Per-file results are mtime-cached at
`~/.cache/powerline-claude-code/usage.json` so repeated status refreshes are
cheap.

> **Note:** including `cache_read_input_tokens` makes the absolute numbers
> large during long-context sessions (cache reads are billed at ~10% but are
> the dominant token type by volume). The 5m/30m/1h *ratios* are still the
> useful signal for "am I burning more than usual?".

## Highlight groups

Define these in your tmux colorscheme:

- `claude_code:attention` — windows-need-attention indicator
- `claude_code:usage` — normal usage segment
- `claude_code:usage_spike` — usage segment in spike state
- `claude_code:divider`
- Fallbacks used: `warning`, `information:additional`, `background`

## Uninstall

Remove the three hook entries from `~/.claude/settings.json` (search for the
`powerline-claude-code:` marker), then `pipx uninject powerline-status
powerline-claude-code`.
