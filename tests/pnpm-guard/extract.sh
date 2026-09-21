#!/usr/bin/env bash
# Re-extract the pnpm-version guard verbatim from the shipped YAML. Run before
# every suite run; a stale copy passing is worse than no test.
#
# The block is duplicated into three workflows, so the extractor reads all
# three and refuses unless they are byte-identical. Testing only node-test.yml
# would leave the other two copies free to drift into the exact "first run
# passes" shape this guard exists to prevent.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
D="$(cd "$(dirname "$0")" && pwd)"
python3 - "$D/guard.sh" <<'PY'
import io, sys

FILES = [
    ".github/workflows/node-test.yml",
    ".github/workflows/deploy-cloudflare.yml",
    ".github/workflows/release-extension.yml",
]
BEGIN = "          # pnpm-guard-slice-begin"
END = "          # pnpm-guard-slice-end"

slices = {}
for path in FILES:
    s = io.open(path, encoding="utf-8").read()
    try:
        a = s.index(BEGIN)
        # Skip the marker's own comment paragraph, not just its first line:
        # every line up to the first non-comment is prose about the markers.
        a = s.index("\n", a) + 1
        while s[a:].lstrip(" ").startswith("#"):
            a = s.index("\n", a) + 1
        b = s.index(END)
    except ValueError:
        sys.exit(f"pnpm-guard-slice markers missing from {path}; refusing to guess the slice")
    body = [l[10:] if l.startswith(" " * 10) else l for l in s[a:b].splitlines()]
    slices[path] = "\n".join(body).rstrip() + "\n"

first = FILES[0]
for path in FILES[1:]:
    if slices[path] != slices[first]:
        sys.exit(
            f"the pnpm guard in {path} is not byte-identical to the one in {first}. "
            "All three must stay in lockstep -- the suite tests one copy on behalf of all."
        )

io.open(sys.argv[1], "w", encoding="utf-8").write("set -euo pipefail\n" + slices[first])
PY
