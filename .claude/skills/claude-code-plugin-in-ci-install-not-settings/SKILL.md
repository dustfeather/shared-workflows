---
name: claude-code-plugin-in-ci-install-not-settings
description: |
  Enable a Claude Code plugin (and its subagents) in headless CI —
  `anthropics/claude-code-action@v1`, a `claude -p` job, or a pre-baked runner
  image. Use when: (1) you declared `enabledPlugins` /
  `extraKnownMarketplaces` in `settings.json` (or the action's `settings:`
  input) and the plugin's skills, commands or subagents are still missing;
  (2) `claude plugin marketplace add` fails in CI cloning
  `git@github.com:...` with `Host key verification failed` /
  `Permission denied (publickey)` while the same command works on a laptop;
  (3) `claude plugin install` hangs or refuses without a TTY; (4) a plugin
  subagent exists in `claude plugin list` but a `Task` call to
  `plugin-name:agent-name` is denied or the agent is "not found";
  (5) you are adding a plugin to a Dockerfile and need to know whether the
  install requires an API key or OAuth token; (6) you are wiring
  `pr-review-toolkit` (or any git-diff-oriented agent) into a PR review job
  and it reports nothing. Covers why declaring is not installing, the
  SSH-vs-HTTPS marketplace trap, the `--allowed-tools` entries subagents
  need, and how claude-code-action merges its `settings:` input.
author: Claude Code
version: 1.0.0
date: 2026-09-02
---

# Claude Code plugins in CI: declaring is not installing

## Problem

A plugin that works interactively silently does nothing in a headless job.
There is no error — the review/agent just runs without the plugin's skills,
commands, subagents or MCP servers, and the log looks clean.

Four separate traps produce that same silent outcome, and each is invisible
on a developer laptop where SSH keys, a TTY and a warm `~/.claude/plugins`
all exist.

## Context / Trigger conditions

