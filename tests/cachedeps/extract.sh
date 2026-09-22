#!/usr/bin/env bash
# Slices the cache-glob block out of a workflow, verbatim, and prints it as a
# runnable script. Re-extracting on every run is the point: the suite tests the
# bytes that ship, and cannot drift from an edit.
#
# Refuses rather than guesses when the markers are absent. Anchoring on a line
# of the block's own logic is what the bump-token guard did, and it made that
# line the one thing the suite could not change: mutating it deleted the anchor
# and the slice silently matched something else entirely.
#
# With --env-keys it prints the step's declared `env:` keys instead of its
# body. Those keys live OUTSIDE the markers, so nothing in the slice records
# which variable it is entitled to read -- and the way a second copy is made is
# by pasting the first, which is exactly how a slice ends up reading a name its
# own workflow never exports. `${INSTALL:-.}` does not trip `set -u`, so that
# mistake is silent in production and invisible to a harness that exports both.
set -euo pipefail

mode="body"
if [ "${1:-}" = "--env-keys" ]; then
  mode="env"
  shift
fi

file=${1:?usage: extract.sh [--env-keys] <workflow.yml>}

MODE="$mode" python3 - "$file" <<'PY'
import os
import sys

path = sys.argv[1]
lines = open(path).read().split("\n")

begin = [i for i, l in enumerate(lines) if l.strip() == "# cachedeps-slice-begin"]
end = [i for i, l in enumerate(lines) if l.strip() == "# cachedeps-slice-end"]
if len(begin) != 1 or len(end) != 1 or end[0] < begin[0]:
    sys.exit(
        f"{path}: expected exactly one # cachedeps-slice-begin/-end pair, "
        f"found {len(begin)}/{len(end)}. Restore the markers -- this script "
        f"refuses to guess the region, because guessing is how a suite ends up "
        f"testing 290 lines of the wrong file and passing."
    )

if os.environ.get("MODE") == "env":
    # The step's own env: block, read from the shipped YAML rather than listed
    # here, for the same reason the file set is derived rather than listed.
    step = [i for i, l in enumerate(lines) if l.strip() == "id: cachedeps"]
    if len(step) != 1:
        sys.exit(f"{path}: expected exactly one `id: cachedeps` step, found {len(step)}")
    i = step[0]
    step_indent = len(lines[i]) - len(lines[i].lstrip())
    keys, in_env = [], None
    for l in lines[i + 1 :]:
        if not l.strip():
            continue
        ind = len(l) - len(l.lstrip())
        if ind < step_indent or (ind == step_indent and l.strip().startswith("- ")):
            break
        if ind == step_indent:
            in_env = l.strip() == "env:"
            continue
        if in_env and ind > step_indent and ":" in l:
            keys.append(l.strip().split(":", 1)[0])
    if not keys:
        sys.exit(f"{path}: the cachedeps step declares no env: keys; refusing to guess")
    print("\n".join(keys))
    raise SystemExit(0)

body = lines[begin[0] + 1 : end[0]]
indent = min((len(l) - len(l.lstrip()) for l in body if l.strip()), default=0)
print("\n".join(l[indent:] if l.strip() else "" for l in body))
PY
