# CLAUDE.md

Library of reusable GitHub Actions workflows (`workflow_call`) in `.github/workflows/`, consumed by every other repo under this account via `uses: dustfeather/shared-workflows/.github/workflows/<name>.yml@v1`. No app code. A merged change ships to every caller on next run — treat blast radius accordingly. Exception: `tag-release.yml` = this repo's own release automation, not callable.

## Verifying changes

No build/test; only local check = YAML parse (`python3 -c "import yaml; yaml.safe_load(...)"`). Reusable workflows cannot be invoked from local checkout — `uses:` resolves through GitHub. Real e2e test: push to feature branch, point one caller at it (`@feature/<name>`) before tagging.

## Versioning — pick bump by caller impact

`tag-release.yml` auto-tags every push to `main`: default bumps **patch** (wraps at 100 → minor; minor uncapped), re-points floating `v1` tag. **major never bumped automatically.** Larger bump: put `#minor` or `#major` in commit subject (largest wins). Match is fixed-string — don't write tokens verbatim unless you mean them.

- **patch** — bug fix, doc/log/comment tweak, internal refactor, bumping action used inside workflow with no interface change.
- **minor** — backwards-compatible feature: new *optional* input (with default), new workflow, opt-in job/step.
- **major** — breaks workflow contract: removing/renaming input or secret, adding *required* input, changing default callers must react to, requiring new permissions.

Rule of thumb: if caller's shim could keep working untouched → patch/minor; if not → major. Unsure → prefer larger bump.

## Conventions

- Inputs added to reusable workflow MUST default to value preserving prior behavior.
- Every workflow declares explicit top-level `permissions:` block; jobs needing more declare own. CodeQL flags missing — hard error, not warning.
- Reusable workflows run in **caller's** context with caller's secrets. `secrets: inherit` works for same-owner callers; cross-owner (`ITGuys-RO/*`) MUST pass secrets explicitly — `inherit` does not reliably carry org-level "selected"-visibility secrets across owner boundary.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
