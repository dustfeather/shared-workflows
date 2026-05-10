# Contributing

This repo hosts reusable GitHub Actions workflows that are consumed by
every other repo under this account via `uses:
dustfeather/shared-workflows/.github/workflows/<name>.yml@v1`. A change
that lands here ships to every caller on their next workflow run. Treat
the blast radius accordingly.

## Before you change a workflow

- **Know your callers.** A non-trivial change to `publish-chrome.yml`
  affects four browser-extension repos. A change to
  `claude-code-review.yml` or `claude.yml` affects every repo with claude
  review wired up. Skim what they do via
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

- `v1` — moving tag. Every backwards-compatible commit on `main` moves
  this tag forward (`git tag -f v1 && git push --force origin v1`).
  Callers pin to this and pick up improvements automatically.
- `vX.Y.Z` — immutable per-commit tag. Created for archaeology so the
  exact version a caller was on at any point in time can be reconstructed.
- `v2` — only when an input/secret/permission becomes breaking. New
  callers opt in by changing their `uses:` reference.

Patch = wording / comments / log-message tweaks.
Minor = new optional input, new bot in default allowlist, new feature
behind a flag.
Major = required input added, default behavior changed, secret renamed,
permission removed.

## Testing changes locally

YAML must parse cleanly:

```bash
python3 -c "import yaml; \
  [yaml.safe_load(open(f)) for f in [
    '.github/workflows/claude-code-review.yml',
    '.github/workflows/claude.yml',
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
