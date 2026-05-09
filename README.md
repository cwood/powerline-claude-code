# powerline-claude-code

A [Powerline](https://powerline.readthedocs.io) segment that highlights tmux
windows where [Claude Code](https://claude.com/claude-code) is waiting for you
— either because it finished a turn or because it's blocked on a permission
prompt.

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

## Highlight groups

The segment emits these groups (define them in your colorscheme):

- `claude_code:attention`
- `warning` (fallback)
- `background` (fallback)
- `claude_code:divider`

## Uninstall

Remove the three hook entries from `~/.claude/settings.json` (search for the
`powerline-claude-code:` marker), then `pipx uninject powerline-status
powerline-claude-code`.
