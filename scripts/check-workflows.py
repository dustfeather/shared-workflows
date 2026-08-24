#!/usr/bin/env python3
"""Static checks for the reusable workflows in .github/workflows/.

There is no build and no test suite here, and a reusable workflow cannot be
invoked from a local checkout — `uses:` only resolves through GitHub. So the
only feedback a mistake gets is a run in a *caller's* repo, and two of the
failure modes below produce a run with zero jobs, no logs and no annotation,
which is close to undebuggable from the API.

Each check exists because it already shipped a break:

  on-block-expression   an expression anywhere in `on:` is evaluated at parse
                        time, where the secrets/inputs contexts do not exist,
                        and GitHub rejects the whole file. A `${{ secrets.X }}`
                        written into an input *description* took down every
                        caller of deploy-cloudflare.yml.

  undeclared-input      `${{ inputs.foo }}` in a step, where `foo` is not
                        declared under workflow_call.inputs. Survives a YAML
                        parse and fails the run at validation.

  missing-permissions   no top-level `permissions:` block. CodeQL treats this
                        as a hard error, and the implicit default is far wider
                        than anything here needs.

  escalating-permission a called workflow may not request MORE token
                        permission than the calling job grants, and
                        `permissions:` takes no expressions — so a job asking
                        for anything beyond `contents: read` breaks every
                        least-privilege caller with `startup_failure`, zero
                        jobs, and an explanation that exists only in the web UI.
                        Workflows that legitimately need more are listed in
                        PRIVILEGED below, so adding one is a deliberate act.

Exit 1 on any finding. Run from the repo root.
"""

import glob
import json
import re
import sys

import yaml

# Workflows whose job may request more than `contents: read`, with the reason.
# A caller of one of these must grant the same permission on its calling job.
PRIVILEGED = {
    # Pushes the built image to GHCR.
    ".github/workflows/build-push-image.yml": {"packages"},
    # Posts the review and authenticates to the Anthropic API via OIDC.
    ".github/workflows/claude-code-review.yml": {"pull-requests", "id-token"},
    ".github/workflows/claude.yml": {"pull-requests", "id-token", "contents"},
    # Merging is the point of these two.
    ".github/workflows/dependabot-auto-merge.yml": {"contents", "pull-requests"},
    ".github/workflows/merge-on-approval.yml": {"contents", "pull-requests"},
    # Commits the version bump and cuts the GitHub release.
    ".github/workflows/release-extension.yml": {"contents"},
    # Not workflow_call — this one tags its own repo.
    ".github/workflows/tag-release.yml": {"contents"},
}

INPUT_REF = re.compile(r"\$\{\{\s*inputs\.([A-Za-z0-9_-]+)")


def load(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def on_block(doc):
    # An unquoted `on:` key is the YAML 1.1 boolean True. A check that looks
    # for the string 'on' passes vacuously on every file, which is worse than
    # no check at all.
    return doc.get(True, doc.get("on"))


def check(path):
    problems = []
    doc = load(path)
    on = on_block(doc)

    if on is None:
        return [f"{path}: no `on:` block found"]

    # workflow_call.outputs.<name>.value REQUIRES `${{ jobs.… }}` — it is
    # resolved in job context, not at parse time. Strip those before scanning,
    # or the check flags the one legitimate expression in the block.
    scan = json.loads(json.dumps(on))
    if isinstance(scan, dict):
        wc = scan.get("workflow_call")
        if isinstance(wc, dict):
            wc.pop("outputs", None)
    if "${{" in json.dumps(scan):
        problems.append(
            f"{path}: expression in the `on:` block. GitHub evaluates it at "
            f"parse time and rejects the file — every caller breaks."
        )

    if "permissions" not in doc:
        problems.append(f"{path}: no top-level `permissions:` block")

    declared = set()
    if isinstance(on, dict) and isinstance(on.get("workflow_call"), dict):
        declared = set((on["workflow_call"].get("inputs") or {}).keys())

        allowed = PRIVILEGED.get(path, set())
        for name, job in (doc.get("jobs") or {}).items():
            for scope, level in (job.get("permissions") or {}).items():
                if level != "read" and scope not in allowed:
                    problems.append(
                        f"{path}: job `{name}` requests `{scope}: {level}`. A "
                        f"caller granting least privilege will fail at "
                        f"validation with startup_failure and no logs. Add it "
                        f"to PRIVILEGED in this script if it is deliberate."
                    )

        body = json.dumps(doc.get("jobs") or {})
        for ref in sorted(set(INPUT_REF.findall(body))):
            if ref not in declared:
                problems.append(
                    f"{path}: `inputs.{ref}` is used but never declared under "
                    f"workflow_call.inputs"
                )

        for name, spec in (on["workflow_call"].get("inputs") or {}).items():
            spec = spec or {}
            if not spec.get("required") and "default" not in spec:
                problems.append(
                    f"{path}: optional input `{name}` has no default. Inputs "
                    f"must default to a value preserving prior behaviour."
                )

    return problems


def main():
    files = sorted(glob.glob(".github/workflows/*.yml"))
    if not files:
        sys.exit("no workflows found — run from the repo root")

    found = []
    for path in files:
        try:
            found.extend(check(path))
        except yaml.YAMLError as exc:
            found.append(f"{path}: YAML parse error: {exc}")

    for problem in found:
        print(f"  {problem}")

    if found:
        print(f"\n{len(found)} problem(s) in {len(files)} workflow(s)")
        return 1
    print(f"{len(files)} workflows OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
