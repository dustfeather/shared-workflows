set -euo pipefail
if [ "$method" = "--squash" ]; then
  pr_title=$(gh pr view "$PR_NUMBER" --repo "$REPO" --json title --jq '.title')
  # Subjects come from REST, NOT `gh pr view --json commits`. That
  # field is GraphQL `messageHeadline`, which returns GitHub's DISPLAY
  # form: measured on this PR, every subject longer than 72 characters
  # comes back as exactly 70 -- 69 characters plus a U+2026 ellipsis --
  # however much longer the real one is, while 72 and under are
  # untouched. The bump token is APPENDED by convention, so the clip
  # eats precisely the substring this scan searches for. The guard
  # would then read "no bump token asked for", allow the merge, and the
  # release would degrade to a patch with nothing in the log to say
  # why. Three commits on this branch are long enough (73, 75, 80).
  #
  # REST returns the raw message, so take its first line: bodies stay
  # excluded, which is the rule tag-release.yml itself scans by (prose
  # discussing a bump matched itself and cut a spurious minor).
  # per_page rides in the PATH, not in `-F`. `gh api` defaults to GET
  # only while no parameters are supplied; a single `-f`/`-F` flips the
  # default method to POST, and POST on this route is a 404. Under the
  # `set -euo pipefail` above that kills the step before the merge, so
  # EVERY squash merge through this workflow would fail for every
  # caller -- with the error pointing at the guard rather than at the
  # merge. `-X GET` would also work; a query string cannot be undone by
  # a later flag, so it is the safer of the two.
  #
  # The `// ""` and the `if . == ""` arm are both load-bearing on a
  # commit made with --allow-empty-message.
  #
  # `"" | split("\n")` is the EMPTY ARRAY, so `[0]` is null, and
  # `sub` on null is a hard jq error: "null (null) cannot be matched,
  # as it is not a string". Under the `set -euo pipefail` above that
  # kills the step, and the log blames the guard rather than the empty
  # commit. `// ""` turns the null back into a string first.
  #
  # Then the placeholder: command substitution strips trailing
  # newlines, so a LAST commit with an empty subject would vanish from
  # the capture, leaving n_commits one short of declared_commits below
  # and refusing a perfectly good merge under an error about the 250
  # cap that has nothing to do with it. An empty subject can carry no
  # token, so neither arm can mask one.
  pr_subjects=$(gh api --paginate "repos/$REPO/pulls/$PR_NUMBER/commits?per_page=100" \
    --jq '.[].commit.message | split("\n")[0] // "" | sub("\r$";"") | if . == "" then "(empty subject)" else . end')

  # This list is capped -- the pull-request commits endpoint stops at
  # 250 however far it is paginated -- and a token past the cap is
  # exactly the silent failure this guard exists to prevent.
  #
  # Do not test the count against the cap. `--paginate` defaults to
  # per_page=30, which stops at the last whole page below the cap, so a
  # `-ge 250` test would never fire and the truncation would pass
  # unnoticed; the per_page=100 above happens to make the boundary land
  # on 250, but that is arithmetic to get wrong twice. Compare against the PR's own
  # declared commit count instead, which is a plain integer on the PR
  # object and carries the TRUE total -- measured on NixOS/nixpkgs#560469,
  # where `.commits` is 780 while the paginated commits listing returns
  # exactly 250. So the two sides legitimately disagree past the cap and
  # the refusal below fires there instead of failing open.
  #
  # A failed PAGE is not among the causes this can see, though it is
  # tempting to list: `pr_subjects` is assigned from a command
  # substitution, and under the `set -euo pipefail` at the top of this
  # step a nonzero `gh api` kills the step outright, so a dropped page
  # never reaches the comparison. What reaches it is truncation at the
  # cap and a head that moved between the reads -- nothing else.
  # Three reads across two endpoints, so the head can move between any
  # of them, and the refusal must not name a cause it cannot know.
  #
  # The re-read below catches ONE of the two orderings: a push landing
  # between the declared-count read and the re-read changes the count
  # under us, and the two readings name it a moving head. The MIRROR
  # ordering is invisible to it -- a push landing between the commit
  # list and the declared-count read leaves declared and recheck equal
  # (both post-push) while the subjects are pre-push, so the shortfall
  # looks exactly like truncation on a PR that may have twenty commits.
  # That is the same wrong diagnosis the re-read exists to prevent,
  # pointing the other way.
  #
  # `declared_commits` is what separates them, because it is not capped
  # -- measured above at 780 against a 250-item listing. A PR at or
  # below 250 could have had every commit returned, so the cap cannot
  # explain a shortfall there, and the message says so instead of
  # asserting truncation. Note the direction of the dependency: 250
  # picks WORDING here, never whether to refuse. The refusal is already
  # decided by the count mismatch above, so if GitHub ever moves the
  # cap the worst outcome is a message that hedges the wrong way.
  #
  # This narrows the race rather than closing it; the merge itself is
  # what closes it, via `--match-head-commit` when the caller sets it.
  declared_commits=$(gh api "repos/$REPO/pulls/$PR_NUMBER" --jq '.commits')
  n_commits=$(printf '%s' "$pr_subjects" | grep -c '' || true)
  if [ "$n_commits" -ne "$declared_commits" ]; then
    recheck_commits=$(gh api "repos/$REPO/pulls/$PR_NUMBER" --jq '.commits')
    if [ "$recheck_commits" -ne "$declared_commits" ]; then
      echo "::error::The head of #${PR_NUMBER} moved while this guard was reading it: the commit count went from ${declared_commits} to ${recheck_commits} between two reads, and the ${n_commits} subjects scanned are from before that. A bump token on a commit that arrived mid-read would be invisible here, so this merge is refused. Re-run once the branch has settled; set match-head-commit if you want the merge itself to refuse a moved head."
      exit 1
    fi
    # Both directions are reachable, and only one of them is a short
    # read. Commits can be REMOVED between the two reads as well as
    # added -- a force-push or a base rebase -- which leaves MORE
    # subjects in hand than the PR now declares. The recheck above
    # does not catch it unless the count moves a second time, so
    # without this arm the message reports "declares 3 but only 5
    # could be read" and hangs a cap diagnosis off it. Refusing is
    # right either way; the diagnosis is what has to stop guessing.
    if [ "$n_commits" -gt "$declared_commits" ]; then
      cause="that is MORE subjects than the PR declares, so the head shrank between the two reads -- a force-push or a base rebase dropped commits after the list was fetched. The subjects in hand are from before that, and the cap plays no part in it"
    elif [ "$declared_commits" -gt 250 ]; then
      cause="${declared_commits} is past the 250-commit cap on the pull-request commit list, so the read was most likely truncated there -- though a push landing between the two reads produces the same shortfall"
    else
      cause="${declared_commits} is within the 250-commit cap on that list, so truncation does not explain this either -- what is left is a push landing between the commit read and the count read"
    fi
    echo "::error::PR #${PR_NUMBER} declares ${declared_commits} commits and ${n_commits} subjects were read -- ${cause}. A bump token on a commit this guard did not scan would be invisible here, so this merge is refused rather than decided from a list that does not match the PR. Re-run; if it repeats, check the subjects by hand and merge manually, or split the PR."
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
    # Name BOTH remedies, and which one applies. The refusal cannot
    # tell an intended token from an incidental one -- a subject like
    # `docs: explain the #minor token` trips it with no bump meant --
    # and naming only the title edit turns this message into the
    # mirror hazard the block below describes: follow it when the
    # token was incidental and the title, which is always the landing
    # subject, cuts a release nobody asked for. The second remedy
    # costs an approving review, so it has to say so rather than
    # leave the author to discover it after the force-push.
    echo "::error::A ${asked} token was requested on a commit subject, but this squash lands ONE commit whose subject is the PR TITLE. tag-release.yml reads subjects only, so the token would end up in the commit body where the scan is designed not to look, and the release would be cut as a patch. There are two fixes and this guard cannot tell which you need, because an intended token and an incidental one are identical text. If you DID mean the bump: edit the PR title to contain ${asked}, then re-run -- that needs no new commit, so the approving review still stands. If the token was INCIDENTAL, prose merely naming ${asked} rather than asking for it: do NOT copy it into the title, because the title is always the landing subject and that cuts a ${asked} release nobody asked for -- reword the commit subject instead, which means a force-push and a re-requested review. Either way, pushing an ADDITIONAL commit to carry the token does NOT work: its subject becomes body text too."
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
  # The subject is frozen on BOTH modes, and the freeze point is the
  # `pr_title` read at the top of this step -- not the merge. That read
  # is baked into `squash_subject` and passed as `--subject`; nothing
  # re-reads the title afterwards. So a title edit made after this
  # point changes nothing on `direct` either. MODE changes only how
  # long the window between the read and the merge is: seconds on
  # `direct`, indefinite on `auto`, where GitHub then holds the value
  # itself -- GraphQL's `AutoMergeRequest` carries `commitHeadline`
  # alongside `enabledAt`, so it is stored at enable time rather than
  # recomputed at merge time. That is why `auto` warns: the window is
  # long enough that someone will try to edit the title inside it.
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
  # "would land", not "will land". The one configuration this guard
  # cannot control is a base branch behind a MERGE QUEUE, which
  # discards the subject passed here and composes its own (see the
  # note above). No caller uses one, so there is nothing to detect
  # today and a detection call that can never fire is worse than none
  # -- but a log line that states the outcome as fact would be
  # confidently wrong in exactly that case, and a wrong log is worse
  # than a hedged one. The hedge costs a word.
  echo "Bump-token guard: this squash would land the subject: ${squash_subject} (a base branch behind a merge queue discards it and composes its own; no caller uses one)"
  case "$title_rank" in
    2) cuts="MAJOR release (major token found in the PR title)" ;;
    1) cuts="MINOR release (minor token found in the PR title)" ;;
    *) cuts="PATCH release (no bump token in the PR title)" ;;
  esac
  # The ANNOTATION is gated; the message is not. Two different
  # hazards share this line, and only one of them scales with the
  # annotation:
  #   - an incidental token in the title cutting a release nobody
  #     asked for. Can only happen when `title_rank` is nonzero.
  #   - a title edited after this read, which changes nothing. Can
  #     happen at any rank, and is most likely at rank 0, where the
  #     author has not written a token yet.
  # The second is why the text is printed unconditionally. But a
  # `::warning::` on EVERY squash merge -- and `auto` is the default,
  # so that is every merge in every caller repo -- makes a clean run
  # render yellow, which is how an annotation stops being read. The
  # one it would drown is the rank-nonzero case, the only one where
  # a release gets cut that nobody asked for. So escalate there and
  # log plainly otherwise: same words, same log, no yellow.
  #
  # The annotation fires on BOTH modes, though the two wordings
  # differ. The rank gate and the mode split answer different
  # questions: an incidental token cutting an unasked-for release is
  # mode-independent -- `direct` cuts it just as hard, only sooner --
  # while the wording is about the second hazard, a title edited
  # after this read, whose window is what the mode governs. Gating
  # the annotation on the mode as well would exempt exactly the
  # wrong repo: this one's `pr-merge.yml` passes `direct`, and a
  # workflow library is where PR titles are likeliest to discuss
  # bump tokens in prose.
  if [ "$title_rank" = "0" ]; then ann=""; else ann="::warning::"; fi
  if [ "$MODE" = "direct" ]; then
    echo "${ann}Bump-token guard: this merge will cut a ${cuts}. The subject was captured at the top of this step, so editing the PR title from now on does not change this release; re-run to change it. On this mode the merge follows within seconds, so that window is short."
  else
    echo "${ann}Bump-token guard: this merge will cut a ${cuts}. The subject was captured at the top of this step, so editing the PR title from now on changes NOTHING about the release this cuts. Mode is '${MODE}', so the merge may not happen for a long time yet — GitHub stores the subject when auto-merge is enabled below and does not re-read the title when it finally merges, so the wait does not give the title back. To change it: edit the title, then re-run this workflow, which needs a fresh approving review or a manual dispatch."
  fi