- `enabledPlugins` / `extraKnownMarketplaces` set in `settings.json` (or in
  `anthropics/claude-code-action@v1`'s `settings:` input) and the plugin's
  components never appear.
- `claude plugin marketplace add owner/repo` in a container or CI runner dies
  cloning `git@github.com:owner/repo.git` — `Permission denied (publickey)`
  or `Host key verification failed`.
- `claude plugin install` with no TTY refusing the marketplace-declared
  command confirmation.
- A plugin subagent shows as enabled but `Task(subagent_type:
  "plugin:agent")` is denied, or the parent agent reports it does not exist.
- A `pr-review-toolkit` (or similar) agent returns "no changes to review" on
  a PR that clearly has a diff.

## Solution

### 1. Declaring does not install — the install has to run

`enabledPlugins` and `extraKnownMarketplaces` are read, but Claude Code
performs **no fetch** from them. A `settings.json`-only approach loads
nothing.

Verified: a clean `$HOME` containing only

```json
{
  "extraKnownMarketplaces": {
    "claude-plugins-official": {
      "source": { "source": "github", "repo": "anthropics/claude-plugins-official" }
    }
  },
  "enabledPlugins": { "pr-review-toolkit@claude-plugins-official": true }
}
```

then `claude -p "..."` → run succeeds, `~/.claude/plugins/marketplaces/` stays
empty and `claude plugin list` says "No plugins installed."

So the plugin must be **installed**, either baked into the runner image or in
a job step. Baking is preferable: no per-job marketplace clone, and a pinned
version.

### 2. Use the full HTTPS URL, never the `owner/repo` shorthand

```sh
# WRONG in CI — resolves to git@github.com: and needs an SSH key
claude plugin marketplace add anthropics/claude-plugins-official

# RIGHT — forces https://
claude plugin marketplace add https://github.com/anthropics/claude-plugins-official
```

The shorthand records `{"source":"github","repo":"..."}` and clones over SSH.
The URL form records `{"source":"git","url":"https://….git"}`. Verified by
running both with `GIT_SSH_COMMAND=/bin/false`: shorthand fails, URL form
clones fine.

### 3. `install` needs `-y` without a TTY

```sh
claude plugin install <plugin>@<marketplace> -y
```

Without `-y` it refuses the marketplace-declared-command confirmation when
stdin/stdout is not a TTY. Both commands exit 0 on success, so they chain
safely under `set -eux`.

### 4. Neither command needs credentials

`marketplace add` and `install` both work with no `ANTHROPIC_API_KEY` and no
`CLAUDE_CODE_OAUTH_TOKEN` — verified with `env -u` on both. That is what makes
the Docker-build bake possible: the image never has to carry a token.

### 5. Subagents need `Task` **and** read tools in `--allowed-tools`

Plugin subagents do load in `-p` mode, namespaced `plugin-name:agent-name`.
But `--allowed-tools` is a strict allowlist:

- Without `Task`, the agents load and are simply uninvokable — the parent
  silently does the work alone.
- The allowlist is **session-wide and inherited by subagents**. A subagent
  cannot open a file the parent could not, regardless of its own frontmatter
  saying "Tools: All tools". Add `Read,Grep,Glob` (and whatever `Bash(...)`
  patterns the agents need) or they come back empty-handed.

### 6. `claude-code-action`'s `settings:` input shallow-merges

`base-action/src/setup-claude-code-settings.ts`:

```ts
settings = { ...settings, ...inputSettings };
```

It reads the existing `~/.claude/settings.json`, spreads the input over it,
and writes it back. Top-level keys only. So plugin keys written into the image
by `claude plugin install` survive an input that carries only `hooks` — but an
input that *does* carry `enabledPlugins` replaces the baked one wholesale.

## Verification

Inside the built image, or as a job step:

```sh
claude plugin list | grep -q '<plugin>@<marketplace>'
```

Then confirm the subagents are actually reachable at runtime:

```sh
echo "List the exact subagent_type names available to your Task tool. \
Output only the names, one per line. No prose." | claude -p --allowed-tools "Task"
```

Expected: the namespaced plugin agents appear alongside the built-ins
(`Explore`, `Plan`, `general-purpose`, …). If they are absent, the install
did not happen; if they are listed but a `Task` call is denied, `Task` is
missing from `--allowed-tools`.

## Example

The bake, as a `USER runner` layer in the ARC runner image (see
[claude-code-action-prebake-arc-runner-image](../claude-code-action-prebake-arc-runner-image/SKILL.md)):

```dockerfile
USER runner

RUN set -eux; \
    claude plugin marketplace add https://github.com/anthropics/claude-plugins-official; \
    claude plugin install pr-review-toolkit@claude-plugins-official -y; \
    claude plugin list | grep -q 'pr-review-toolkit@claude-plugins-official'
```

~10 MB, mostly the marketplace clone. Runs as `runner` deliberately: plugin
state is per-`$HOME` and the job runs as `runner`.

Then in the workflow:

```yaml
claude_args: >-
  --allowed-tools "Task,Read,Grep,Glob,Bash(gh pr diff:*),..."
```

## Notes

- **git-diff-oriented agents are useless on a CI checkout.**
  `pr-review-toolkit`'s agents default to reviewing `git diff` of a dirty
  worktree. A CI checkout is clean and (with `fetch-depth: 1`) shallow, so
  that yields nothing and the agent reports no findings — which reads exactly
  like "the PR is fine". The dispatching prompt must tell each agent to source
  the diff from `gh pr diff <N>` and state the PR number and repo.
- **Cost.** 5 of `pr-review-toolkit`'s 6 agents declare `model: inherit`, so
  each fans out at the parent's model and effort. Under
  `--model claude-opus-4-8 --effort high` that multiplies spend per PR by
  roughly the number of agents dispatched. Pick a subset.
- **`code-simplifier` edits source.** Never dispatch it from a read-only
  review job.
- **`--bare` disables plugins** along with hooks, LSP and settings. Do not
  combine it with a plugin-dependent prompt.
- `--plugin-dir <path>` and `--plugin-url <url>` load a plugin for one session
  only. Viable if you would rather vendor a plugin than install it, but they
  bypass the marketplace and so bypass version pinning too.
- Callers on an older runner image will not have the plugin. Give the prompt
  an explicit degrade path ("if those subagents do not exist, do the review
  yourself") or the parent will retry or improvise.
- See also:
  [duplicate-plugin-install-across-marketplaces](~/.claude/skills/duplicate-plugin-install-across-marketplaces/SKILL.md)
  — installing the same plugin from two marketplaces makes it ambiguous which
  copy answers.

## References

- `anthropics/claude-code-action@v1` — `base-action/src/setup-claude-code-settings.ts`
  (the shallow merge).
- `claude plugin --help`, `claude plugin install --help`,
  `claude plugin marketplace add --help` — the authority on flags; all
  behaviour above was verified against the installed CLI, not docs.
