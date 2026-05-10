<!--
This repo's workflows are consumed by every other repo via
`uses: dustfeather/shared-workflows/.github/workflows/<name>.yml@v1`.
A merge here ships to every caller on its next workflow run. Help the
reviewer assess that blast radius.
-->

## What changed

<!-- One or two sentences. -->

## Callers affected

- [ ] `publish-chrome.yml` consumers (filelist-ext, series-auto-skip, discord-purge, uninsta)
- [ ] `publish-firefox.yml` consumers (same four)
- [ ] `claude-code-review.yml` consumers (every repo with the shim)
- [ ] `claude.yml` consumers (every repo with the shim)
- [ ] None — this PR only touches docs / tests / repo metadata

## Compatibility

- [ ] **Backwards-compatible** — every existing caller continues to work without changes. After merge, force-move `v1` to this commit.
- [ ] **Breaking** — at least one caller must update its `uses:` ref or `with:` inputs. Bump to `v2` instead of force-moving `v1`. List the migration steps below.

<!-- If breaking, what does each caller need to do? -->

## Tag plan after merge

- [ ] `v1.X.Y` (immutable, this commit)
- [ ] Force-move `v1` to this commit
- [ ] Skip both — no tag bump needed (docs / repo-metadata only)

## Test plan

<!--
For non-trivial workflow changes, point one caller at this branch
(`uses: …@feature/<branch>`) and let it run end-to-end before merging.
For YAML-only edits, `python3 -c "import yaml; yaml.safe_load(open('…'))"`
is enough.
-->

- [ ] YAML parses cleanly (`yaml.safe_load`)
- [ ] Validated end-to-end on a caller (link the run): _________
- [ ] N/A — change is docs / repo-metadata only

## Permissions

- [ ] No change to `permissions:` blocks
- [ ] Permissions narrowed (defense-in-depth — describe)
- [ ] Permissions widened (call out which scope and why)
