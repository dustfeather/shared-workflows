#!/usr/bin/env bash
D="$(cd "$(dirname "$0")" && pwd)"
export PATH="$D/bin:$PATH"
"$D/extract.sh" || { echo "extract.sh failed; refusing to run against a stale guard.sh"; exit 2; }
export REPO=o/r PR_NUMBER=42
pass=0; fail=0
t() { # name expected_exit mode method title subjects [expected_substring]
  local name=$1 want=$2 want_str=${7:-}
  export MODE=$3 method=$4 FIX_TITLE=$5 FIX_SUBJECTS=$6
  export FIX_NCOMMITS=${8:-}
  NC_SEQ=$(mktemp); export NC_SEQ
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
  "::warning::Bump-token guard: this merge will cut a MAJOR release (major token found in the PR title). The subject was captured at the top of this step"
t "auto: re-run is named as the remedy"            0 auto   --squash  "fix: y"         "fix: y" \
  "re-run this workflow, which needs a fresh approving review"
t "direct: plain line, title still editable"       0 direct --squash  "feat: y #minor" "feat: y" \
  "Bump-token guard: this merge will cut a MINOR release (minor token found in the PR title). The subject was captured at the top of this step, so editing the PR title from now on does not change this release"
t "direct: no ::warning:: on the direct path"      0 direct --squash  "fix: y"         "fix: y" \
  "!::warning::"
t "direct: does NOT claim the title is still live"  0 direct --squash  "fix: y"         "fix: y" \
  "!still changes what lands"
t "CONTROL: refusal text names the title edit"     1 auto   --squash  "feat: y"        "feat: y #major" \
  "Fix: edit the PR title to contain #major, then re-run."
# The commit list is read from REST now: truncation of GraphQL messageHeadline
# used to eat an appended token, and the endpoint caps at 250 commits.
big249=$(for i in $(seq 1 248); do echo "c$i: filler"; done; echo "c249: last #minor")
big250=$(printf '%s\nc250: over the cap\n' "$big249")
t "249 commits: token on the last one still seen"  1 auto   --squash  "feat: y"        "$big249"
# The cap is detected by comparing against the PR's own declared count, not by
# testing the count against 250 -- `--paginate` stops on a page boundary, so a
# `-ge 250` test never fires at the default per_page=30.
t "CONTROL: short read vs declared count refuses"  1 auto   --squash  "feat: y #major" "$big250" \
  "::error::PR #42 declares 260 commits but only 250 subjects could be read" 260
t "CONTROL: one commit short still refuses"        1 auto   --squash  "fix: y"         "$big249" \
  "declares 250 commits but only 249 subjects could be read" 250
t "a long subject is NOT truncated by this guard"  1 auto   --squash  "feat: y" \
  "fix(merge-on-approval): a subject long enough that messageHeadline would clip it #minor"
t "log prints the landing subject verbatim"        0 auto   --squash  "feat: y #minor" "feat: y" \
  "would land the subject: feat: y #minor (#42)"
# declared_commits is a SECOND api call, so the head can move between the two
# reads. The re-read must name that as the cause instead of blaming the cap.
t "CONTROL: head moved mid-read names the head"    1 auto   --squash  "feat: y" "$(printf 'a: one\nb: two')" \
  "::error::The head of #42 moved while this guard was reading it: the commit count went from 3 to 4" "3 4"
# A stable count is NOT proof of the cap: a push landing between the commit list
# and the declared-count read leaves both later reads equal and pre-push subjects
# short, which looks identical to truncation. The declared count is what tells
# them apart, because it is uncapped -- at or below 250 the listing could have
# returned everything, so truncation cannot be the cause and the message must
# not claim it. Both arms still exit 1; only the wording differs.
t "a stable count below the cap rules truncation out" 1 auto --squash  "feat: y" "$(printf 'a: one\nb: two')" \
  "3 is within the 250-commit cap on that list, so truncation does not explain this" "3 3"
t "CONTROL: below-cap shortfall does not assert the cap" 1 auto --squash "feat: y" "$(printf 'a: one\nb: two')" \
  "!was most likely truncated" "3 3"
t "a stable count past the cap names truncation"   1 auto   --squash  "feat: y" "$(printf 'a: one\nb: two')" \
  "781 is past the 250-commit cap on the pull-request commit list, so the read was most likely truncated there" "781 781"
# The subject line must not state the landing subject as settled fact: a base
# branch behind a merge queue discards it, and this guard cannot detect that.
t "subject line is hedged, not asserted"           0 auto   --squash  "fix: y"  "fix: y" \
  "!will land this subject"
t "the merge-queue caveat is on the subject line"  0 auto   --squash  "fix: y"  "fix: y" \
  "a base branch behind a merge queue discards it"
echo "---- $pass passed, $fail failed ----"
[ "$fail" = 0 ]
