# Contributing

This repo hosts reusable GitHub Actions workflows that are consumed by
every other repo under this account via `uses:
dustfeather/shared-workflows/.github/workflows/<name>.yml@v6`. A change
that lands here ships to every caller on their next workflow run. Treat
the blast radius accordingly.

## Before you change a workflow

- **Know your callers.** A non-trivial change to `publish-chrome.yml`
  affects four browser-extension repos. A change to `node-test.yml`
  affects every repo with a PR gate wired up. Skim what they do via
  [`gh search code --owner dustfeather --owner ITGuys-RO 'dustfeather/shared-workflows/'`](https://docs.github.com/en/search-github/searching-on-github/searching-code).
- **Backwards compatibility is the default.** Inputs added to a reusable
  workflow must default to a value that preserves prior behavior. Removing
  an input or changing its semantics is a breaking change — bump the
  major (see "Versioning").
- **Test on one caller first.** If a change is non-trivial, point one
  caller at a feature branch (e.g. `@feature/<name>` instead of `@v1`) and
  let it run end-to-end before tagging.

## Versioning

The repo uses GitHub Actions' floating major-tag convention:

- `v6` — moving tag, the current major. `tag-release.yml` re-points it
  automatically on every push to `main`; nobody runs `git tag -f` by hand.
  Callers pin to this and pick up improvements on their next run.
- `vX.Y.Z` — immutable per-commit tag, cut by the same workflow. Created
  for archaeology so the exact version a caller was on at any point in
  time can be reconstructed.
- `v7` — only when an input/secret/permission becomes breaking, and only
  deliberately: the major is never bumped automatically. New callers opt
  in by changing their `uses:` reference. Note that re-pointing only ever
  applies to the CURRENT major, so cutting a new one freezes the old tag
  where it stands.

Bumps larger than a patch are requested with a token, and where it goes
depends on how the change lands:

- **Through a PR -> the PR title.** PRs land with a squash merge, and the
  repo setting `squash_merge_commit_title` is `PR_TITLE` (asserted by
  `scripts/check-workflows.py` under `CHECK_MERGE_SETTINGS=1`), so the PR
  title *is* the subject of the single commit that reaches `main`, however
  many commits the PR has. On GitHub's default `COMMIT_OR_PR_TITLE` a
  single-commit PR would land the commit's own subject instead, which is
  why the setting is asserted rather than assumed.
- **Pushed straight to `main` -> the commit subject.** Nothing replays it,
  so its own subject is the subject.

`tag-release.yml` reads tokens from commit subjects only: deliberately, so
prose merely discussing a bump cannot cut one. A token left on a branch
commit's subject therefore lands in the squash commit's *body*, where the
scan is designed not to look, and the release quietly degrades to a patch.
Put the token in the PR title.

**The mirror hazard.** Because the PR title is the landing subject, a bump
token written there *incidentally* (quoting an error message, naming the
convention in prose, describing what a change does) cuts that release for
real. `tag-release.yml` excludes commit bodies for exactly this reason: prose
discussing a bump once matched itself and cut a spurious minor. A PR title has
no equivalent exclusion, because it is the only channel the token can travel
on, so an incidental token and an intended one are identical text. Write "the
major token" rather than the literal string unless you mean it.

Patch = wording / comments / log-message tweaks.
Minor = new optional input, new feature behind a flag.
Major = required input added, default behavior changed, secret renamed,
permission removed.

## Testing changes locally

YAML must parse cleanly:

```bash
python3 -c "import yaml; \
  [yaml.safe_load(open(f)) for f in [
    '.github/workflows/node-test.yml',
    '.github/workflows/publish-chrome.yml',
    '.github/workflows/publish-firefox.yml',
  ]]; print('YAML OK')"
```

For end-to-end testing of a workflow change, the only real path is
pushing to a feature branch and pointing one caller at it temporarily.
Reusable workflows can't be invoked from the local checkout because
`uses:` resolves through GitHub.

## Style

- 2-space YAML indent throughout.
- Comments belong on the *non-obvious* lines: WHY a field is set this
  way, what surprise it works around. Don't restate what well-named
  inputs already say.
- Sensitive values (secrets, tokens) NEVER appear in YAML — only as
  `${{ secrets.NAME }}` references.

## Permissions

Every workflow declares an explicit top-level `permissions:` block
(typically `contents: read`) and any job that needs more declares its
own at the job level. CodeQL flags missing permissions blocks; we treat
that as a hard error, not a warning.

## Pull requests

Open a PR against `main`. The PR template will prompt for the things
reviewers want to see (callers affected, tag-bump strategy, test
evidence). After merge, tag if needed and force-move `v1`.

## Security issues

See [SECURITY.md](SECURITY.md).