fi

del=""
if [ "$DELETE_BRANCH" = "true" ]; then
  del="--delete-branch"
fi

# Pin the merge to the reviewed commit when the caller asked for it.
# `gh` fails the merge if the head has moved since, which is the
# point: the commit that moved it has not been reviewed.
match=""
if [ -n "${MATCH_HEAD_COMMIT:-}" ]; then
  match="--match-head-commit $MATCH_HEAD_COMMIT"
  echo "Merge pinned to reviewed head $MATCH_HEAD_COMMIT."
fi

# `direct` captures the status instead of letting `set -e` act on it.
# This command can exit nonzero AFTER the merge has already landed —
# `--delete-branch` failing on a protected or already-deleted branch is
# the obvious way, and gh's exact exit behaviour there is NOT verified
# here. Under `set -e` that kills the step before any of the post-merge
# work below, which is the same unrecoverable outcome every comment
# down there is written to avoid: tag-release.yml only ever sees this
# job's `workflow_run`, so a failed step means the merge commit is
# never tagged and there is no retry path, and the linked issues are
# never closed and never annotated.
#
# Nothing is assumed about what the status means. The merged-state read
# below is what adjudicates, and it already distinguishes merged from
# not-merged from could-not-tell. A nonzero status just gets said out
# loud so the log shows it alongside whatever that read concludes.
#
# `auto` stays bare. There the merge has NOT happened — gh hands it to
# GitHub and returns — so a nonzero exit means auto-merge was not
# enabled, nothing landed, and there is no tag or close to strand.
# Failing is the correct outcome on that path.

