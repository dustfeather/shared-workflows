# shared-workflows

[![Tag release](https://github.com/dustfeather/shared-workflows/actions/workflows/tag-release.yml/badge.svg)](https://github.com/dustfeather/shared-workflows/actions/workflows/tag-release.yml)

Reusable GitHub Actions workflows for cross-cutting CI across every repo
under this account. One source of truth for Claude Code review, browser-
extension publishing, and anything else that recurs.

## Workflows

### `node-test.yml` — Node.js audit / lint / typecheck / test / build

Reusable workflow that installs dependencies and runs `lint`,
`typecheck`, `test`, and (opt-in) `build` package.json scripts —
skipping any that don't exist with a notice. Auto-detects the package
manager from the lockfile (pnpm > yarn > bun > npm) and supports an
explicit override. Defaults to Node 24.

It also runs a dependency vulnerability audit (`npm audit` / `pnpm
audit` / `yarn audit` / `bun audit`, matched to the detected package
manager) — **on by default**, failing at `audit-level: high` or worse,
so anything gated on this job via `needs:` (a deploy, a publish) won't
ship on a high/critical advisory. Tune with `with: audit-level: …`
(`low`/`moderate`/`high`/`critical`) or turn it off with `with:
run-audit: false`.

Pass `with: run-build: true` for projects (e.g. browser extensions)
where the build itself is the most useful PR-time check; that way the
PR-time `tests.yml` shim subsumes what a separate `build.yml` would
have done.

Optionally pass `secrets: NPM_TOKEN` to authenticate to
registry.npmjs.org for both the install and the audit — see
[Registry authentication](#registry-authentication-npm_token).

### `python-test.yml` — Python ruff + pytest

Reusable workflow for ruff lint, ruff format check, and pytest. Defaults
to Python 3.14.

### `publish-chrome.yml` — Chrome Web Store publish

Reusable workflow for releasing browser extensions to the Chrome Web
Store. Handles 429 + 5xx retries (with `Retry-After` honored), parses
`uploadState` and `status[]` body fields (HTTP 200 + `uploadState:
FAILURE` is a real silent-failure mode in naive code), staggers parallel
releases deterministically by repo hash, and skip-with-warning when
secrets are missing.

### `publish-firefox.yml` — Mozilla Add-ons (AMO) publish

Same shape as Chrome but for AMO. Honors the actual `Retry-After` HTTP
header (the older `.retry_after` JSON-body pattern is wrong), uses the
multipart-split pattern for `release_notes` (AMO rejects translatable
fields combined with multipart source uploads), and the same
deterministic stagger.

### `dependabot-auto-merge.yml` — auto-merge Dependabot PRs

Reusable workflow that merges a Dependabot PR after Claude (or any
trusted reviewer) approves it. Two modes:

- `mode: auto` (default) — calls `gh pr merge --auto`. Pair with a
  `pull_request_review` trigger in your shim. Requires GitHub's
  auto-merge feature (Pro+ for private repos, Free only for public).
- `mode: direct` — calls `gh pr merge` immediately after CI succeeds.
  Pair with a `workflow_run` trigger watching your CI workflow. For
  Free private repos where `--auto` isn't available (see
  `github-private-repo-auto-merge-workaround` skill).

Auto-detects the merge method from the repo's allowed methods (priority:
squash > rebase > merge); override via `merge-method:`.

### Recommended PR-checks shape

For the cleanest gate (review skipped if tests/build/CodeQL fail, and
in-flight reviews cancelled when the PR closes/merges), each caller
combines `node-test.yml` + `claude-code-review.yml` into one workflow
with `needs:` ordering, the `closed` trigger, and per-job `if:` gates:

```yaml
name: PR checks
on:
  pull_request:
    # `closed` is here so a merge fires a same-concurrency-group run that
    # cancels the in-flight one (cancel-in-progress: true). The new run
    # itself does no work — both jobs skip via the if: gate below.
    types: [opened, synchronize, reopened, closed]
    paths-ignore:
      - '.github/workflows/pr-checks.yml'

permissions: { contents: read }
concurrency:
  group: pr-checks-${{ github.ref }}
  cancel-in-progress: true

jobs:
  tests:
    if: github.event.action != 'closed'
    permissions: { contents: read }
    uses: dustfeather/shared-workflows/.github/workflows/node-test.yml@v4
    with:
      run-build: true   # extension repos / projects with a meaningful build script
  review:
    if: github.event.action != 'closed'
    needs: tests   # review skips if tests fail
    permissions:
      contents: read
      pull-requests: write
      issues: read
      id-token: write
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v4
    secrets: inherit  # use explicit pass for cross-owner — see "Cross-owner callers"
```

`needs: tests` gates review on tests/build passing. The central review
workflow itself adds a cheap bash gate that skips when CodeQL has
already failed (no agent spin-up, no Claude tokens) — treating
IN_PROGRESS as "proceed" so reviews aren't blocked waiting for CodeQL.
Adding `closed` to the trigger types + the `if: != 'closed'` gates on
each job means a PR being merged or closed mid-review cancels the
in-flight run via the shared concurrency group — no Claude tokens
spent on a review whose PR is no longer open.

### `claude-code-review.yml` — automatic PR review

Runs Claude Code Action on every PR (after a trusted-actor / allowed-bot
gate). Uses a structured prompt that splits dependabot bumps from human PRs:

- **Dependabot track**: classifies the bump (patch/minor/major), checks the
  changelog for breaking changes, approves or requests changes via
  `gh pr review` based on the rules.
- **Standard track**: leaves inline comments + a top-level summary; never
  auto-approves human PRs.

### `claude.yml` — interactive `@claude` mention

Picks up `@claude` mentions in issues, issue comments, PR review comments,
and PR reviews from a trusted actor and hands the conversation to Claude
Code Action.

### `deploy-cloudflare.yml` — deploy one Worker with wrangler

Command-shaped rather than framework-shaped: it takes shell strings
(`build-command`, `pre-deploy-command`, `deploy-command`) instead of detecting
whether the caller is a plain Worker, an OpenNext Next.js app or something
else. One job deploys one Worker; a repo with two Workers calls it twice and
orders them with `needs:`.

The step order is fixed and is the part that matters:

```
install → build → pre-deploy → budget gate → deploy
  → secrets check → startup gate → cron check → verify
```

`pre-deploy-command` (migrations, publishing reference data) runs *before* the
deploy on purpose. A Worker whose schema or KV is not there yet does not fail
to start — it answers wrongly, which reads as an application bug rather than a
deploy that ran out of order.

Two budget gates, and they sit on opposite sides of the deploy because
wrangler reports the two numbers at different times:

| Gate | Input | When | Prevents a bad deploy? |
| --- | --- | --- | --- |
| Bundle gzip size | `max-gzip-kib` | before, via `--dry-run` | **yes** |
| Worker startup time | `max-startup-ms` | after the upload | no — only the upload measures it |

The startup gate cannot block the version that tripped it, because
`--dry-run` never reports a startup time. What it does is make the *next*
merge fail loudly instead of letting cold-start creep run until it hits the
hard 1 s ceiling and deploys start dying with
`10021 Script startup exceeded CPU time limit`. Both gates default to `0`,
which disables them.

`verify-url` polls the deployed Worker until it answers 2xx (12 attempts, 5 s
apart by default). Skipping it is allowed and is a choice to deploy without
checking.

Secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are both required.

#### Verifying a hostname behind Cloudflare Access

A hostname fronted by an Access application answers an unauthenticated probe
with a `302` to `<team>.cloudflareaccess.com`, never a 2xx. `verify-url` then
burns every attempt and fails a deploy that actually succeeded, taking the
downstream jobs with it.

Pass an Access **service token** and the verify step sends it as the
`CF-Access-Client-Id` / `CF-Access-Client-Secret` headers:

```yaml
deploy-api:
  uses: dustfeather/shared-workflows/.github/workflows/deploy-cloudflare.yml@v4
  with:
    working-dir: apps/api
    verify-url: https://gated.example.com/api/v1/health
    verify-requires-access: true
  secrets:
    CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    CF_ACCESS_CLIENT_ID: ${{ secrets.CF_ACCESS_CLIENT_ID }}
    CF_ACCESS_CLIENT_SECRET: ${{ secrets.CF_ACCESS_CLIENT_SECRET }}
```

- The token must be attached to a **`non_identity`** policy on that
  application. Access ignores a service token that no policy admits and keeps
  redirecting.
- Both halves must be non-empty or **neither** is sent. Omitting them keeps the
  prior behaviour exactly: an unauthenticated probe.
- `verify-requires-access: true` turns the silent fallback into a failure. An
  unset or misnamed secret interpolates to the empty string, so without this
  flag a typo in the caller's `secrets:` block looks identical to "this caller
  has no Access in front of it" — and the run fails much later, claiming the
  Worker is not serving.
- When the probe still lands on `cloudflareaccess.com`, the error says whether a
  token was sent (Access **rejected** it) or not (the URL is **gated** and no
  token was supplied) instead of blaming the Worker.
- Headers travel through the step's positional parameters; the values are never
  interpolated into a command string and never echoed.

#### Cron-only Workers — `expect-crons`

A Worker whose only entrypoint is `scheduled()` has no HTTP surface, so
`verify-url` has nothing to curl and every other gate in the workflow runs
before or beside the schedules rather than on them. Wrangler makes that gap
easy to fall into:

- an absent `triggers` block is a **no-op, not an error** — the crons already
  deployed are left in place, so a block under the wrong environment key, or in
  a config file wrangler never read, uploads clean with zero schedules and zero
  warnings;
- `wrangler versions upload` **does not apply triggers at all**. A caller that
  overrides `deploy-command` with it gets its secrets and no crons, and needs a
  follow-up `wrangler triggers deploy`.

Either way the deploy is green and the failure only shows up an hour or a day
later as "the job never ran", with no failed run to point at.

`expect-crons` closes that. Set it to the comma-separated expressions the deploy
must leave registered, and after the upload the job reads
`GET /accounts/{id}/workers/scripts/{name}/schedules` and fails naming any that
are missing. It asserts the end state on the account rather than parsing
wrangler's output, so it also covers the `versions upload` path — where the log
would legitimately never mention a schedule. No new credential: the endpoint
takes `Workers Scripts Read`, which `CLOUDFLARE_API_TOKEN` already exceeds by
being able to deploy.

Empty (the default) skips the check, so it is non-breaking for existing callers.
The script name is resolved from the deploy output, then from `name` in the
wrangler config; `worker-name` overrides it if both fail.

```yaml
deploy-cron-worker:
  uses: dustfeather/shared-workflows/.github/workflows/deploy-cloudflare.yml@v4
  with:
    working-dir: workers/gw2roi
    expect-crons: 0 * * * *
  secrets:
    CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
```

#### Runtime secrets — `WORKER_SECRETS`

Optional. Publishes the caller's GitHub repo secrets onto the Worker as part of
the deploy, so the credentials a repo holds and the credentials its Worker runs
with cannot drift apart.

They travel via wrangler's `--secrets-file`, which attaches them **to the
version being deployed** — not as a follow-up `wrangler secret bulk`. A bulk
call after the deploy would publish a *second* version, leaving a window where
the new code runs against the old credentials, and it requires the Worker to
already exist, which is false on a first-ever deploy.

- **Additive.** Dropping a key from `WORKER_SECRETS` does not delete it from the
  Worker. Removing a secret is still a deliberate `wrangler secret delete`.
- **`KEY=VALUE` lines are the form to use.** Wrangler tries JSON first and falls
  back to dotenv; the JSON form makes the caller responsible for escaping every
  value. Values must be single-line in either form.
- **Names only are logged**, and the staged file is shredded after the deploy.
- An unparseable payload **fails the job**. Wrangler silently no-ops on input it
  cannot read, which would otherwise deploy a Worker with no secrets and report
  success. After deploying, the job re-reads `wrangler secret list` and fails if
  any staged name is absent.

```yaml
deploy-api:
  needs: test
  uses: dustfeather/shared-workflows/.github/workflows/deploy-cloudflare.yml@v4
  with:
    working-dir: apps/api
    install-dir: .
    runner: arc-df-my-repo
    pre-deploy-command: npx wrangler d1 migrations apply my-db --remote
    max-gzip-kib: 600
    max-startup-ms: 400
    verify-url: https://example.com/api/v1/health
  secrets:
    CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    WORKER_SECRETS: |
      SESSION_SECRET=${{ secrets.SESSION_SECRET }}
      STRIPE_SECRET_KEY=${{ secrets.STRIPE_SECRET_KEY }}
```

### `deploy-k8s.yml` — deploy one app to the in-cluster k3s

The sibling of `deploy-cloudflare.yml`, and command-shaped for the same
reason: the callers it was derived from deploy a Deployment, a StatefulSet, a
CronJob and a bare Service. There is no single shape to detect, so it takes
manifest paths and shell strings.

The step order is fixed:

```
build+push → namespace → pull secret → app secrets → prune
  → apply → rollout → verify → show
```

Secrets are materialised **before** the apply on purpose. A Deployment whose
Secret does not exist yet does not fail the apply — the pod sits in
`CreateContainerConfigError` and `rollout status` times out with no hint why,
which reads as a broken image rather than a missing key. The ghcr pull Secret
comes first for the same reason: without it the pod lands in `ImagePullBackOff`
and the rollout times out identically.

**Auth is the runner pod's own ServiceAccount.** There is no `KUBECONFIG`
input. Every caller runs on an ARC scale set inside the target cluster, where
kubectl picks the token up with no configuration — so `runner:` must name a
scale set in that cluster. A GitHub-hosted runner cannot use this workflow.

**This workflow does not build images**, and that is a constraint rather than
an omission. A called workflow may not request more `GITHUB_TOKEN` permission
than the calling job grants, and `permissions:` accepts no expressions — so a
workflow asking for `packages: write` in order to build would break every
caller that grants least privilege, whether or not that caller builds.

The break is also unusually nasty: the run dies at validation with
`startup_failure`, **zero jobs and no logs**. GitHub's explanation ("the nested
job is requesting `packages: write`, but is only allowed `packages: none`")
appears in the web UI only and is absent from the REST and GraphQL APIs, so
`gh run view --log-failed` returns nothing at all.

So building lives in `build-push-image.yml`. A caller that needs one runs it
first and passes the image through:

```yaml
build:
  permissions:
    contents: read
    packages: write        # only this job needs it
  uses: dustfeather/shared-workflows/.github/workflows/build-push-image.yml@v4

deploy:
  needs: build
  permissions:
    contents: read         # deploy never needs more
  uses: dustfeather/shared-workflows/.github/workflows/deploy-k8s.yml@v4
  with:
    image: ${{ needs.build.outputs.image }}
```

Every input defaults to behaviour-preserving, so a minimal call is namespace +
manifests. The ones worth knowing:

| Input | Default | Note |
| --- | --- | --- |
| `namespace` | *required* | every kubectl call is `-n` scoped to it |
| `ensure-namespace` | `get` | `get` asserts, `create` creates, `none` skips. `get` is the default because a per-scale-set runner SA usually cannot create namespaces at all, and `create` would trade a clear error for an RBAC denial |
| `setup-kubectl` | `false` | the pre-baked runner image already ships kubectl and envsubst; turn on for a scale set still on the stock `actions-runner` image |
| `image` | derived | full reference exposed to `templated-manifests` as the image variable. Empty derives `ghcr.io/<owner>/<repo>:<sha>`; pass `build-push-image.yml`'s output when you built one |
| `manifests` | `""` | applied verbatim, in order |
| `templated-manifests` | `""` | piped through envsubst first, applied after `manifests` |
| `envsubst-vars` | image var only | restricts substitution. Unrestricted envsubst also eats shell-looking tokens that belong to the manifest — a snippet in an exec probe, an arg the container expects at runtime — silently replacing them with an empty string |
| `rollout-targets` | `""` | empty skips the wait; a CronJob-only deploy has no rollout to watch |
| `verify-command` | `""` | non-zero exit fails the deploy |
| `show-command` | `kubectl get pods,svc` | runs with `if: always()` |

`show-command` running under `always()` is not cosmetic. Without it the
diagnostics step is skipped *exactly* when the rollout failed, so the log holds
the timeout and nothing that explains it. The step also dumps recent namespace
events, which is where `ImagePullBackOff` and missing-Secret failures actually
show up. One such failure took commit archaeology to reconstruct because the
caller's own `Show result` step had no `always()`.

Build-and-deploy caller — two jobs, and only the first is privileged:

```yaml
build:
  needs: test
  permissions:
    contents: read
    packages: write
  uses: dustfeather/shared-workflows/.github/workflows/build-push-image.yml@v4
  with:
    runner: arc-itguys-ro-apps-page

deploy:
  needs: build
  permissions:
    contents: read
  uses: dustfeather/shared-workflows/.github/workflows/deploy-k8s.yml@v4
  with:
    namespace: apps-page
    runner: arc-itguys-ro-apps-page
    setup-kubectl: true
    ensure-namespace: none
    image: ${{ needs.build.outputs.image }}
    manifests: deploy/service.yaml
    templated-manifests: deploy/deployment.yaml
    rollout-targets: deployment/apps-page
    rollout-timeout: 120s
```

No-build caller with a real post-deploy assertion:

```yaml
deploy:
  uses: dustfeather/shared-workflows/.github/workflows/deploy-k8s.yml@v4
  with:
    namespace: dosar-rapid-render
    runner: arc-df-dosar-rapid
    manifests: |
      deploy/render/service.yaml
      deploy/render/deployment.yaml
    rollout-targets: deployment/render
    rollout-timeout: 300s
    verify-command: kubectl exec deploy/render -- fc-list ":charset=0400" | grep -q .
```

#### Application secrets — `SECRET_LITERALS`

Optional, and the same idea as `WORKER_SECRETS`: the caller composes
`KEY=VALUE` lines from its own GitHub secrets, and the workflow upserts them
as one generic Secret named by `secret-name`. Applied via a client-side dry
run piped into `apply`, so it is idempotent. Only the key **names** are ever
logged.

```yaml
  with:
    namespace: social-update
    secret-name: social-update-secrets
  secrets:
    SECRET_LITERALS: |
      CLAUDE_CODE_OAUTH_TOKEN=${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

`GHCR_PULL_PAT` is only needed when `ghcr-pull-secret` is set, i.e. the
namespace pulls a **private** ghcr image. It must be a classic PAT with
`read:packages` — a GitHub App installation token cannot be granted access to
a user-owned package and pulls 403 with a token that is otherwise valid.
Making the package public removes the need entirely.

All three secrets must be passed **explicitly** by cross-owner (`ITGuys-RO/*`)
callers; `secrets: inherit` does not reliably carry org-level
selected-visibility secrets across the owner boundary.

#### Several Secrets — `SECRETS_MANIFEST`

For a namespace whose Secrets do not collapse into one. Each Secret is a
`[name]` header followed by its own `KEY=VALUE` lines; blank lines and `#`
comments are ignored, only the key **names** are logged, and each Secret is
upserted through the same idempotent dry-run-into-apply as `SECRET_LITERALS`.

```yaml
  with:
    namespace: trading
  secrets:
    SECRETS_MANIFEST: |
      [gw2-api-key]
      ARENA_NET_KEY=${{ secrets.ARENA_NET_KEY }}

      [gw2-postgres-creds]
      POSTGRES_USER=gw2
      POSTGRES_DB=gw2
      POSTGRES_PASSWORD=${{ secrets.PG_PASSWORD }}
      PGUSER=gw2
      PGDATABASE=gw2
      PGPASSWORD=${{ secrets.PG_PASSWORD }}
```

Note the second Secret above mixes a password with the non-secret user and
database names that travel with it. That is the point rather than an
accident: splitting it so the non-secret half could arrive as an input would
hand the workload two Secrets to mount where the manifest expects one.

There is **no input** naming these Secrets — the presence of the secret is the
switch. An input listing them would be a second place for the same list to be
written and a chance for the two to disagree, and it would reveal nothing that
is not already logged.

**An empty value is an error**, unless `allow-empty-secret-values: true`. An
unset GitHub secret interpolates to the empty string, so without that check a
caller who forgot to set one gets a Secret with the right key and an empty
value — the apply succeeds, the pod starts, and the workload authenticates
with `""` against whatever it talks to. Every hand-written deploy this
workflow replaced opened with a `test -n` guard for that reason, and this is
where the guard went. `SECRET_LITERALS` is deliberately *not* covered: it has
always permitted empty values, so tightening it would be a breaking change
rather than a new default.

`SECRETS_MANIFEST` and `secret-name` are independent; use either, both, or
neither. If a caller composes it entirely from GitHub secrets that are all
unset, the step fails with "no `[secret-name]` section could be parsed" rather
than silently creating nothing.

#### What not to migrate

A `workflow_call` hands the caller a **job**, not a step, so a repo needing
its own steps *interleaved* with these cannot use it without smuggling them in
as shell strings. That constraint is what the `prune-command`,
`post-apply-command`, `verify-command` and `show-command` inputs exist for —
and where a caller's extra work does not fit one of those slots, the answer is
a **second job in the caller's own repo**, not a wider input surface here.

- **A deploy with app-specific observability steps** — Grafana dashboard and
  alert provisioning, deliberately soft-failing — would drag three
  single-caller secrets into this workflow's surface. Split it: this workflow
  for image + secrets + apply, then a `needs: deploy` job in the caller for
  the rest, keeping those secrets local to the repo that owns them.
  `ITGuys-RO/invest` is the worked example.
- **A Helm-based pipeline** is a different problem — the unit of change is a
  release, not a file. That is [`deploy-helm.yml`](#deploy-helmyml--install-or-upgrade-one-helm-release) below.
- **A caller authenticating with a `KUBECONFIG` secret** cannot use this
  workflow at all: auth is the runner pod's ServiceAccount and there is no
  kubeconfig input. Only `ITGuys-RO/ollama-k3s` was ever in that shape, and
  its namespace is torn down, so it stays un-migrated rather than justifying
  an unused credential path. Reviving it means adding an optional
  `KUBECONFIG` secret here — a backwards-compatible minor bump.

Two cases that *used* to be listed here are now handled: two distinct Secrets
(see `SECRETS_MANIFEST` above), and Helm (see below).

### `deploy-helm.yml` — install or upgrade one Helm release

The Helm sibling of `deploy-k8s.yml`, and separate from it for a reason worth
stating: a Helm pipeline is not a `kubectl apply` pipeline with extra flags.
The unit of change is a release rather than a file, the ordering constraints
are Helm's, and the useful pre-flight — `helm lint` plus `helm template` piped
into a dry-run apply — has no counterpart in a manifest apply. Folding it into
`deploy-k8s.yml` would have doubled that workflow's inputs for every caller
that never installs a chart.

Auth, runner requirements and the `contents: read` permission ceiling are
identical to `deploy-k8s.yml`. It has **no secrets**: the caller it was
derived from pre-creates its Secrets in shapes no `KEY=VALUE` blob can express
— a `patch --merge` into a *different* namespace, a `--from-file` SSH key —
and references them from values by name.

The step order is fixed:

```
repo add/update → lint → template check → upgrade
  → post-upgrade → rollout → verify → show
```

**Validation and deployment are the same workflow, called twice.** A PR check
calls it with `upgrade: false` and writes nothing; a deploy calls it with the
defaults. That is what stops the gate and the deploy drifting apart — a
validation step that lints a different chart version than the one being
installed is worse than no gate, because it is green.

| Input | Default | Note |
| --- | --- | --- |
| `namespace`, `release`, `chart` | *required* | `chart` is `<repo>/<chart>`, an `oci://` reference, or a directory in the checkout |
| `chart-version` | `""` | empty resolves to whatever the repository serves today — pin it. Warns unless `chart` is a directory, which the commit already pins |
| `repo-url` | `""` | added and updated before resolving `chart`; `repo-name` defaults to `chart`'s first path segment |
| `values-files` | `""` | newline-separated `-f` paths relative to `working-dir`, later files winning, checked for existence here so a `working-dir` mistake names itself |
| `set-literals` | `""` | `--set-string` lines, **non-secret only** — they land in the run log and in `helm get values` |
| `setup-helm` | `true` | on by default, unlike `setup-kubectl`: the pre-baked runner image ships kubectl but not helm |
| `lint` | `false` | a remote chart is pulled and untarred first, because helm v4 dropped `helm lint --version` on a remote reference |
| `template-check` | `false` | render + `kubectl apply --dry-run=client`. **Not offline** — see below |
| `upgrade` | `true` | set false for a pure validation gate |
| `atomic` | `false` | rolls back on failure. Implies `--wait`, so `helm-timeout` becomes the readiness budget |
| `rollout-targets` | `""` | separate from `atomic` because `--atomic` already waited |
| `create-namespace` | `false` | off because a per-scale-set SA usually cannot create namespaces, and on would trade a clear "not found" for an RBAC denial |

`template-check` is **not** an offline parse. `kubectl apply
--dry-run=client` still GETs the live object to compute the merge patch, so it
needs a reachable API server and a namespace the runner's ServiceAccount can
read. It is a schema-and-RBAC gate.

`--atomic` is also what makes the `always()` show step matter more here than
in `deploy-k8s.yml`: by the time anyone reads the log, Helm has already rolled
the release back, so the failed objects are gone and the dumped events are the
only surviving record of why it never went ready. The step prints
`helm history` alongside them for the same reason.

Deploy caller — a pinned chart, values from the repo, atomic:

```yaml
helm:
  permissions:
    contents: read
  uses: dustfeather/shared-workflows/.github/workflows/deploy-helm.yml@v4
  with:
    namespace: nextcloud
    release: nextcloud
    chart: nextcloud/nextcloud
    chart-version: "9.1.0"
    repo-url: https://nextcloud.github.io/helm/
    values-files: helm/nextcloud-values.yaml
    runner: arc-itguys-ro-nextcloud
    setup-kubectl: true
    atomic: true
    helm-timeout: 5m
    rollout-targets: deploy/nextcloud
    rollout-timeout: 5m
```

The same workflow as a PR gate — one word different, and nothing is written:

```yaml
validate-chart:
  uses: dustfeather/shared-workflows/.github/workflows/deploy-helm.yml@v4
  with:
    namespace: nextcloud
    release: nextcloud
    chart: nextcloud/nextcloud
    chart-version: "9.1.0"
    repo-url: https://nextcloud.github.io/helm/
    values-files: helm/nextcloud-values.yaml
    runner: arc-itguys-ro-nextcloud
    setup-kubectl: true
    lint: true
    template-check: true
    upgrade: false
```

## Usage

Each calling repo has a tiny shim. The shim handles trigger configuration
(which can't be inside a `workflow_call` workflow); the central workflow
handles all the logic.

### `.github/workflows/claude-code-review.yml` (shim)

```yaml
name: Claude Code Review
on:
  pull_request:
    types: [opened, synchronize]
    # Avoid the claude-code-action self-modify validation guard tripping
    # on PRs that change this very file. Keep the path narrow — broader
    # filters like '.github/**' would also skip review on workflow PRs
    # that are unrelated to this file.
    paths-ignore:
      - '.github/workflows/claude-code-review.yml'

jobs:
  review:
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v4
    secrets: inherit
```

### `.github/workflows/claude.yml` (shim)

```yaml
name: Claude Code
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened, assigned]
  pull_request_review:
    types: [submitted]

jobs:
  claude:
    uses: dustfeather/shared-workflows/.github/workflows/claude.yml@v4
    secrets: inherit
```

## Secrets

### Registry authentication — `NPM_TOKEN`

`node-test.yml`, `deploy-cloudflare.yml` and `release-extension.yml` accept an
optional `NPM_TOKEN` and write it into `~/.npmrc` before any step touches
registry.npmjs.org, so installs and audits run authenticated rather than
anonymously.

Mint it on npmjs.com as a **granular access token**, **read-only**, with **no
package, scope or organization grants** — nothing here publishes, and nothing
here installs a private package, so any grant beyond that is blast radius for
no gain. Classic tokens were removed in November 2025, so granular is the only
kind available; they carry a mandatory expiry and will need rotating.

The token is written as a **literal `${NPM_TOKEN}` reference, not its value**:

```
//registry.npmjs.org/:_authToken=${NPM_TOKEN}
```

npm, pnpm and yarn all expand env references when they *read* `.npmrc`, so the
secret is resolved at use time and never lands on disk, in a restored cache
layer or in an uploaded artifact.

The step is **guarded on a non-empty value and the secret is `required: false`**.
An unset or misnamed secret interpolates to the empty string, which is
indistinguishable from "not configured" — and writing an empty `_authToken`
turns every registry call into `ENEEDAUTH`, which is strictly worse than
anonymous. So the step skips instead, and a caller that has not been wired up
keeps working unchanged.

Note this is a *hardening* measure, not a fix for a broken audit: as of
2026-09-04 both `/-/npm/v1/security/audits/quick` and
`/-/npm/v1/security/advisories/bulk` answer HTTP 200 unauthenticated. If the
audit step fails, check for the hang described in issue #24 before suspecting
credentials.

### Claude — `CLAUDE_CODE_OAUTH_TOKEN`

Each calling repo must have `CLAUDE_CODE_OAUTH_TOKEN` set — either as a
repository secret or inherited from an organization secret. The shim then
needs to pass it through.

**Reusable workflows run in the caller's context with the caller's secrets.**
The central repo's secrets are not visible to callers; each caller pays for
its own Claude usage with its own token.

### Same-owner callers — `secrets: inherit` works

When the calling repo is owned by the same account as `shared-workflows`
(i.e. another `dustfeather/*` repo), the shim can use:

```yaml
jobs:
  review:
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v4
    secrets: inherit
```

`inherit` carries every secret the caller can see — both repo-level and
org-level — into the called workflow.

### Cross-owner callers — explicit secret pass required

When the caller lives under a **different owner** (e.g. an `ITGuys-RO/*`
repo calling `dustfeather/shared-workflows`), `secrets: inherit` does NOT
reliably pass **org-level secrets with "selected" visibility** across the
owner boundary. You'll see this error from the called workflow's first
job:

```
Error when evaluating 'secrets'. .github/workflows/claude-code-review.yml
(Line: N, Col: M): Secret CLAUDE_CODE_OAUTH_TOKEN is required, but not
provided while calling.
```

The fix is to pass the secret explicitly. The reference is resolved in
the **caller's** context (where the org secret IS visible to the repo)
and forwarded as a named secret:

```yaml
jobs:
  review:
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v4
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

This works in both cases (same-owner and cross-owner), so when in doubt,
prefer the explicit form. `inherit` is just a convenience shortcut for
the same-owner case.

## Configuration

Both workflows accept optional `with:` inputs:

| Input | Workflow | Default | Purpose |
|-------|----------|---------|---------|
| `trusted-actors` | both | `dustfeather` | Comma-separated GitHub actors allowed to trigger |
| `allowed-bots` | review | `dependabot[bot]` | Bots whose PRs trigger reviews |
| `show-full-output` | review | `true` | Surface every tool call in the action log |

Example (private repo where another teammate should also be able to invoke):

```yaml
jobs:
  review:
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v4
    with:
      trusted-actors: "dustfeather,collaborator-handle"
    secrets: inherit
```

## Versioning

Callers pin to `@v4` (the current moving major-version tag, GitHub Actions
convention). Every push to `main` is auto-tagged by
`.github/workflows/tag-release.yml`: by default it bumps the **patch**
component by one (wrapping at 100 into minor; minor grows without bound),
and it re-points the floating `vN` tag at the new commit. **major is never
bumped automatically** — a major bump can break `@vN` callers, so it only
happens when you ask for it. To request a larger bump for a given commit,
put a token in that commit's subject line — `#minor` or `#major` (largest
wins; `#patch` is the explicit form of the default).

Pick the bump by what changes for **callers** of these reusable workflows:

| Bump | Token | When | Caller impact |
|---|---|---|---|
| **patch** | _(default, or `#patch`)_ | Bug fix in a workflow; doc-only change; internal refactor; bumping an action used *inside* a workflow with no interface change; log/wording tweaks. | None — `@v4` callers get it automatically, nothing to do. |
| **minor** | `#minor` | Backwards-compatible feature: a new **optional** input (with a default), a brand-new workflow, a new opt-in job/step, broadened behavior that callers don't have to react to. | None required; new capability is available if they want it. |
| **major** | `#major` | Breaking change to a workflow's contract: removing/renaming an input or secret, adding a **required** input, changing a default in a way callers must account for, requiring callers to grant new permissions, removing a workflow, renaming a job output. | **Callers on `@v4` would break.** A `#major` bump rolls the version to `vN+1`; update the README usage examples and tell callers to re-pin to `@vN+1`. |

Rule of thumb: if a caller's shim workflow could keep working untouched →
patch or minor; if it couldn't → major. When unsure, prefer the larger bump.

Note: the token match is a fixed-string search of the commit subject, so
don't write `#major`/`#minor` verbatim in a commit message unless you mean
it (e.g. say "the major token" rather than the literal string).

## Extension publishing usage

For each browser-extension repo's `release.yml` (after a `build` job that
uploads an artifact named `extensions` containing the packaged `.zip`,
`.xpi`, source `.zip`, and `release-notes.txt`):

```yaml
publish-chrome:
  needs: build
  uses: dustfeather/shared-workflows/.github/workflows/publish-chrome.yml@v4
  with:
    zip-name: my-ext-chrome-${{ needs.build.outputs.tag }}.zip
  secrets: inherit

publish-firefox:
  needs: build
  uses: dustfeather/shared-workflows/.github/workflows/publish-firefox.yml@v4
  with:
    xpi-name: my-ext-firefox-${{ needs.build.outputs.tag }}.xpi
    source-name: source-${{ needs.build.outputs.tag }}.zip
    addon-id: my-ext@dustfeather
  secrets: inherit
```

`secrets: inherit` passes through `CHROME_*` and `AMO_*` secrets from the
calling repo. If a repo doesn't have a given store's secrets configured,
the matching publish job emits a `::warning::` and exits 0 cleanly.

## History

This repo subsumes the earlier `dustfeather/extension-workflows` (which
was extension-publish-only). All publish workflows now live here so
there's one home for shared CI.
