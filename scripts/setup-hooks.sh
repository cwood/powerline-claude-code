#!/usr/bin/env bash
# Idempotently install the Claude Code hooks needed by powerline-claude-code.
#
# Hooks installed (all guarded so they no-op outside tmux):
#   Notification         → touch ~/.claude/attention/$TMUX_PANE
#   Stop                 → touch ~/.claude/attention/$TMUX_PANE
#   UserPromptSubmit     → rm  -f ~/.claude/attention/$TMUX_PANE
#
# Re-running is safe: existing matching hooks are replaced, others kept.

set -euo pipefail

SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
ATTENTION_DIR="$HOME/.claude/attention"

if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is required (brew install jq)" >&2
    exit 1
fi

mkdir -p "$ATTENTION_DIR"
mkdir -p "$(dirname "$SETTINGS")"
[[ -f "$SETTINGS" ]] || echo '{}' >"$SETTINGS"

# Single shell command shared across hooks. Guarded on $TMUX_PANE so non-tmux
# Claude sessions are unaffected. The leading `: 'powerline-claude-code';` is
# a no-op that embeds an idempotency marker we can match on.
MARKER="powerline-claude-code"
TOUCH_CMD=": '$MARKER'; [ -n \"\$TMUX_PANE\" ] && mkdir -p \"\$HOME/.claude/attention\" && touch \"\$HOME/.claude/attention/\$TMUX_PANE\" || true"
CLEAR_CMD=": '$MARKER'; [ -n \"\$TMUX_PANE\" ] && rm -f \"\$HOME/.claude/attention/\$TMUX_PANE\" || true"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

# jq filter: ensure .hooks.<event> exists and contains exactly one matcher
# whose command is our marker. Other matchers are preserved so user-managed
# hooks aren't clobbered.
jq \
    --arg touch "$TOUCH_CMD" \
    --arg clear "$CLEAR_CMD" \
    --arg marker "$MARKER" '
    def upsert(event; cmd):
        .hooks[event] = (
            ((.hooks[event] // []) | map(select(
                (.hooks // []) | all(.command // "" | contains($marker) | not)
            )))
            + [{
                matcher: "*",
                hooks: [{ type: "command", command: cmd }]
            }]
        );
    .
    | .hooks //= {}
    | upsert("Notification"; $touch)
    | upsert("Stop"; $touch)
    | upsert("UserPromptSubmit"; $clear)
' "$SETTINGS" >"$tmp"

mv "$tmp" "$SETTINGS"

echo "Installed powerline-claude-code hooks in $SETTINGS"
echo "Attention flag dir: $ATTENTION_DIR"
echo
echo "Add this segment to your powerline tmux theme:"
echo '  { "function": "powerline_claude_code.attention", "priority": 30 }'
