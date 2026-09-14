#!/usr/bin/env bash
# Re-pin shared-workflows callers from @v5 to @v6.
#
# DRY RUN BY DEFAULT. Pass --apply to commit and push.
#
# Do not run this until the v6 tag EXISTS. Every edit writes @v6 into a `uses:`
# ref; until tag-release.yml has cut it, each edited caller fails its next run
# with an unresolvable reference.
#
# Repo list is the byte-level inventory taken 2026-09-14 (20 active repos).
# Deliberately absent:
#   - the three archived @v4 repos (fear-greed-telegram-bot,
#     alpaca-opus-trading, degoog-infra) — frozen at v4.14.2, stale regardless
#   - shared-workflows' own pr-merge.yml and pr-checks.yml, which each need
#     their own direct push for reasons recorded in that repo's CLAUDE.md
set -euo pipefail

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

SED_SCRIPT="$(cd "$(dirname "$0")" && pwd)/repin.sed"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

REPOS="
dustfeather/social-update
dustfeather/dosar-rapid.ro
dustfeather/vaultwarden
dustfeather/uninsta
dustfeather/series-auto-skip
dustfeather/filelist-seed-purge
dustfeather/filelist-ext
dustfeather/chrome-group-discard
dustfeather/dustfeather
dustfeather/discord-purge
dustfeather/device-activity-telegram-bot
dustfeather/gw2roi
ITGuys-RO/fleet-manager
ITGuys-RO/k3s-cluster
ITGuys-RO/itguys.ro
ITGuys-RO/invest
ITGuys-RO/nextcloud
ITGuys-RO/bentopdf
ITGuys-RO/apps-page
"

total_files=0
for repo in $REPOS; do
  dir="$WORK/${repo##*/}"
  git clone -q --depth 1 "git@github.com:$repo.git" "$dir" || { echo "CLONE FAILED $repo"; continue; }
  [ -d "$dir/.github/workflows" ] || { echo "  $repo: no workflows dir"; continue; }

  find "$dir/.github/workflows" -name '*.yml' -exec sed -i -f "$SED_SCRIPT" {} +

  changed=$(cd "$dir" && git diff --name-only)
  [ -n "$changed" ] || { echo "  $repo: nothing to change"; continue; }

  n=$(printf '%s\n' "$changed" | wc -l)
  total_files=$((total_files + n))
  echo "$repo ($n file(s)):"
  # shellcheck disable=SC2001  # per-line prefix; ${var//} cannot anchor per line
  sed 's/^/    /' <<<"$changed"

  # A third-party action left on its own @v5 is expected and fine; a
  # shared-workflows self-ref still on @v5 means the pattern missed one.
  if (cd "$dir" && grep -rn "shared-workflows/\.github/workflows/.*@v5\([[:space:]]\|$\)" .github/workflows/); then
    echo "  !! $repo still has an unconverted self-ref above — NOT pushing this repo"
    continue
  fi

  if [ "$APPLY" = 1 ]; then
    (cd "$dir"
     git add -A .github/workflows
     git commit -q -m "ci: re-pin shared-workflows to @v6

v6 changes the squash commit subject: merge-on-approval.yml now passes
--subject explicitly, so the landing subject is the PR title rather than
whatever squash_merge_commit_title resolved to. Bump tokens (#minor/#major)
therefore belong in the PR title.

tag-release.yml re-points only the current major, so v5 is frozen from the
v6 cut onward and this repo would otherwise stop receiving changes."
     git push -q origin HEAD)
    echo "    pushed"
  fi
done

echo
if [ "$APPLY" = 1 ]; then
  echo "APPLIED across $total_files file(s)."
else
  echo "DRY RUN: $total_files file(s) would change. Re-run with --apply to push."
fi
