#!/usr/bin/env bash
# Blocks committing competitor-sensitive content to the PUBLIC Kompany repo.
# High-signal denylist (low false-positive). Override a false hit with:
#   ALLOW_SENSITIVE=1 git commit ...
# Routing for real sensitive docs: put them in the private kompany-docs repo.
set -u
[ "${ALLOW_SENSITIVE:-0}" = "1" ] && exit 0

# Only scan staged, added lines of text files.
staged=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$staged" ] && exit 0

# Pattern => why
patterns='Y Combinator|\bYC\b|pre-YC|Swedexpress|SveaPrep|Founder License|299[[:space:]]*CNY|[A-Za-z0-9._%+-]+@(gmail|hotmail|qq|163)\.com|kompany-docs|(^|[^.[:alnum:]])\.trellis(/|[^a-z]|$)|(^|[^.[:alnum:]])\.claude(/|[^a-z-]|$)'

hits=""
for f in $staged; do
  case "$f" in
    scripts/check-sensitive.sh|.githooks/pre-commit) continue ;;  # guard defines the patterns itself
    *.md|*.py|*.txt|*.json|*.yaml|*.yml|*.toml|*.rs|*.sh|*.html|*.js) ;;
    *) continue ;;
  esac
  # only newly-added lines
  added=$(git diff --cached -U0 -- "$f" | grep '^+' | grep -v '^+++')
  m=$(printf '%s\n' "$added" | grep -nEi "$patterns")
  [ -n "$m" ] && hits="$hits\n--- $f\n$m"
done

if [ -n "$hits" ]; then
  echo "BLOCKED: staged change adds competitor-sensitive content to the PUBLIC repo." >&2
  echo -e "$hits" >&2
  echo "" >&2
  echo "If this belongs in the public repo (false positive), re-run with ALLOW_SENSITIVE=1." >&2
  echo "If it's real strategy/pricing/roadmap/PII, move it to the private kompany-docs repo." >&2
  exit 1
fi
exit 0
