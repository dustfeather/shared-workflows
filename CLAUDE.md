# CLAUDE.md

Library of reusable GitHub Actions workflows (`workflow_call`) in `.github/workflows/`, consumed by every other repo under this account via `uses: dustfeather/shared-workflows/.github/workflows/<name>.yml@v4`. No app code. A merged change ships to every caller on next run — treat blast radius accordingly. Exception: `tag-release.yml` = this repo's own release automation, not callable.

## Verifying changes

No build/test; only local check = YAML parse (`python3 -c "import yaml; yaml.safe_load(...)"`). Reusable workflows cannot be invoked from local checkout — `uses:` resolves through GitHub. Real e2e test: push to feature branch, point one caller at it (`@feature/<name>`) before tagging.

## Versioning — pick bump by caller impact

`tag-release.yml` auto-tags every push to `main`: default bumps **patch** (wraps at 100 → minor; minor uncapped), re-points the floating major tag (currently `v4`). **major never bumped automatically.** Larger bump: put `#minor` or `#major` in a commit message (largest wins). Match is fixed-string over the **subject** of **every commit in the push**, not just HEAD — one push fires one run, so a HEAD-only scan would drop a token from an earlier commit in a batch. Bodies are excluded on purpose: prose *discussing* a bump matched itself and cut a spurious minor. Don't write tokens verbatim unless you mean them.

- **patch** — bug fix, doc/log/comment tweak, internal refactor, bumping action used inside workflow with no interface change.
- **minor** — backwards-compatible feature: new *optional* input (with default), new workflow, opt-in job/step.
- **major** — breaks workflow contract: removing/renaming input or secret, adding *required* input, changing default callers must react to, requiring new permissions.

Rule of thumb: if caller's shim could keep working untouched → patch/minor; if not → major. Unsure → prefer larger bump.

## Conventions

- Inputs added to reusable workflow MUST default to value preserving prior behavior.
- Every workflow declares explicit top-level `permissions:` block; jobs needing more declare own. CodeQL flags missing — hard error, not warning.
- Reusable workflows run in **caller's** context with caller's secrets. `secrets: inherit` works for same-owner callers; cross-owner (`ITGuys-RO/*`) MUST pass secrets explicitly — `inherit` does not reliably carry org-level "selected"-visibility secrets across owner boundary.

## Code exploration

Plain `Grep`/`Glob`/`Read` are the tools here. 17 YAML workflows, no call graph — a knowledge graph earns nothing on this repo.

`code-review-graph` is **CI-only**: it exists on the `actions-runner-claude` ARC image, where `claude-code-review.yml` builds it and serves it over MCP to the review agent. It is not installed locally and no session here can call those tools. Any `.code-review-graph/` dir you find in a checkout is a stale leftover (gitignored) — delete it.

The local equivalent is the `graphify` CLI (pipx `graphifyy`, `graphify-out/` built in the app repos). Not wired as an MCP server; reach for it via the `graphify` skill, and only in a repo with real code to trace.
