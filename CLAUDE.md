# CLAUDE.md

Library of reusable GitHub Actions workflows (`workflow_call`) in `.github/workflows/`, consumed by every other repo under this account via `uses: dustfeather/shared-workflows/.github/workflows/<name>.yml@v6`. No app code. A merged change ships to every caller on next run — treat blast radius accordingly. Two exceptions, neither callable — this repo's own automation: `tag-release.yml` (cuts the version tag) and `pr-merge.yml` (lands a PR the review agent approved).

## Verifying changes

No build/test; only local check = YAML parse (`python3 -c "import yaml; yaml.safe_load(...)"`). Reusable workflows cannot be invoked from local checkout — `uses:` resolves through GitHub. Real e2e test: push to feature branch, point one caller at it (`@feature/<name>`) before tagging.

## Versioning — pick bump by caller impact

`tag-release.yml` auto-tags every push to `main`: default bumps **patch** (wraps at 100 → minor; minor uncapped), re-points the floating major tag (currently `v6`). **major never bumped automatically.** Larger bump: put `#minor` or `#major` **in the PR title** when it lands through a PR, **on the commit subject** when pushed straight to `main` (largest wins). Match is fixed-string over the **subject** of **every commit in the push**, not just HEAD — one push fires one run, so a HEAD-only scan would drop a token from an earlier commit in a batch. Bodies are excluded on purpose: prose *discussing* a bump matched itself and cut a spurious minor. Don't write tokens verbatim unless you mean them — **in a PR title least of all**, since that text is now always the landing subject, so prose merely *naming* a token cuts that release. This is the same failure the body exclusion exists to stop, on the one channel where no exclusion is possible.

**PR title, since 2026-09-14**, when `pr-merge.yml` moved to `merge-method: squash` so main's history verifies (a rebase merge is replayed server-side and arrives unsigned; GitHub signs a squash commit with its web-flow key). A squash lands one commit, and `merge-on-approval.yml` passes `--subject "<the PR title>"` so that commit's subject is the PR title by construction. The flag is what makes the rule single-valued: without it the subject comes from the repo's `squash_merge_commit_title`, and GitHub's default `COMMIT_OR_PR_TITLE` uses the commit's own subject on a single-commit PR, switching to the PR title only from two commits up — so the advice would depend on a per-repo setting and on the commit count. The guard refuses a merge when a bump asked for anywhere is missing from the PR title; the fix it names is editing the title, which needs no new commit and so does not drop the approving review. A direct push to `main` is unaffected — nothing replays it.

**The title stops being editable earlier than you think, on BOTH modes.** The freeze point is the guard's own `pr_title` read at the top of its step: that value is baked into `squash_subject` and passed as `--subject`, and nothing re-reads the title afterwards. An edit after that changes nothing on `direct` either — `direct` only gives a shorter window, because its merge follows within seconds. `merge-on-approval.yml` defaults to `auto`, and `enablePullRequestAutoMerge` stores the subject: GraphQL's `AutoMergeRequest` carries `commitHeadline` next to `enabledAt`, so GitHub keeps what `gh` sent when auto-merge was enabled and does not re-read the title when it later merges. **Measured, not inferred** (2026-09-14, throwaway repo): auto-merge enabled with a subject distinct from the title stored that subject in `commitHeadline`; the title was then edited and `commitHeadline` did not move; the landed squash commit carried the stored subject, not the edited title. Editing the title after that run changes nothing about the release it cuts — the remedy is a re-run, which needs a fresh approving review or a manual dispatch. The guard says which of the two situations you are in, warning on `auto` and noting on `direct`. This repo's own `pr-merge.yml` passes `mode: direct` (it has to merge synchronously, see below), so here the window between the read and the merge is seconds rather than open-ended — short, not live.

**A PR with more than 250 commits is refused, not merged.** Caller-visible and new in v6, on the DEFAULT settings (`merge-method: squash`, `mode: auto`), so a caller that overrides neither is affected. To decide the bump the guard has to read every commit subject on the PR, and the pull-request commits endpoint stops at 250 however far it is paginated — a bump token on commit 251 would be silently invisible, which is the exact failure this guard exists to prevent. It therefore compares the subjects it read against the PR's own `.commits` total (uncapped: measured at 780 on a PR whose listing returns 250) and refuses on any shortfall rather than deciding a release from a partial list. **Remedy: merge that PR by hand, or split it.** The same refusal covers a failed page or a push landing mid-read, and the error message names which of those it can rule out.

- **patch** — bug fix, doc/log/comment tweak, internal refactor, bumping action used inside workflow with no interface change.
- **minor** — backwards-compatible feature: new *optional* input (with default), new workflow, opt-in job/step.
- **major** — breaks workflow contract: removing/renaming input or secret, adding *required* input, changing default callers must react to, requiring new permissions.

Rule of thumb: if caller's shim could keep working untouched → patch/minor; if not → major. Unsure → prefer larger bump.

### How a merge reaches `tag-release.yml` — read before touching either file

