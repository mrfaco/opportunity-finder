#!/usr/bin/env bash
# Non-blocking PreToolUse hook for `git push`.
# Asks Claude (haiku) whether CLAUDE.md is stale relative to the commits
# being pushed; if so, emits a systemMessage so the operator sees it.
# Failures are silent — this hook must never block a push.

set -u

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')
case "$cmd" in
  *"git push"*) ;;
  *) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -f CLAUDE.md ] || exit 0

upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) || exit 0
commits=$(git log "$upstream..HEAD" --stat 2>/dev/null)
[ -z "$commits" ] && exit 0

claude_md=$(cat CLAUDE.md)

prompt=$(cat <<EOF
You are auditing whether CLAUDE.md is stale relative to commits about to be pushed.
CLAUDE.md is for guiding future AI agents — flag staleness only for changes that
would mislead such an agent (new commands, new modules, renamed boundaries, new
gotchas, removed features). Routine bugfixes, refactors, test additions, and doc
tweaks are NOT staleness.

CURRENT CLAUDE.md:
---
$claude_md
---

COMMITS BEING PUSHED (git log --stat):
---
$commits
---

Reply with exactly one line:
  STALE: <one short sentence on what to add/update>
or:
  FRESH
EOF
)

verdict=$(printf '%s' "$prompt" | timeout 30 claude -p --model haiku 2>/dev/null | tr -d '\r' | grep -E '^(STALE|FRESH)' | head -1)

case "$verdict" in
  STALE:*)
    msg="CLAUDE.md may be stale — ${verdict#STALE: }"
    jq -n --arg m "$msg" '{systemMessage: $m}'
    ;;
esac

exit 0
