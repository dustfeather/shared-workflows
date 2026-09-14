#!/usr/bin/env bash
# The stub harness tests the guard's SHELL. This tests the guard's jq program,
# which the stub cannot see at all: it lives in the argv the stub only matches
# against. Extract the program verbatim from the YAML and run real jq over a
# fixture holding the cases that actually break it.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
prog=$(python3 - <<'PY'
import io, re
s = io.open('.github/workflows/merge-on-approval.yml', encoding='utf-8').read()
m = re.search(r"--jq '(\.\[\]\.commit\.message.*?)'\)", s, re.S)
print(m.group(1))
PY
)
fixture='[
  {"commit":{"message":"feat: ordinary subject\n\nbody line"}},
  {"commit":{"message":"fix: crlf subject #minor\r\nbody"}},
  {"commit":{"message":"docs: only a subject"}},
  {"commit":{"message":""}}
]'
out=$(printf '%s' "$fixture" | jq -r "$prog")
n=$(printf '%s\n' "$out" | grep -c '')
fail=0
chk() { if [ "$2" = "$3" ]; then printf 'PASS  %-46s %s\n' "$1" "$2"; else printf 'FAIL  %-46s got=%s want=%s\n' "$1" "$2" "$3"; fail=1; fi; }
chk "one line per commit, empty one included" "$n" 4
chk "no empty line survives" "$(printf '%s\n' "$out" | grep -c '^$')" 0
chk "the empty subject became a placeholder" "$(printf '%s\n' "$out" | tail -1)" "(empty subject)"
chk "CR stripped, token intact" "$(printf '%s\n' "$out" | sed -n 2p)" "fix: crlf subject #minor"
chk "body excluded from the subject" "$(printf '%s\n' "$out" | sed -n 1p)" "feat: ordinary subject"
# Two controls, because the empty message breaks the naive program in two
# different ways and only one of them is the reported one.
#   1. `"" | split("\n")` is [], so [0] is null and sub() on null is a hard jq
#      error -- the step dies under set -e, it does not miscount.
naive_rc=0
printf '%s' "$fixture" | jq -r '.[].commit.message | split("\n")[0] | sub("\r$";"")' >/dev/null 2>&1 || naive_rc=$?
chk "CONTROL: no // \"\" -> jq errors on the empty one" "$naive_rc" 5
#   2. with the null fixed but no placeholder, the trailing empty line is eaten
#      by command substitution and the count comes up one short.
half=$(printf '%s' "$fixture" | jq -r '.[].commit.message | split("\n")[0] // "" | sub("\r$";"")')
chk "CONTROL: no placeholder -> count drops to 3" "$(printf '%s\n' "$half" | grep -c '')" 3
echo "---- jq program: $([ "$fail" = 0 ] && echo OK || echo BROKEN) ----"
[ "$fail" = 0 ]
