#!/usr/bin/env bash
# Re-extract the guard's shell verbatim from the shipped YAML. Run before every
# suite run; a stale copy passing is worse than no test.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
python3 - "$D/guard.sh" <<'PY'
import io, sys
s = io.open('.github/workflows/merge-on-approval.yml', encoding='utf-8').read()
a = s.index('          if [ "$method" = "--squash" ]; then')
b = s.index('          del=""')
out = [l[10:] if l.startswith(' ' * 10) else l for l in s[a:b].splitlines()]
io.open(sys.argv[1], 'w', encoding='utf-8').write("set -euo pipefail\n" + "\n".join(out) + "\n")
PY
