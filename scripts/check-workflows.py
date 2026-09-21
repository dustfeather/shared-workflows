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

  pnpm-pin-drift        the `pnpm-version` default must be identical in every
                        workflow that declares it, and every
                        `pnpm/action-setup@` must be pinned to the SAME commit
                        SHA. That version is safe only because it equals the
                        BOOTSTRAP version the action installs at that SHA — the
                        action never reads the runner image's pnpm. Move one
                        without the other and pnpm's self-installer really runs,
                        installs pnpm THROUGH pnpm into the shared store, and
                        every job after the first dies with
                        ERR_PNPM_BROKEN_PNPM_INSTALL. The FIRST run passes, so
                        no PR can catch this; hence a static check.
                        CHECK_PNPM_BOOTSTRAP=1 additionally verifies the default
                        against the action's committed bootstrap lockfiles over
                        the network.

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
import os
import re
import sys
import time
import urllib.request

import yaml

# Workflows whose job may request more than `contents: read`, with the reason.
# A caller of one of these must grant the same permission on its calling job.
PRIVILEGED = {
    # Pushes the built image to GHCR.
    ".github/workflows/build-push-image.yml": {"packages"},
    # Posts the review and authenticates to the Anthropic API via OIDC.
    ".github/workflows/claude-code-review.yml": {"pull-requests", "id-token"},
    ".github/workflows/claude.yml": {"pull-requests", "id-token", "contents"},
    # Merging is the point of these two, and both now close the merged PR's
    # linked issues themselves.
    #
    # `issues` is on both because a merged `Closes #N` does not close anything
    # without it: GitHub computes the link for free, but EXECUTING the close is a
    # write to the Issues API attributed to whoever performed the merge —
    # GITHUB_TOKEN here. Deliberate on both, and each cost its caller PRs first
    # (merge-on-approval: uninsta, fleet-manager, invest, itguys.ro;
    # dependabot-auto-merge: eleven callers, 2026-09-13), because this list is
    # exactly the set that every caller must match.
    #
    # dependabot-auto-merge USED to be excluded here, on the reasoning that
    # Dependabot never writes closing keywords. That is true of Dependabot's own
    # bodies and false of the workflow: a caller can add body text to a
    # Dependabot PR, and the reusable workflow cannot tell the difference. The
    # exclusion bought nothing and left the two merge paths behaving differently
    # for no reason a caller could see. Reversed deliberately (issue #35).
    ".github/workflows/dependabot-auto-merge.yml": {"contents", "pull-requests", "issues"},
    ".github/workflows/merge-on-approval.yml": {"contents", "pull-requests", "issues"},
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


ACTION_SETUP_REF = re.compile(
    r"^\s*(?:-\s*)?uses:\s*pnpm/action-setup@(\S+)", re.MULTILINE
)
SHA40 = re.compile(r"^[0-9a-f]{40}$")

# The run-time guard step's copy of the bootstrap version. It exists because a
# CALLER's `pnpm-version` is invisible to this script; this check exists so that
# copy cannot drift away from the default it is meant to mirror.
GUARD_LITERAL = re.compile(r'^\s*PNPM_BOOTSTRAP_VERSION:\s*"([^"]+)"', re.MULTILINE)

# The GitHub contents API, retried and authenticated. This runs in `guard`,
# the ONE required status check on main, and a hard failure there blocks even
# pr-merge.yml's bot merge (github-actions[bot] is not an admin, so it cannot
# bypass protection). Hard-on-CI is still right — "could not check" must not
# read as "checked" — but it must not be one-shot: a rate-limit or a blip would
# otherwise wedge the repo rather than raise a red advisory check.
FETCH_ATTEMPTS = 3


def fetch_json(url):
    """GET url as JSON, retrying transient failures. Raises the last error."""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github.raw+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    # api.github.com, NOT raw.githubusercontent.com. raw does not fall back to
    # anonymous on an Authorization header it cannot validate -- it answers 404
    # (measured: valid token 200, malformed token 404, no header 200). Sending
    # `github.token`, an installation token with no grant on pnpm/action-setup,
    # therefore risked turning every guard run into a hard 404 on the one
    # required check on main. The contents API is documented to take the token
    # and is where it actually raises the limit, 60 -> 1000/hr unauthenticated
    # to authenticated. Without a token it still answers, on the 60/hr tier.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    last = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=20) as fh:
                return json.loads(fh.read())
        except Exception as exc:  # noqa: BLE001 — retry anything transient
            last = exc
            if attempt < FETCH_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
    raise last


BOOTSTRAP_LOCKS = {
    "pnpm-lock.json": "node_modules/pnpm",
    "exe-lock.json": "node_modules/@pnpm/exe",
}


def pnpm_pin_drift(files):
    """Cross-file: the pnpm pin is a three-way agreement, not a per-file value.

    `pnpm-version` is safe at exactly one value — the one pnpm/action-setup
    BOOTSTRAPS. The action never consults the runner image: it wipes its dest
    dir, `npm ci`s its own committed bootstrap lockfile, then runs
    `pnpm self-update <the input>` unconditionally. Equal to the bootstrap that
    is a no-op. Anything else really installs — through pnpm, so it honours the
    container's shared `pnpm_config_store_dir` — and every job after the first
    reuses a store entry missing @pnpm/exe.linux-x64 and dies.

    A PR cannot catch it, because the FIRST run is the one that passes. So the
    invariant is asserted statically instead: one default, one SHA.
    """
    problems = []
    defaults = {}
    refs_by_file = {}
    text = {}

    for path in files:
        with open(path) as fh:
            text[path] = fh.read()
        try:
            doc = load(path)
        except yaml.YAMLError:
            continue  # check() already reports the parse error
        on = on_block(doc)
        if isinstance(on, dict) and isinstance(on.get("workflow_call"), dict):
            spec = (on["workflow_call"].get("inputs") or {}).get("pnpm-version")
            if isinstance(spec, dict) and "default" in spec:
                defaults[path] = str(spec["default"])

        found = ACTION_SETUP_REF.findall(text[path])
        if found:
            refs_by_file[path] = found

    for path, refs in sorted(refs_by_file.items()):
        for ref in refs:
            if not SHA40.match(ref):
                problems.append(
                    f"{path}: `pnpm/action-setup@{ref}` is a floating ref. Pin "
                    f"the commit SHA — the action can otherwise bump its "
                    f"bootstrap pnpm under us, and the break lands on the job "
                    f"AFTER the one that proves it green."
                )

    # Both directions. A workflow that calls the action but ships no guard is
    # the copy-paste case: it passes every other check here while leaving a
    # CALLER free to pass "12", which is the hole the guard exists to close.
    for path in sorted(refs_by_file):
        if not GUARD_LITERAL.search(text[path]):
            problems.append(
                f"{path}: calls pnpm/action-setup but has no "
                f"PNPM_BOOTSTRAP_VERSION guard step. A caller could pass any "
                f"pnpm-version and nothing here would notice."
            )

    for path in files:
        for literal in GUARD_LITERAL.findall(text[path]):
            declared = defaults.get(path)
            if declared is None:
                problems.append(
                    f"{path}: PNPM_BOOTSTRAP_VERSION is set here, but this "
                    f"workflow declares no `pnpm-version` input for it to mirror."
                )
            elif literal != declared:
                problems.append(
                    f"{path}: the run-time guard checks {literal} while the "
                    f"`pnpm-version` default is {declared}. They mirror the same "
                    f"bootstrap version and must be equal."
                )

    distinct_defaults = set(defaults.values())
    if len(distinct_defaults) > 1:
        problems.append(
            "the `pnpm-version` defaults disagree: "
            + ", ".join(f"{p} -> {v}" for p, v in sorted(defaults.items()))
            + ". Every workflow declaring it must carry the same exact version."
        )

    pinned = sorted({r for refs in refs_by_file.values() for r in refs if SHA40.match(r)})
    if len(pinned) > 1:
        problems.append(
            "pnpm/action-setup is pinned to more than one SHA: "
            + ", ".join(pinned)
            + ". One SHA means one bootstrap version; several cannot all match "
            "a single default."
        )

    # The network half, which the pre-push hook and the guard job both enable.
    # A version MISMATCH is always a hard finding. A network FAILURE is one only
    # under CI: on a runner, "could not check" must not read as "checked", since
    # this is the ONLY assertion that catches the SHA moving to a commit with a
    # different bootstrap -- the shape a Dependabot bump arrives in. Locally it
    # is a note, so a push from a plane is not blocked by a check that never
    # actually disagreed with anything.
    if os.environ.get("CHECK_PNPM_BOOTSTRAP") == "1":
        if len(pinned) != 1 or len(distinct_defaults) != 1:
            problems.append(
                "CHECK_PNPM_BOOTSTRAP=1 but the SHA or the default is not "
                "single-valued; fix the findings above first."
            )
        else:
            sha, want = pinned[0], next(iter(distinct_defaults))
            for lock, key in BOOTSTRAP_LOCKS.items():
                url = (
                    "https://api.github.com/repos/pnpm/action-setup/contents/"
                    f"src/install-pnpm/bootstrap/{lock}?ref={sha}"
                )
                try:
                    data = fetch_json(url)
                except Exception as exc:  # noqa: BLE001 — offline, rate-limited, DNS…
                    msg = (
                        f"could not read {lock} at {sha[:7]} ({exc}); "
                        f"pnpm bootstrap left unverified"
                    )
                    if os.environ.get("CI"):
                        # Hard here by design, which means an outage that
                        # outlasts the retry budget -- notably a contents-API
                        # rate limit, which resets hourly, not in the ~3s three
                        # attempts cover -- reds the one required check and so
                        # blocks the bot merge on EVERY pr in the repo, not just
                        # this one. That is the cost of refusing to let "could
                        # not check" read as "checked", and it is the right
                        # trade, but a wedged repo must not also be a puzzle.
                        # Name the way out in the failure itself.
                        msg += (
                            ". This is the one required check on main, so it "
                            "blocks the bot merge repo-wide until it passes. "
                            "If the cause is transient (rate limit: resets "
                            "hourly), re-run the job. enforce_admins is false, "
                            "so an owner can merge directly meanwhile."
                        )
                        problems.append(msg)
                    else:
                        print(f"  note: {msg}", file=sys.stderr)
                    continue
                got = ((data.get("packages") or {}).get(key) or {}).get("version")
                # A missing key is not a version drift. It means the lockfile's
                # shape changed under us -- `packages` restructured, the entry
                # renamed -- and reporting that as "bootstraps None" sends the
                # reader looking for a version move that never happened. Same
                # class as the fetch failure above: could not read, not disagreed.
                if got is None:
                    msg = (
                        f"could not read the bootstrap version from {lock} at "
                        f"{sha[:7]}: no \"version\" under packages[{key!r}]"
                    )
                    if os.environ.get("CI"):
                        problems.append(msg)
                    else:
                        print(f"  note: {msg}", file=sys.stderr)
                elif got != want:
                    problems.append(
                        f"pnpm-version defaults to {want}, but "
                        f"action-setup@{sha[:7]} bootstraps {got} in {lock}. "
                        f"self-update would really run."
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

    found.extend(pnpm_pin_drift(files))

    for problem in found:
        print(f"  {problem}")

    if found:
        print(f"\n{len(found)} problem(s) in {len(files)} workflow(s)")
        return 1
    print(f"{len(files)} workflows OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
