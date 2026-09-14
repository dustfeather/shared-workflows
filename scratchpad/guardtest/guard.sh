set -euo pipefail
if [ "$method" = "--squash" ]; then
  pr_title=$(gh pr view "$PR_NUMBER" --repo "$REPO" --json title --jq '.title')
  # Subjects come from REST, NOT `gh pr view --json commits`. That
  # field is GraphQL `messageHeadline`, which GitHub truncates around
  # 72 characters (69 plus an ellipsis), and the bump token is
  # conventionally APPENDED to the subject -- so truncation eats
  # precisely the substring this scan searches for. The guard would
  # then read "no bump token asked for", allow the merge, and the
  # release would degrade to a patch with nothing in the log to say
  # why. Three commits on this very branch are long enough to trip it.
  #
  # REST returns the raw message, so take its first line: bodies stay
  # excluded, which is the rule tag-release.yml itself scans by (prose
  # discussing a bump matched itself and cut a spurious minor).
  pr_subjects=$(gh api --paginate "repos/$REPO/pulls/$PR_NUMBER/commits" \
    --jq '.[].commit.message | split("\n")[0] | sub("\r$";"")')

  # That endpoint caps at 250 commits however far it is paginated. A
  # token on commit 251 would be invisible, which is the exact silent
  # failure this guard exists to prevent, so refuse rather than scan a
  # list known to be incomplete.
  n_commits=$(printf '%s' "$pr_subjects" | grep -c '' || true)
  if [ "$n_commits" -ge 250 ]; then
    echo "::error::PR #${PR_NUMBER} reports ${n_commits} commits and the pull-request commit list caps at 250, so a bump token on a later commit cannot be seen from here. Check the subjects by hand and merge manually, or split the PR."
    exit 1
  fi

  # `--subject` replaces GitHub's own squash-title composition
  # wholesale, and that composition appends "(#<number>)". Reproduce
  # the suffix rather than inherit it: `git log --oneline` and the
  # blame UI key the PR backlink off it, so dropping it would strip
  # that backlink from every commit landing through this workflow, for
  # every caller. It cannot disturb the scan below — the bump match is
  # a fixed-string search anywhere in the subject.
  # Strip an existing suffix before appending, so a title copied from
  # a `git log --oneline` line does not land as "... (#42) (#42)".
  squash_subject="${pr_title%" (#$PR_NUMBER)"} (#$PR_NUMBER)"

  # The merge below passes `--subject "$squash_subject"`, so the landing
  # subject IS the PR title — by construction, not by prediction.
  #
  # That is the whole reason the flag is passed. Left off, the subject
  # comes from the repo's `squash_merge_commit_title` setting, and
  # GitHub's default `COMMIT_OR_PR_TITLE` uses THE COMMIT's subject on
  # a single-commit PR, switching to the PR title only from two commits
  # up. A guard would then have to infer which text wins from a
  # per-repo setting nobody audits, and the rule taught to humans would
  # have to be "put the token in both places, because it depends".
  # Controlling the subject collapses both into one rule: PR title.
  bump_rank() {
    case "$1" in
      *'#major'*) echo 2 ;;
      *'#minor'*) echo 1 ;;
      *)          echo 0 ;;
    esac
  }
  # Join with printf rather than an inline literal newline: a
  # continuation line at column 0 would terminate this `run: |` block
  # scalar and break the file (caught by the YAML parse, but only after
  # the fact).
  asked_text=$(printf '%s\n%s' "$pr_title" "$pr_subjects")
  title_rank=$(bump_rank "$squash_subject")
  asked_rank=$(bump_rank "$asked_text")

  if [ "$title_rank" -lt "$asked_rank" ]; then
    case "$asked_rank" in
      2) asked='#major' ;;
      *) asked='#minor' ;;
    esac
    echo "::error::A ${asked} token was requested on a commit subject, but this squash lands ONE commit whose subject is the PR TITLE. tag-release.yml reads subjects only, so the token would end up in the commit body where the scan is designed not to look, and the release would be cut as a patch. Fix: edit the PR title to contain ${asked}, then re-run. Editing the title needs no new commit, so the approving review still stands. Pushing another commit to carry the token does NOT work — its subject becomes body text too."
    exit 1
  fi
  # Always say what this merge will cut, not only when a bump was
  # asked for. Forcing the subject closed one hazard and opened its
  # mirror: the PR title is now ALWAYS the landing subject, so a token
  # written incidentally in a title — quoting an error, naming the
  # convention in prose — cuts that release. tag-release.yml excludes
  # commit BODIES for exactly this reason (prose discussing a bump
  # matched itself and cut a spurious minor, measured), but a title
  # has no such exclusion available: under this convention the title
  # is the only channel, so an incidental token and an intended one
  # are identical text and no check can separate them.
  #
  # So the guard does the one thing that is actually available --
  # state the consequence in the log, before the merge.
  #
  # How long that log stays actionable depends on MODE, and the
  # difference is not cosmetic. GraphQL's `AutoMergeRequest` carries
  # `commitHeadline` alongside `enabledAt`: the subject is STORED when
  # auto-merge is enabled, not recomputed when GitHub finally merges.
  # On `mode: auto` -- the DEFAULT -- the title therefore stops
  # mattering a few steps below this one, and a title edit made after
  # that changes nothing about the release that gets cut. Saying
  # "still free to fix by editing the title" would be false on the
  # path most callers are on.
  #
  # Known bound, both modes: `commitHeadline` is documented as IGNORED
  # when the base branch merges through a MERGE QUEUE, which hands the
  # subject back to GitHub's own composition and voids the control
  # this guard rests on. No caller uses one today; a caller that
  # adopts one needs this guard to grow a check for it.
  # Print the landing subject itself, not only the bump it implies.
  # A human auditing this log for an incidental token, or checking
  # that the "(#N)" de-duplication above produced the right text, has
  # nothing to read otherwise.
  echo "Bump-token guard: the squash will land this subject: ${squash_subject}"
  case "$title_rank" in
    2) cuts="MAJOR release (major token found in the PR title)" ;;
    1) cuts="MINOR release (minor token found in the PR title)" ;;
    *) cuts="PATCH release (no bump token in the PR title)" ;;
  esac
  if [ "$MODE" = "direct" ]; then
    echo "Bump-token guard: this merge will cut a ${cuts}. The merge runs later in this same job, so editing the PR title before it does still changes what lands."
  else
    echo "::warning::Bump-token guard: this merge will cut a ${cuts}. Mode is '${MODE}', so the subject is handed to GitHub's auto-merge a few steps below and frozen there — editing the PR title after this run changes NOTHING about the release this cuts. To change it: edit the title, then re-run this workflow, which needs a fresh approving review or a manual dispatch."
  fi
fi

