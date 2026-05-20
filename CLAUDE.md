# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A library of **reusable GitHub Actions workflows** (`workflow_call`) in
`.github/workflows/`, consumed by every other repo under this account via
`uses: dustfeather/shared-workflows/.github/workflows/<name>.yml@v1`. No
application code. A change merged here ships to every caller on their next run —
treat blast radius accordingly. Exception: `tag-release.yml` is this repo's own
release automation, not callable.

## Verifying changes

No build/test step; the only local check is YAML parse (`python3 -c "import yaml; yaml.safe_load(...)"`).
Reusable workflows cannot be invoked from a local checkout — `uses:` resolves
through GitHub. For real end-to-end testing, push to a feature branch and point
one caller at it (`@feature/<name>`) before tagging.

## Versioning — pick the bump by caller impact

`tag-release.yml` auto-tags every push to `main`: default bumps **patch**
(wraps at 100 into minor; minor uncapped), and re-points the floating `v1` tag.
**major is never bumped automatically.** To request a larger bump, put `#minor`
or `#major` in the commit subject (largest wins). The match is a fixed-string
search — do not write those tokens verbatim in a commit message unless you mean them.

- **patch** — bug fix, doc/log/comment tweak, internal refactor, bumping an action used inside a workflow with no interface change.
- **minor** — backwards-compatible feature: a new *optional* input (with default), a new workflow, an opt-in job/step.
- **major** — breaks the workflow contract: removing/renaming an input or secret, adding a *required* input, changing a default callers must react to, requiring new permissions.

Rule of thumb: if a caller's shim could keep working untouched → patch/minor;
if not → major. When unsure, prefer the larger bump.

## Conventions

- Inputs added to a reusable workflow must default to a value preserving prior behavior.
- Every workflow declares an explicit top-level `permissions:` block; jobs needing more declare their own. CodeQL flags a missing block — treated as a hard error, not a warning.
- Reusable workflows run in the **caller's** context with the caller's secrets. `secrets: inherit` works for same-owner callers; cross-owner callers (e.g. `ITGuys-RO/*`) must pass secrets explicitly — `inherit` does not reliably carry org-level "selected"-visibility secrets across the owner boundary.
