# Security policy

## Reporting a vulnerability

For anything that looks security-sensitive — token leakage, privilege
escalation in a reusable workflow, ability for a malicious caller to
exfiltrate secrets, or anything else where public discussion would
expose users — **do not open a public issue**. Instead use GitHub's
private vulnerability reporting:

1. Go to https://github.com/dustfeather/shared-workflows/security/advisories/new
2. File a draft advisory describing the issue.

Expect an acknowledgement within 7 days.

For non-security bugs, normal issues are fine.

## Trust model

This repo is **public**. Anyone can read every workflow file, including
the prompts, allowed-tools lists, and trigger conditions. Treat the
contents accordingly:

- No secrets, tokens, or credentials are ever committed. Workflows
  reference secrets via `${{ secrets.NAME }}`, which the GitHub Actions
  runtime resolves at execution time from the calling repo's secret
  store — never from this repo's secret store.
- The `claude-code-review` and `claude` workflows include a
  `trusted-actors` gate (default `dustfeather`) that fences the
  invocation context. A fork or arbitrary contributor cannot trigger
  Claude Code Action runs through these workflows even if their PR
  modifies the shim.
- The publish workflows skip with a warning rather than fail when
  store secrets aren't configured. They have no fallback that uploads
  to "wherever" — they no-op if the right repo-level secrets aren't set.

## Reusable-workflow secret semantics (important)

When a caller does:

```yaml
uses: dustfeather/shared-workflows/.github/workflows/<file>.yml@v1
secrets: inherit
```

…the **caller's secrets** are passed into the called workflow at
runtime. This repo's own secrets are **not** visible to callers. Each
caller pays for its own Claude/Chrome/AMO usage with its own credentials.

Conversely, a caller cannot use these workflows to extract secrets from
this repo. The token-exchange flow is one-directional caller → called.

### Cross-owner inheritance gap

`secrets: inherit` does **not** reliably carry **org-level secrets with
"selected" visibility** when the calling workflow and the called workflow
live under different owners (e.g. `ITGuys-RO/*` calling
`dustfeather/shared-workflows`). The symptom is:

```
Error when evaluating 'secrets'. .github/workflows/<shim>.yml: Secret X
is required, but not provided while calling.
```

The fix is to pass the secret explicitly:

```yaml
secrets:
  CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

The reference resolves in the caller's context, where the org-level
selected secret IS visible to the calling repo, and is then forwarded as
a named secret to the called workflow — bypassing the cross-owner
`inherit` gap. See README.md "Cross-owner callers" for the full pattern.

This is not a security vulnerability — it's a defensive default by
GitHub. It just means same-owner callers get a convenience shortcut
(`inherit`) that cross-owner callers don't.

## What's in scope

- Workflow files under `.github/workflows/`
- The `trusted-actors`, `allowed-bots` defaults and gate logic
- Permissions blocks
- Any future code (currently: none)

## What's out of scope

- Vulnerabilities in `actions/checkout`, `anthropics/claude-code-action`,
  or any other third-party action invoked here. Report those upstream.
- The Chrome Web Store API, Mozilla AMO API, or other external services
  the publish workflows call.
- Misconfiguration in a calling repo (e.g. a caller that grants overly
  broad permissions in its own shim).

## Pinning third-party actions

Where reasonable, third-party actions are pinned by tag. SHA-pinning is
preferred for long-term security but not yet enforced project-wide; PRs
that switch tag pins to SHA pins are welcome.
