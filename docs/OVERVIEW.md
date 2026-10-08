# shared-workflows

## Goal

One source of truth for reusable GitHub Actions workflows plus the self-hosted runner container image used across every repo under the account — Claude Code review, browser-extension publishing, and the shared Node/Python CI gates. Callers keep a tiny shim; the central repo holds all the logic.

## Stack

- **GitHub Actions** — reusable workflows (`workflow_call`), pinned by callers to the floating `@v1` tag.
- **Runner container images** — a pre-baked derivative of `ghcr.io/actions/actions-runner`, published to `ghcr.io/dustfeather/actions-runner-claude`.
- **k8s (ARC)** — runner pods are [Actions Runner Controller](https://github.com/actions/actions-runner-controller) scale sets on the k3s cluster; image override + GHCR pull-credential glue live in `k8s/`.

## Repo

`dustfeather/shared-workflows` (confirmed via `git remote -v` → `git@github.com:dustfeather/shared-workflows.git`).

Reusable workflows under `.github/workflows/`:

- `node-test.yml` — Node audit / lint / typecheck / test / opt-in build (auto-detects package manager, defaults Node 24).
- `python-test.yml` — ruff lint + format check + pytest (defaults Python 3.14).
- `claude-code-review.yml` — automatic PR review (Claude Code Action; splits Dependabot vs human PR tracks).
- `claude.yml` — interactive `@claude` mention handler for issues/PR comments/reviews.
- `dependabot-auto-merge.yml` — auto-merge Dependabot PRs after approval (`auto`/`direct` modes).
- `publish-chrome.yml` — Chrome Web Store publish.
- `publish-firefox.yml` — Mozilla Add-ons (AMO) publish.
- `tag-release.yml` — auto-tags every push to `main`, re-points the floating `vN` tag (patch by default; `#minor`/`#major` tokens in the commit subject bump larger).
- `runner-image.yml` / `refresher-image.yml` — build + push the runner image and the GHCR-pull refresher image.
- `pr-checks.yml` — the repo's own PR gate.

Other layout: `runner-image/` (the pre-baked runner Dockerfile), `k8s/` (ARC scale-set image override + `ghcr-pull-refresher/` CronJob).

## Deploy

The reusable workflows are **consumed by other repos** via a shim that pins `@v1` and passes secrets (`secrets: inherit` for same-owner `dustfeather/*` callers; explicit `CLAUDE_CODE_OAUTH_TOKEN` pass for cross-owner `ITGuys-RO/*` callers).

The runner images run **IN-CLUSTER** — ARC scale sets on the k3s cluster, with each scale set's Helm release overridden to point its `runner` container at `ghcr.io/dustfeather/actions-runner-claude:latest` (`k8s/runner-scale-set-image-override.yaml`). A `ghcr-pull-refresher` CronJob mints a GHCR pull credential from the `github-app-dustfeather` App every 30 min so the image can stay private.

Runner images hosted on [Homelab](https://github.com/ITGuys-RO/k3s-cluster/blob/main/docs/homelab.md).

## Status

Active. Current work is on branch `feat/standard-track-formal-review` (`claude-code-review.yml` emitting a formal approve / request-changes verdict on the standard track). `main` recently upgraded the review model from claude-opus-4-7 to 4-8.

## Tasks

- [ ] Keep `BUN_VERSION` in `runner-image/Dockerfile` in sync with `anthropics/claude-code-action@v1`'s `setup-bun` pin (currently 1.3.14).
- [ ] Bump `KUBECTL_MINOR` (currently 1.33) when the cluster's API server upgrades.
- [ ] Watch the GitHub App installation-token format change (rolling out from 2026-04-27) — refresher is forward-compatible, but verify no downstream consumer pattern-matches the old `ghs_` token shape.

## Notes

**Consumers** — nearly every repo runs its CI on the k3s ARC runners defined here (own scale set per repo, named `arc-<owner>-<repo>`):

- `ITGuys-RO/*`: [Flotila](https://github.com/ITGuys-RO/fleet-manager/blob/main/docs/product.md) (`fleet-manager`), [itguys.ro](https://github.com/ITGuys-RO/itguys.ro/blob/main/docs/OVERVIEW.md), [invest](https://github.com/ITGuys-RO/invest/blob/main/docs/OVERVIEW.md), [nextcloud](https://github.com/ITGuys-RO/nextcloud/blob/main/docs/OVERVIEW.md), [ollama-k3s](https://github.com/ITGuys-RO/ollama-k3s/blob/main/docs/OVERVIEW.md).
- `dustfeather/*`: [dustfeather](https://github.com/dustfeather/dustfeather/blob/main/docs/OVERVIEW.md) (refresh-badges), fear-greed-telegram-bot, and the [Browser Extensions](https://github.com/dustfeather/filelist-ext/blob/main/docs/extension-group.md) (`filelist-ext`, `series-auto-skip`) which also consume the Chrome/Firefox publish workflows.
- GitHub-hosted (not ARC): `discord-purge`, `uninsta` (`@v1`, `ubuntu-latest`); `device-activity-telegram-bot` has no CI.

This repo subsumes the older extension-publish-only `dustfeather/extension-workflows`.

**Runner image contents** (`actions-runner-claude`, layered on the upstream ARC runner so `runner` user / `run.sh` / `externals/` stay intact):

- `git`, `jq`, `gh`, `unzip`
- `kubectl` (minor-pinned, k8s apt repo) + `gettext-base` (`envsubst`) for workflows that `kubectl apply` to the cluster they run in — capability gated per scale set by RBAC, not by the image.
- Node.js (NodeSource major), `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`.
- Bun pinned to the version `claude-code-action@v1` expects, plus a **pre-warmed Bun install cache** of the action's ~140 runtime deps (cuts the per-job install from ~20 s to ~1–2 s).
- `@anthropic-ai/claude-code` CLI installed globally.
- Python 3 + pip with `jsonschema`, `PyJWT`, `cryptography` globally installed.

Wired into the review workflow via `path-to-bun` / `path-to-claude` so the action skips its own Bun and Claude Code installs.

Area: Software Engineering · [Homelab](https://github.com/ITGuys-RO/k3s-cluster/blob/main/docs/homelab.md) · [CI Runners](https://github.com/ITGuys-RO/k3s-cluster/blob/main/docs/ci-runners.md)

## Log

- **2026-05-31** — Note created from repo scan.
