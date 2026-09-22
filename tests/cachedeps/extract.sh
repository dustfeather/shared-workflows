#!/usr/bin/env bash
# Slices the cache-glob block out of a workflow, verbatim, and prints it as a
# runnable script. Re-extracting on every run is the point: the suite tests the
# bytes that ship, and cannot drift from an edit.
#
# Refuses rather than guesses when the markers are absent. Anchoring on a line
# of the block's own logic is what the bump-token guard did, and it made that
# line the one thing the suite could not change: mutating it deleted the anchor
# and the slice silently matched something else entirely.
set -euo pipefail

file=${1:?usage: extract.sh <workflow.yml>}

python3 - "$file" <<'PY'
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

body = lines[begin[0] + 1 : end[0]]
indent = min((len(l) - len(l.lstrip()) for l in body if l.strip()), default=0)
print("\n".join(l[indent:] if l.strip() else "" for l in body))
PY
