# Contributing

This repo hosts reusable GitHub Actions workflows that are consumed by
every other repo under this account via `uses:
dustfeather/shared-workflows/.github/workflows/<name>.yml@v6`. A change
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

- **Through a PR → the PR title.** PRs land with a squash merge, and
  `merge-on-approval.yml` passes `--subject "<the PR title>"`, so the PR
  title *is* the subject of the single commit that reaches `main`.

  **Not yet true in this repo itself, and the exception is silent.**
  `pr-merge.yml` is pinned to `@v5`, which has no bump-token guard at all and
  passes no `--subject` — it merges with a bare
  `gh pr merge "$PR_URL" "$method"`. The subject is therefore composed by
  GitHub from `squash_merge_commit_title`, and on the default
  `COMMIT_OR_PR_TITLE` a **single-commit** PR lands the commit's own subject,
  switching to the PR title only from two commits up. So a one-commit PR
  titled `add X input #minor` whose commit subject is `add X input` lands
  `add X input`, `tag-release.yml` finds no token, and a patch is cut where a
  minor was asked for — with nothing checking, because the guard that would
  refuse this ships in v6 and `pr-merge.yml` does not run it yet.
  **Until `pr-merge.yml` is repointed to `@v6` (#43), put the token in BOTH
  the PR title and the head commit subject when working in this repo.**
- **Pushed straight to `main` → the commit subject.** Nothing replays it,
  so its own subject is the subject.

`tag-release.yml` reads tokens from commit subjects only — deliberately, so
prose merely discussing a bump cannot cut one. A token left on a branch
commit's subject therefore lands in the squash commit's *body*, where the
scan is designed not to look. `merge-on-approval.yml` refuses such a merge
rather than letting the release quietly degrade to a patch, and the fix it
names is to edit the PR title: that needs no new commit, so the approving
review still stands. Pushing another commit to carry the token does not
work — its subject becomes body text too.

**The mirror hazard, and why it matters more now.** Because the PR title is
always the landing subject, a bump token written there *incidentally* — quoting
an error message, naming the convention in prose, describing what a change does
— cuts that release for real. `tag-release.yml` excludes commit bodies for
exactly this reason: prose discussing a bump once matched itself and cut a
spurious minor. A PR title has no equivalent exclusion, because under this
convention it is the only channel the token can travel on, so an incidental
token and an intended one are identical text and nothing can tell them apart.
Write "the major token" rather than the literal string unless you mean it. The
merge log states which release the merge will cut before it lands, so check it
if a title mentions versioning at all.

**A PR with more than 250 commits is refused rather than merged, and there is
no way to turn that off.** Deciding the bump means reading the subject of every
commit on the PR, and GitHub's pull-request commits endpoint caps at 250 however
far it is paginated — so a token on commit 251 would be invisible and the
release would quietly degrade to a patch. That is precisely the failure this
guard exists to prevent, so instead of trusting a partial list it compares the
subjects it read against the PR's own `.commits` total, which is uncapped
(measured: 780 on a PR whose listing returns 250), and exits nonzero on any
shortfall.

The refusal is deliberate and it has no opt-out input, which means the
automation simply cannot land such a PR: **merge it by hand, or split it into
smaller ones.** The same comparison also fires when a pagination page fails or
a push lands mid-read, and the message says which of those it can rule out, so
read it before assuming the commit count is the cause. It exits before
`gh pr merge` is called, so a refusal can never strand a merged-but-untagged
commit — the failure mode it is most important not to have.

The subject is passed explicitly rather than left to the repo's
`squash_merge_commit_title` setting on purpose. GitHub's default there,
`COMMIT_OR_PR_TITLE`, uses the commit's own subject on a single-commit PR
and switches to the PR title only from two commits up. Without the flag the
correct advice would depend on a per-repo setting nobody audits and on how
many commits the PR happens to have, and the only rule that always held
would be "put the token in both places".

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
