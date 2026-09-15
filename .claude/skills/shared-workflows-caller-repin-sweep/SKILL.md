---
name: shared-workflows-caller-repin-sweep
description: |
  Repin repos that `uses:` dustfeather/shared-workflows, after THIS repo cuts a version
  tag. Use when tag-release.yml has just run, when a major is cut, or to verify a repin.
  Callers float `@vN`, so only a MAJOR strands them; patch/minor reach every caller on their
  own. Discriminator: `gh search code` misses most callers, and repinning before the tag
  exists breaks every caller at once.
author: Claude Code
version: 2.0.0
date: 2026-09-15
---

# Caller repin sweep — run it when this repo cuts a version

This skill belongs to `dustfeather/shared-workflows` and has exactly one
trigger: **this repo just cut a version tag.** It is not a standing fleet audit.
Callers pin a floating major (`@v6`), so between majors there is nothing to
sweep — `tag-release.yml` re-points `v6` onto the new commit and every caller
picks it up on its next run without anyone touching them.

## Decide first: does this release strand anyone?

Read the tag `tag-release.yml` just cut, and stop early when you can.

| Release | What happened to callers | Action |
|---|---|---|
| patch (`v6.0.1` → `v6.0.2`) | `v6` moved; every caller already has it | none — say so and stop |
| minor (`v6.0.2` → `v6.1.0`) | same | none — say so and stop |
| **major** (`v6.x` → `v7.0.0`) | `v6` **froze** where it stood; callers are stranded permanently | run the sweep |

The major row is the whole point. `tag-release.yml` re-points only the CURRENT
major, so cutting v7 freezes v6 exactly as the v6 cut froze v5 at v5.3.0. A
caller left on the old major does not lag by a version — it stops receiving
fixes for good, and nothing reports that.

The one other time to run it: **`--verify` after a sweep**, to confirm the
previous repin actually landed everywhere.

Confirm which kind of release it was rather than assuming:

```bash
gh api repos/dustfeather/shared-workflows/tags --jq '.[0:4][].name'
```

## The five things that go wrong

### 1. `gh search code` misses most callers

It is index-backed, lags, and skips private and low-traffic repos. A short,
plausible-looking list comes back and reads like a complete one. **Enumerate
repos, then read their workflow files.** `scripts/repin.sh` does this; if you
work by hand, do the same.

### 2. Repinning before the tag exists breaks every caller at once

A `uses:` ref that resolves to nothing is `startup_failure`: zero jobs, empty
logs, no annotation. It looks like a GitHub outage rather than a bad ref, in
every repo simultaneously. **Confirm the tag resolves before touching any
caller:**

```bash
gh api repos/dustfeather/shared-workflows/git/ref/tags/v7 --jq .object.sha
```

The script refuses to apply if this fails. Do not remove that check. It matters
most in exactly this skill's trigger situation — running moments after a cut,
when the tag may not have been pushed yet.

### 3. The residue check must not reuse the substitution's own pattern

The natural post-edit assertion — "no `@v6` refs survive" — is worthless if it
greps with the same regex the `sed` just used. By construction nothing survives
*that* pattern; the shapes that survive are the ones it never matched (a quoted
ref, an unusual workflow name, a comment, a line continuation). That is exactly
the half-rewritten repo you are trying to prevent, and the tautology hides it.

Verify with a **deliberately looser, independently written** pattern and compare
counts. A discrepancy is the finding.

### 4. A frozen major in an archived repo is not a blocker

Repos still on `@v4` are archived. They never receive a v5 or v6 change, and
"fixing" them revives dead CI and skips whole majors of contract changes at
once. **Leave them.** Report them as archived-and-frozen, not as work.

### 5. Stored inventory counts drift, and drift confidently

A previous sweep's numbers went stale between sessions — a memory note claimed
92 refs / 73 files where the live measurement was 88 / 63, having double-counted
comment-only examples and swept archived repos into the file count.
**Re-measure every time; never report a remembered count.**

## Classifying what you find

Not every non-current ref is work. Decide per shape:

| Ref shape | Meaning | Action |
|---|---|---|
| `@v7` (current floating major) | up to date | none |
| `@v6`, `@v5` (frozen major) | stranded, unless archived | repin, or report as archived |
| `@v6.1.0` (exact tag) | deliberate freeze | **ask** — do not silently float it |
| `@<sha>` | deliberate freeze, usually security | **ask** |
| `@feature/<name>` | mid-e2e-test of an unreleased change | leave; flag as transient |
| `@main` | tracks an untagged branch | flag — usually a mistake |

The exact-tag and SHA rows matter: a sweep that "helpfully" floats a deliberate
pin removes a freeze someone chose. Report and ask.

## Excluding shared-workflows itself

This repo's own pins are **not** part of the sweep and the script skips it. They
need a direct push to `main` for a reason specific to here: `pr-checks.yml` names
itself in its own `paths-ignore`, so a PR touching only it creates no run object
at all — no run, no review, no approval, and `pr-merge.yml` triggers on
`pull_request_review`. It cannot land itself. Bump them separately, by hand, on
`main`. (That still has to happen on a major — it is just not this script's job.)

## Procedure

```bash
S=.claude/skills/shared-workflows-caller-repin-sweep/scripts

# 1. Inventory only. Writes a table; changes nothing.
"$S/repin.sh" --from v6 --to v7

# 2. Read the table. Resolve every ASK row with the user before continuing.

# 3. Apply. Clones, edits, commits, pushes to each default branch.
"$S/repin.sh" --from v6 --to v7 --apply

# 4. Verify independently, on FRESH clones, with the loose detector.
"$S/repin.sh" --from v6 --to v7 --verify
```

`--verify` re-clones and greps with the loose pattern rather than the strict
one, and does so on every caller rather than only the ones the strict pattern
matched — so it is a real second opinion, not a replay of step 3's assumption.

## Why a direct push and not PRs

Twenty one-line `uses:` bumps through twenty PRs would need twenty reviews to
say the same thing, and in repos whose review agent is itself invoked through
the workflow being changed, several of those PRs cannot land themselves. The
change is mechanical, uniform, and verified by grep. Push it.

This is the one genuinely hard-to-reverse part of the sweep, so the script keeps
`--apply` explicit and separate from the inventory, and never infers it.

## Reporting

Report per-repo, and always both numbers:

```
repos touched: N   files: N   refs: N
ASK:     owner/repo  path:line  @v6.1.0  (exact tag — deliberate?)
SKIPPED: owner/repo  (archived, frozen at v4)
```

State the measured counts, never remembered ones, and say explicitly if the
strict and loose detectors disagreed. When the release was a patch or minor,
the entire report is one line saying no caller needed touching and why.
