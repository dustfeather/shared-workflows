#!/usr/bin/env bash
# Re-extract the guard's shell verbatim from the shipped YAML. Run before every
# suite run; a stale copy passing is worse than no test.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
D="$(cd "$(dirname "$0")" && pwd)"
python3 - "$D/guard.sh" <<'PY'
import io, sys
s = io.open('.github/workflows/merge-on-approval.yml', encoding='utf-8').read()
# Anchor on dedicated markers, never on a line of the code under test. The
# `--squash` condition used to be the start anchor, which quietly exempted it
# from mutation testing: rewriting it removed the anchor, `index` found the
# NEXT match far below, and the suite ran against a meaningless slice while
# reporting ordinary-looking failures.
try:
    a = s.index('          # guard-slice-begin\n') + len('          # guard-slice-begin\n')
    b = s.index('          # guard-slice-end')
except ValueError:
    sys.exit('guard-slice markers missing from merge-on-approval.yml; refusing to guess the slice')
out = [l[10:] if l.startswith(' ' * 10) else l for l in s[a:b].splitlines()]
io.open(sys.argv[1], 'w', encoding='utf-8').write("set -euo pipefail\n" + "\n".join(out) + "\n")
PY
