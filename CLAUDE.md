# CLAUDE.md

Library of reusable GitHub Actions workflows (`workflow_call`) in `.github/workflows/`, consumed by every other repo under this account via `uses: dustfeather/shared-workflows/.github/workflows/<name>.yml@v4`. No app code. A merged change ships to every caller on next run — treat blast radius accordingly. Two exceptions, neither callable — this repo's own automation: `tag-release.yml` (cuts the version tag) and `pr-merge.yml` (lands a PR the review agent approved).

## Verifying changes

No build/test; only local check = YAML parse (`python3 -c "import yaml; yaml.safe_load(...)"`). Reusable workflows cannot be invoked from local checkout — `uses:` resolves through GitHub. Real e2e test: push to feature branch, point one caller at it (`@feature/<name>`) before tagging.

## Versioning — pick bump by caller impact

`tag-release.yml` auto-tags every push to `main`: default bumps **patch** (wraps at 100 → minor; minor uncapped), re-points the floating major tag (currently `v4`). **major never bumped automatically.** Larger bump: put `#minor` or `#major` in a commit message (largest wins). Match is fixed-string over the **subject** of **every commit in the push**, not just HEAD — one push fires one run, so a HEAD-only scan would drop a token from an earlier commit in a batch. Bodies are excluded on purpose: prose *discussing* a bump matched itself and cut a spurious minor. Don't write tokens verbatim unless you mean them.

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
- **`pr-merge.yml` pins `@v4`, which always lags this branch by one release.**
  A `uses:` job validates its inputs against the PINNED ref, not against the
  PR. So a PR that adds an input to a reusable workflow *and* passes it from
  this repo's own shim in the same commit fails with `Invalid input, <name> is
  not defined in the referenced workflow` — `startup_failure`, the merge job
  never starts, and the PR cannot land itself. Either merge that PR by hand,
  or split it: ship the input, let `tag-release.yml` move `v4`, then add the
  caller.
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