`pr-merge.yml` lands a PR the review agent approved, merging with the default
`GITHUB_TOKEN`. GitHub creates **no workflow run from events raised by that
token**, so a bot merge fires no `push` — and that suppression belongs to the
token, not the trigger, so `pull_request: [closed]` is dead too. There is no
merge event to listen for. `tag-release.yml` therefore has a second trigger,
`workflow_run` on **`"PR merge on approval"` matched by NAME**: renaming
`pr-merge.yml`'s `name:` stops all tagging and is not an error.

Consequences a change here is most likely to get wrong:

- **`pr-merge.yml` must merge synchronously** (`mode: direct`). `--auto`
  returns before the merge, so the run completes — raising `workflow_run` —
  while `main` is still the pre-merge commit; tag-release then sees the head
  already tagged, exits clean, and the commit is stranded untagged for good.
- **`tag-release.yml`'s guard on `workflow_run.conclusion == 'success'` is
  the load-bearing one.** `types: [completed]` delivers every conclusion, and
  a run whose only job is filtered out concludes `skipped` (measured: runs
  34702777640, 34702294904) — the conclusion test is what drops it. The
  shim's own `if:` on state and draft duplicates what `merge-on-approval.yml`
  checks internally; keep it, because it stops pointless runs and keeps the
  intent next to the trigger, but do not mistake it for the thing preventing
  a `changes_requested` review from cutting a release.
- **`pr-merge.yml` pins `@v5` while this branch ships `v6`, so that pin is
  FROZEN, not lagging.**
  A `uses:` job validates inputs against the PINNED ref, not the PR. So a PR
  adding an input to a reusable workflow *and* passing it from this repo's own
  shim in one commit fails `Invalid input, <name> is not defined in the
  referenced workflow` — `startup_failure`, merge job never starts, PR cannot
  land itself. Merge it by hand, or split: ship the input, let `tag-release.yml`
  move the major tag, then add the caller.
  Within one major that lag self-heals at the next tag. ACROSS a major it does
  not: `tag-release.yml` re-points only the CURRENT major, so the v6 cut froze
  `v5` exactly as the v5 cut froze `v4` at v4.14.2. Until `pr-merge.yml` is
  repointed to `@v6` by hand (tracked in #43), this repo's own merges keep running frozen v5
  code — which means the explicit squash `--subject` described above is NOT in
  effect for this repo's merges, however plainly the rest of this file states
  it. Same for `pr-checks.yml`, which has the extra constraint below.
- **`pr-checks.yml` names ITSELF in its own `paths-ignore`.** A PR whose only
  changed file is `.github/workflows/pr-checks.yml` changes a non-empty set of
  files, every one of them excluded, so GitHub creates **no run object** — not a
  skipped run, absent. No run means no review; no review means no approval; and
  `pr-merge.yml` triggers on `pull_request_review`. So such a PR cannot land
  itself, nothing goes red, and it sits waiting for CI that will never be
  scheduled. **Push that file straight to `main`** — that is how the `@v4`→`@v5`
  pin bump shipped, and it is the only remedy. Bundling the edit with a change to
  another path looks like a second option and is not one: the run then exists, but
  `claude-code-action` rejects its OIDC exchange whenever the PR edits the
  workflow that triggered the run, then swallows the 401 and exits 0. So
  bundling trades "no run at all" for "a green run that reviewed nothing",
  which is strictly worse here — the first failure is visible, the second is
  not. Treat bundling as a failure mode, not a fallback.

- **`#major` behaves asymmetrically by path.** On a real push range, a
  `#major` not on the head subject is a hard `exit 1`. On the `workflow_run`
  path there is no push range, so the scan spans last-release-tag..HEAD and
  re-reads every unreleased commit on every run — a refusal there would wedge
  the workflow permanently, so it warns and downgrades instead.

## Conventions

- Inputs added to reusable workflow MUST default to value preserving prior behavior.
- Every workflow declares explicit top-level `permissions:` block; jobs needing more declare own. CodeQL flags missing — hard error, not warning.
- Reusable workflows run in **caller's** context with caller's secrets. `secrets: inherit` works for same-owner callers; cross-owner (`ITGuys-RO/*`) MUST pass secrets explicitly — `inherit` does not reliably carry org-level "selected"-visibility secrets across owner boundary.

## Code exploration

Plain `Grep`/`Glob`/`Read` are the tools here. 18 YAML workflows, no call graph — a knowledge graph earns nothing on this repo.

`code-review-graph` is **CI-only**: it exists on the `actions-runner-claude` ARC image, where `claude-code-review.yml` builds it and serves it over MCP to the review agent. It is not installed locally and no session here can call those tools. Any `.code-review-graph/` dir you find in a checkout is a stale leftover (gitignored) — delete it.

The local equivalent is the `graphify` CLI (pipx `graphifyy`, `graphify-out/` built in the app repos). Not wired as an MCP server; reach for it via the `graphify` skill, and only in a repo with real code to trace.
