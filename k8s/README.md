# Cluster manifests for the pre-baked ARC runner image

Cluster-side glue for `ghcr.io/dustfeather/actions-runner-claude`. Kept
in this repo because the image's lifecycle (build, version, consume)
lives here too.

## What's inside

- `ghcr-pull-refresher/` — CronJob that mints a GHCR pull credential
  from the `github-app-dustfeather` App every 30 minutes and writes it
  to `arc-runners/ghcr-pull`. Lets the runner image stay **private**
  (the App, not a human PAT, owns the credential lifecycle).
- `runner-scale-set-image-override.yaml` — Helm values overlay that
  points an `gha-runner-scale-set` release at the pre-baked image and
  attaches the `ghcr-pull` secret.

## Prerequisite — GitHub App permission

The reused App is `github-app-dustfeather` (App ID 3671815). For the
refresher to mint a token that ghcr.io accepts, the App must have:

- **Repository permissions** → **Packages**: **Read-only**, OR
- **Organization/user permissions** → **Packages**: **Read-only** (this
  is the relevant one for a user-owned package like
  `ghcr.io/dustfeather/actions-runner-claude`).

Add via https://github.com/settings/apps/<app-slug> →
**Permissions & events** → **User permissions** → **Packages: Read-only**
→ Save. GitHub then surfaces "Accept new permissions" for the App's
installation on the dustfeather user account — accept that, otherwise
the new scope is granted on paper but not on the live installation
token.

Verify after acceptance (no installable check; do it empirically):

```sh
kubectl -n arc-runners create job --from=cronjob/ghcr-pull-refresher \
    ghcr-pull-verify -n arc-runners
kubectl -n arc-runners logs job/ghcr-pull-verify
# then decode the resulting secret and try a pull:
auth=$(kubectl -n arc-runners get secret ghcr-pull \
    -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d \
    | jq -r '.auths["ghcr.io"].auth' | base64 -d | cut -d: -f2-)
curl -fsSL -u "x-access-token:$auth" \
    https://ghcr.io/v2/dustfeather/actions-runner-claude/manifests/latest \
    -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
    -o /dev/null && echo OK
kubectl -n arc-runners delete job ghcr-pull-verify
```

A 200 means the App has the scope. A 403 means the permission was added
but not accepted on the installation — re-check the App page.

## Order of operations

1. Confirm `Packages: Read` on the App (above).
2. `kubectl apply -f k8s/ghcr-pull-refresher/` to land the refresher.
3. `kubectl apply -f k8s/ghcr-pull-refresher/99-bootstrap-job.yaml` to
   populate `ghcr-pull` immediately. Wait for it to complete:
   `kubectl -n arc-runners wait --for=condition=complete job/ghcr-pull-refresher-bootstrap --timeout=120s`.
   Delete after success:
   `kubectl -n arc-runners delete job ghcr-pull-refresher-bootstrap`.
4. For each ARC release (both `arc-df-*` and `arc-itguys-ro*`):
   ```sh
   helm -n arc-runners upgrade --reuse-values <release> \
       oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
       --version 0.14.1 \
       -f k8s/runner-scale-set-image-override.yaml
   ```
   Helm reuses the existing values (App secret, config URL, replica
   counts, tolerations) and merges in the image + pull secret.
5. Per caller repo, opt the workflow into the pre-baked binaries:
   ```yaml
   uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v1
   with:
     path-to-bun: /usr/local/bin/bun
     path-to-claude: /usr/local/bin/claude
   ```
   Don't set these until that scale set has been migrated to the new
   image, or the action will try to use a binary that's not there.

## Cross-org pulls (ITGuys-RO)

The ITGuys-RO scale sets pull a package owned by `dustfeather`. The
refresher uses the dustfeather App's installation token, which carries
`packages: read` on dustfeather-owned packages. So the same
`arc-runners/ghcr-pull` Secret works for ITGuys-RO pods too — no second
App needed. If a future image lives under a different owner, that owner
would need its own App + refresher.

## Failure modes & how to spot them

- **`ErrImagePull` / `ImagePullBackOff` on runner pods**: either
  `ghcr-pull` doesn't exist (bootstrap Job didn't run), or the App
  doesn't have `packages: read` accepted on the installation, or the
  scale set's Helm values still point at the upstream runner image
  without the pull secret. `kubectl describe pod` shows the exact 401
  vs. 403 vs. "secret not found".
- **CronJob OOMing / failing repeatedly**: check
  `kubectl -n arc-runners logs job/ghcr-pull-refresher-<id>` for an
  HTTP body with `"message": "..."`. The most common cause is the
  installation ID drifting if the App was reinstalled — update
  `github_app_installation_id` in the `github-app-dustfeather` Secret.
- **Refresher silently writes a token with no `packages` scope**: the
  refresher cannot detect this; the empirical pull check above is the
  only signal.

## On the upcoming token format change (2026-04-27 onward)

GitHub is rolling out a new App installation token format
(`ghs_APPID_JWT`, ~520 chars) starting **2026-04-27** for first-party
integrations and **mid-May → late-June 2026** for all Apps. The
refresher script treats the token as an opaque string — no length
checks, no regex — so it is forward-compatible. The Kubernetes Secret
field is base64-of-JSON-of-base64-of-token; a ~520 char token expands
to ~700-1000 chars total, well under the 1 MiB Secret limit. No code
change needed here, but if a downstream consumer ever pattern-matches
`ghs_[A-Za-z0-9]{36}` against this token, it will break.
Reference: https://github.blog/changelog/2026-04-24-notice-about-upcoming-new-format-for-github-app-installation-tokens/
