# Minimal runtime image for the GHCR pull-secret refresher CronJob.
# Bakes the four tools the refresh script needs:
#   - openssl  : sign the App JWT (RS256)
#   - curl     : POST /app/installations/{id}/access_tokens
#   - jq       : parse the response
#   - kubectl  : upsert the resulting `arc-runners/ghcr-pull` Secret
#
# All four are pre-installed because the consuming cluster has no
# egress to alpine/dl-cdn or to dl.k8s.io from inside arc-runners pods
# (Cloudflare WARP / Mesh filtering in place), so `apk add ...` at
# runtime fails. Everything must be present at image-build time.
#
# Published as `ghcr.io/dustfeather/ghcr-pull-refresher:<tag>` with
# PUBLIC visibility — this image is what bootstraps the cluster's
# private-image pull credential, so it cannot itself rely on that
# credential. Contains no secrets; only public tooling.

FROM alpine:3.21

ARG KUBECTL_VERSION=1.32.3

RUN set -eux; \
    apk add --no-cache \
        bash \
        ca-certificates \
        curl \
        jq \
        openssl \
        coreutils; \
    curl -fsSL -o /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl"; \
    chmod 0755 /usr/local/bin/kubectl; \
    kubectl version --client=true; \
    openssl version; \
    jq --version

# nonroot UID/GID (Docker convention 65532) so the CronJob's
# `runAsNonRoot: true` securityContext is satisfied with no chown
# gymnastics. The script writes nothing to the image filesystem at
# runtime — all writes go to the `tmp` emptyDir mount.
USER 65532:65532
