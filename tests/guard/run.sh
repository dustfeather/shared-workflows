#!/usr/bin/env bash
D="$(cd "$(dirname "$0")" && pwd)"
export PATH="$D/bin:$PATH"
"$D/extract.sh" || { echo "extract.sh failed; refusing to run against a stale guard.sh"; exit 2; }
export REPO=o/r PR_NUMBER=42
# The slice now runs through `gh pr merge`, so the inputs that call reads have
# to exist. Defaults match the reusable workflow's own defaults.
export PR_URL=https://github.com/o/r/pull/42 DELETE_BRANCH=false
pass=0; fail=0
t() { # name expected_exit mode method title subjects [expected_substring]
  local name=$1 want=$2 want_str=${7:-}
  export MODE=$3 method=$4 FIX_TITLE=$5 FIX_SUBJECTS=$6
  export FIX_NCOMMITS=${8:-}
  NC_SEQ=$(mktemp); export NC_SEQ
  MERGE_ARGV=$(mktemp); export MERGE_ARGV
  out=$(bash "$D/guard.sh" 2>&1); rc=$?
  # Assertions may target the recorded merge argv instead of the guard's own
  # output, by prefixing the expected substring with "argv:". The guard's echo
  # survives deleting the flag; the argv does not. The recording is
  # pipe-delimited, so write the expected form with its own pipes and a lost
  # argument boundary turns the case red.
  case "$want_str" in "argv:"*|"!argv:"*)
    merge_argv=$(cat "$MERGE_ARGV" 2>/dev/null || true)
    out="merge-argv: ${merge_argv}"
    want_str=${want_str/argv:/} ;;
  esac
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
  "::error::PR #42 declares 260 commits and 250 subjects were read" 260
t "CONTROL: one commit short still refuses"        1 auto   --squash  "fix: y"         "$big249" \
  "declares 250 commits and 249 subjects were read" 250
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
# `squash_subject` strips an existing " (#N)" before appending its own, because
# a title copied off a `git log --oneline` line already carries one. No fixture
# above has the suffix, so the strip was unbound: delete the `%` expansion and
# every case stayed green while the subject landed as "... (#42) (#42)". The
# positive assertion is a substring match and passes against the doubled text
# too, so the negative control is the half that actually binds it.
t "an existing (#N) suffix is not doubled"         0 auto   --squash  "feat: y #minor (#42)" "feat: y" \
  "argv:|--subject|feat: y #minor (#42)|"
t "CONTROL: the suffix is not appended twice"      0 auto   --squash  "feat: y #minor (#42)" "feat: y" \
  "!argv:(#42) (#42)"

# The mismatch is not always a SHORTFALL. A force-push or a base rebase between
# the two reads drops commits, leaving more subjects in hand than the PR now
# declares -- and the recheck only fires if the count moves a second time. The
# old wording said "declares 1 but only 2 could be read" and then hung a cap
# diagnosis on it, which is doubly wrong: nothing was truncated and nothing was
# short.
t "more subjects than declared names the shrink"   1 auto   --squash  "feat: y" "$(printf 'a: one\nb: two')" \
  "that is MORE subjects than the PR declares, so the head shrank between the two reads" "1 1"
t "CONTROL: an over-read does not blame the cap"   1 auto   --squash  "feat: y" "$(printf 'a: one\nb: two')" \
  "!cap on that list" "1 1"

# The guard's PROMISE is the flag on the merge call, not the echo above it.
# These assert the recorded argv: delete `args+=(--subject "$squash_subject")`
# and every echo-based case stays green while these four go red.
t "squash: --subject reaches the merge argv"       0 auto   --squash  "feat: y #minor" "feat: y" \
  "argv:|--subject|feat: y #minor (#42)|"
t "squash: the subject rides on the auto path too" 0 auto   --squash  "fix: y"        "fix: y" \
  "argv:|pr|merge|--auto|--squash|--subject|fix: y (#42)|"
t "direct mode passes the same subject"            0 direct --squash  "fix: y"        "fix: y" \
  "argv:|--squash|--subject|fix: y (#42)|"
# The flag belongs to squash alone: a rebase merge has no subject to set, and
# passing one would be a hard error from the real CLI.
t "CONTROL: rebase carries no --subject"           0 auto   --rebase  "feat: y #minor" "feat: y" \
  "!argv:|--subject|"

# The subject line must not state the landing subject as settled fact: a base
# branch behind a merge queue discards it, and this guard cannot detect that.
t "subject line is hedged, not asserted"           0 auto   --squash  "fix: y"  "fix: y" \
  "!will land this subject"
t "the merge-queue caveat is on the subject line"  0 auto   --squash  "fix: y"  "fix: y" \
  "a base branch behind a merge queue discards it"
echo "---- $pass passed, $fail failed ----"
[ "$fail" = 0 ]