# Assembled as an array: `--subject` carries a PR title with spaces in
# it, while the unquoted `$del $match` below depend on word splitting
# and so cannot hold one. Never empty, which keeps `set -u` happy.
#
# The subject reaches BOTH paths, and on `auto` -- the default -- it
# survives all the way to the merge. MEASURED end to end on a
# throwaway repo, not argued from source: enabling auto-merge with
# `--subject "STORED HEADLINE probe (#1)"` put that exact string in
# `autoMergeRequest.commitHeadline`; editing the PR title afterwards
# left commitHeadline untouched; and when the blocking check went
# green the landed squash commit read `STORED HEADLINE probe (#1)`,
# not the original title and not the edited one. So the guard's
# promise holds for `mode: auto` callers, and the freeze CLAUDE.md
# describes is the observed behaviour rather than an inference.
#
# The mechanism, for whoever has to re-check this: gh builds one
# `MergePullRequestInput` and sets `CommitHeadline` on it before
# choosing the mutation; the auto path then sends
# `EnablePullRequestAutoMergeInput{input}`, which embeds that same
# struct.
#
# Read out of cli/cli `pkg/cmd/pr/merge/http.go`, not assumed, and
# checked across releases rather than at one version: v2.0.0, v2.20.0,
# v2.40.0 and v2.55.0 all set `CommitHeadline` BEFORE branching on
# `auto`. If gh ever splits the two paths, this comment is the thing to
# re-check.
#
# That evidence bounds the PAYLOAD FIELD, not the CLI flag, and the two
# have different floors -- do not read the paragraph above as licensing
# "any gh 2.x" for `--subject` itself. The flag's own floor, measured in
# cli/cli history: `gh pr merge -t/--subject` first shipped in **v2.4.0**
# (commit f2d23d8c88845b8cf94a54130baa9fd5531e8801, "Allow editing commit
# subject when squash-merging a PR"; absent from the v2.3.0 tree, present
# in v2.4.0 at pkg/cmd/pr/merge/merge.go). `-t` and `--subject` are one
# `StringVarP` call, so there is no separate short-form floor.
#
# Worth pinning because of where an unknown flag lands, which is not
# symmetric: on `auto` -- the DEFAULT mode -- the bare `gh pr merge
# --auto` below is unguarded and the step fails for that caller, while
# `direct` swallows the same failure into "It may still have merged".
# v2.4.0 is from 2021 and GitHub-hosted and ARC runners are far past it,
# so this cannot bite today; the floor is recorded so the next reader
# inherits a checked number instead of a plausible one.
args=("$method")
if [ "$method" = "--squash" ]; then
  args+=(--subject "$squash_subject")
fi

merge_rc=0
if [ "$MODE" = "direct" ]; then
  gh pr merge "$PR_URL" "${args[@]}" $del $match || merge_rc=$?
  if [ "$merge_rc" -ne 0 ]; then
    echo "::error::gh pr merge exited ${merge_rc} for $PR_URL. It may still have merged — the branch deletion can fail after the merge lands. See the merge state reported below; do not assume this means nothing merged."
  fi
else
  gh pr merge --auto "${args[@]}" "$PR_URL" $del $match
fi

