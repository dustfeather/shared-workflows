#!/usr/bin/env bash
D="$(cd "$(dirname "$0")" && pwd)"
export PATH="$D/bin:$PATH"
export REPO=o/r PR_NUMBER=42
pass=0; fail=0
t() { # name expected_exit mode method title subjects [expected_substring]
  local name=$1 want=$2 want_str=${7:-}
  export MODE=$3 method=$4 FIX_TITLE=$5 FIX_SUBJECTS=$6
  out=$(bash "$D/guard.sh" 2>&1); rc=$?
  if [ "$rc" != "$want" ]; then
    printf 'FAIL  %-52s exit=%s want=%s\n%s\n' "$name" "$rc" "$want" "$out"; fail=$((fail+1)); return
  fi
  # A leading "!" inverts the assertion: the substring must be ABSENT.
  if [ -n "$want_str" ]; then
    local neg=0
    case "$want_str" in "!"*) neg=1; want_str=${want_str#!} ;; esac
    local found=1
    [ "${out#*"$want_str"}" = "$out" ] && found=0
    if [ "$found" != "$((1 - neg))" ]; then
      printf 'FAIL  %-52s exit=%s assertion (neg=%s) on: %s\n%s\n' "$name" "$rc" "$neg" "$want_str" "$out"; fail=$((fail+1)); return
    fi
  fi
  printf 'PASS  %-52s exit=%s\n' "$name" "$rc"; pass=$((pass+1))
}
t "rebase: guard skipped entirely"                 0 auto   --rebase  "feat: x #minor" "feat: x #minor"
t "squash: no tokens anywhere"                     0 auto   --squash  "fix: y"         "fix: y"
t "squash: #minor in PR title only"                0 auto   --squash  "feat: y #minor" "feat: y"
t "squash: #minor in title AND subject"            0 auto   --squash  "feat: y #minor" "feat: y #minor"
t "squash: #major title subsumes #minor subject"   0 auto   --squash  "feat: y #major" "feat: y #minor"
t "CONTROL: #minor stranded on subject"            1 auto   --squash  "feat: y"        "feat: y #minor"
t "CONTROL: #major stranded on subject"            1 auto   --squash  "feat: y"        "feat: y #major"
t "CONTROL: #minor title, #major on subject"       1 auto   --squash  "feat: y #minor" "feat: y #major"
t "CONTROL: multi-commit, token on 2nd subject"    1 auto   --squash  "feat: y"        "$(printf 'a: one\nb: two #minor')"
t "multi-commit, title carries it"                 0 auto   --squash  "feat: y #minor" "$(printf 'a: one\nb: two')"
t "suffix carries no token of its own"             1 auto   --squash  "feat: y"        "feat: y #minor"
# The consequence report is mode-dependent: on `auto` GitHub freezes the subject
# when auto-merge is enabled, so "edit the title" is false advice after this run.
t "auto: report warns the title freezes here"      0 auto   --squash  "feat: y #major" "feat: y" \
  "::warning::Bump-token guard: this merge will cut a MAJOR release (major token found in the PR title). Mode is 'auto'"
t "auto: re-run is named as the remedy"            0 auto   --squash  "fix: y"         "fix: y" \
  "re-run this workflow, which needs a fresh approving review"
t "direct: plain line, title still editable"       0 direct --squash  "feat: y #minor" "feat: y" \
  "Bump-token guard: this merge will cut a MINOR release (minor token found in the PR title). The merge runs later in this same job"
t "direct: no ::warning:: on the direct path"      0 direct --squash  "fix: y"         "fix: y" \
  "!::warning::"
t "CONTROL: refusal text names the title edit"     1 auto   --squash  "feat: y"        "feat: y #major" \
  "Fix: edit the PR title to contain #major, then re-run."
# The commit list is read from REST now: truncation of GraphQL messageHeadline
# used to eat an appended token, and the endpoint caps at 250 commits.
big249=$(for i in $(seq 1 248); do echo "c$i: filler"; done; echo "c249: last #minor")
big250=$(printf '%s\nc250: over the cap\n' "$big249")
t "249 commits: token on the last one still seen"  1 auto   --squash  "feat: y"        "$big249"
t "CONTROL: 250-commit cap refuses, cannot verify" 1 auto   --squash  "feat: y #major" "$big250" \
  "::error::PR #42 reports 250 commits and the pull-request commit list caps at 250"
t "a long subject is NOT truncated by this guard"  1 auto   --squash  "feat: y" \
  "fix(merge-on-approval): a subject long enough that messageHeadline would clip it #minor"
t "log prints the landing subject verbatim"        0 auto   --squash  "feat: y #minor" "feat: y" \
  "will land this subject: feat: y #minor (#42)"
echo "---- $pass passed, $fail failed ----"
[ "$fail" = 0 ]
