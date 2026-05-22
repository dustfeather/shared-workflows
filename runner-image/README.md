# Pre-baked ARC runner image

`Dockerfile` here builds a derivative of `ghcr.io/actions/actions-runner`
with the tooling our reusable workflows need pre-installed: `gh`, `jq`,
Node, Bun (pinned to the version `anthropics/claude-code-action@v1`
expects), and `@anthropic-ai/claude-code`.

CI builds + pushes it to
`ghcr.io/dustfeather/actions-runner-claude` on every push to `main` that
touches `runner-image/**` (see `.github/workflows/runner-image.yml`).
The package is published with `public` visibility so ITGuys-RO scale
sets (which are owned by a different GitHub org) can pull it without
`imagePullSecrets`. If we ever need to make it private, the ITGuys-RO
runner Helm releases will need a pull-secret added.

## Using it on the cluster

The ARC scale-set releases are Helm-managed. Both the `dustfeather`-owned
runner sets (`arc-df-*`) and the `ITGuys-RO`-owned sets (`arc-itguys-ro`,
`arc-itguys-ro-degoog-infra`, …) need the override — the image bake is
useless on any scale set that's still on the upstream runner image, since
the workflow's `path-to-bun` / `path-to-claude` inputs point at binaries
that won't be there. For each `autoscalingrunnerset`, override the runner
container image in its Helm values:

```yaml
template:
  spec:
    containers:
      - name: runner
        image: ghcr.io/dustfeather/actions-runner-claude:latest
```

Apply per release:

```bash
helm -n arc-runners upgrade --reuse-values <release> \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
  --version 0.14.1 \
  -f values-override.yaml
```

The `init-dind-externals` initContainer pins to the same image — Helm
sets both from the same value, so no manual edit is needed there.

## Wiring the review workflow

`claude-code-review.yml` exposes two optional inputs that tell
`claude-code-action@v1` to skip its own Bun and Claude Code installs and
use the binaries baked into the image:

```yaml
uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v1
with:
  path-to-bun: /usr/local/bin/bun
  path-to-claude: /usr/local/bin/claude
```

Callers running on the upstream `ghcr.io/actions/actions-runner` image
(or GitHub-hosted runners) should leave them empty — the action will
install both itself.

## Keeping the Bun pin honest

`Dockerfile` pins `BUN_VERSION` to the value the upstream action's
`setup-bun` step currently uses. When that action bumps its pin, bump
ours too — a mismatch isn't a hard failure but stays a footgun.
