#!/usr/bin/env bash
# Re-extract the pnpm-version guard verbatim from the shipped YAML. Run before
# every suite run; a stale copy passing is worse than no test.
#
# The block is duplicated into several workflows, so the extractor reads them
# all and refuses unless they are byte-identical. Testing only one copy would
# leave the others free to drift into the exact "first run passes, a later one
# dies" shape this guard exists to prevent.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
D="$(cd "$(dirname "$0")" && pwd)"
python3 - "$D/guard.sh" <<'PY'
import glob, io, re, sys

BEGIN = "          # pnpm-guard-slice-begin"
END = "          # pnpm-guard-slice-end"
# Same pattern check-workflows.py anchors on: a `uses:` line, never prose that
# happens to name the action.
CALLS_ACTION = re.compile(r"^\s*(?:-\s*)?uses:\s*pnpm/action-setup@", re.MULTILINE)

# Derive the file set, never hand-maintain it. A list written here would let
# someone add a fourth workflow by copying the pnpm block, set its
# PNPM_BOOTSTRAP_VERSION literal correctly so check-workflows.py passes, invert
# the `-z` in the copied guard, and ship it: this extractor would never read
# the file, so neither the byte-identical comparison nor run.sh's branch
# assertions would touch it, and both CI steps would be green over a broken
# copy. Deriving it means a new copy is either in lockstep or red.
sources = sorted(glob.glob(".github/workflows/*.yml"))
body = {p: io.open(p, encoding="utf-8").read() for p in sources}

marked = {p for p in sources if BEGIN in body[p]}
callers = {p for p in sources if CALLS_ACTION.search(body[p])}

# The two sets must agree in BOTH directions, for different reasons. A caller
# with no slice is a guard nobody tests. A slice in a workflow that does not
# call the action is a guard that cannot fire -- dead code wearing the look of
# protection, which is worse than none.
if marked != callers:
    untested = sorted(callers - marked)
    orphan = sorted(marked - callers)
    msg = []
    if untested:
        msg.append(
            "these call pnpm/action-setup but carry no pnpm-guard-slice markers, "
            "so their guard is never extracted or tested: " + ", ".join(untested)
        )
    if orphan:
        msg.append(
            "these carry pnpm-guard-slice markers but never call "
            "pnpm/action-setup, so the guard there can never fire: "
            + ", ".join(orphan)
        )
    sys.exit("; ".join(msg))

if not marked:
    sys.exit("no workflow carries pnpm-guard-slice markers; refusing to test nothing")

slices = {}
for path in sorted(marked):
    s = body[path]
    a = s.index(BEGIN)
    # Skip the marker's own comment paragraph, not just its first line: every
    # line up to the first non-comment is prose about the markers.
    a = s.index("\n", a) + 1
    while s[a:].lstrip(" ").startswith("#"):
        a = s.index("\n", a) + 1
    try:
        b = s.index(END, a)
    except ValueError:
        sys.exit(f"pnpm-guard-slice-end missing from {path}; refusing to guess the slice")
    lines = [l[10:] if l.startswith(" " * 10) else l for l in s[a:b].splitlines()]
    slices[path] = "\n".join(lines).rstrip() + "\n"

first = sorted(slices)[0]
for path in sorted(slices):
    if slices[path] != slices[first]:
        sys.exit(
            f"the pnpm guard in {path} is not byte-identical to the one in {first}. "
            "All copies must stay in lockstep -- the suite tests one on behalf of all."
        )

print(f"pnpm-guard: {len(slices)} identical copies in " + ", ".join(sorted(slices)))
io.open(sys.argv[1], "w", encoding="utf-8").write("set -euo pipefail\n" + slices[first])
PY
