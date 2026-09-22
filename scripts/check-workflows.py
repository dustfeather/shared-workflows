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

# Versions a workflow installs itself that runner-image/Dockerfile also bakes.
# Keyed by the Dockerfile ARG; the value matches the workflow's own literal.
# Anchored on the env key rather than a bare version string so an unrelated
# "2.3.5" somewhere in a prompt cannot be read as this pin.
PINNED_WITH_IMAGE = {
    "CRG_VERSION": re.compile(r'^\s*CRG_VERSION:\s*"?([^"\s]+)"?\s*$', re.M),
}
# Matches the invocation, not a mention. Anchored at the start of a line
# (after optional env-var prefixes, which is how the pre-push hook calls it),
# so `echo "python3 scripts/check-workflows.py"`, a comment naming the script
# and a heredoc quoting it do not count as the gate running it. The looser
# version of this matched the echo, which would have let a step that only
# PRINTS the command satisfy the assertion.
SCRIPT_INVOCATION = re.compile(
    r"^[ \t]*(?:[A-Za-z_][A-Za-z0-9_]*=\S*[ \t]+)*python3?[ \t]+\S*scripts/check-workflows\.py",
    re.MULTILINE,
)

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

# The THIRD bootstrap, added in ea17c68. `run.ts` selects it whenever the
# requested version is a range inside pnpm 12 (`targetsPnpm12`), and then
# short-circuits on `satisfies(bootstrapVersion, targetVersion)` rather than on
# equality -- so a bare major needs the bootstrap to be IN it, not equal to it.
# Two different lockfiles and two different comparisons; reading the wrong pair
# is how this check would pass while self-update really ran.
NATIVE_LOCK = ("native-lock.json", "node_modules/pnpm")

# A bare major ("12"), the one range form the bash guard can compare with string
# equality. Anything else -- "^12.0.0", ">=12 <13" -- is a range the action would
# also accept, but the guard could not, so it is refused here rather than left to
# fail confusingly at run time.
BARE_MAJOR = re.compile(r"^\d+$")


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

    # The gate that runs this script must not opt out of its own strictness.
    # PNPM_BOOTSTRAP_SOFT turns an unreachable lockfile into a stderr note, so
    # a step that sets it alongside CHECK_PNPM_BOOTSTRAP=1 reports success
    # having verified nothing -- and does it on the one required check on main,
    # where nothing downstream would notice. The pre-push hook is allowed to
    # opt out; the workflow is not, and this is what makes "not declared" an
    # enforced property rather than a comment asking nicely.
    # Read the parsed env: maps, never the file text. A text scan matches the
    # comment explaining WHY the variable is absent, so the check would fire on
    # its own documentation -- and the obvious "fix" is to delete the comment,
    # which is the opposite of what is wanted.
    gate = "guard-tests.yml"
    gate_path = next((p for p in files if p.endswith(gate)), None)
    if gate_path is None:
        problems.append(
            f"{gate} is missing. It carries the `guard` job, which is the one "
            f"required status check on main and the only place these "
            f"assertions run in CI."
        )
    else:
        try:
            gate_doc = load(gate_path) or {}
        except yaml.YAMLError:
            gate_doc = {}

        # Find the STEP that actually runs this script, and read ITS effective
        # env. A union over every env: map in the file would assert only that
        # the file somewhere sets the variable -- which is the same green no-op
        # the ambient-CI switch was replaced to close. guard-tests.yml already
        # has two python steps, so moving CHECK_PNPM_BOOTSTRAP onto the wrong
        # one, or splitting this step in two, would leave the bootstrap
        # lockfiles unread while the assertion still called the gate strict.
        #
        # Precedence is workflow -> job -> step, LAST wins, which is Actions'
        # own order. The union got this backwards as well: it applied the
        # workflow-level map after the step maps, so a top-level
        # CHECK_PNPM_BOOTSTRAP: "0" would have overridden a correct per-step
        # "1" and reddened the one required check for no reason.
        gate_steps = []
        for job in (gate_doc.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                if SCRIPT_INVOCATION.search(str(step.get("run") or "")):
                    env = {}
                    for scope in (
                        gate_doc.get("env"),
                        job.get("env"),
                        step.get("env"),
                    ):
                        if isinstance(scope, dict):
                            env.update(scope)
                    gate_steps.append((step.get("name") or "<unnamed step>", env))

        if not gate_steps:
            problems.append(
                f"{gate_path}: no step runs scripts/check-workflows.py. These "
                f"assertions then gate nothing in CI, while the pre-push hook "
                f"keeps passing locally and nothing says the difference."
            )
        for step_name, env in gate_steps:
            if str(env.get("CHECK_PNPM_BOOTSTRAP", "")) != "1":
                problems.append(
                    f"{gate_path}: the step {step_name!r} runs "
                    f"check-workflows.py without CHECK_PNPM_BOOTSTRAP=1 in its "
                    f"effective env, so it never reads the bootstrap lockfiles "
                    f"and the pin's third leg goes unverified while the step "
                    f"still exits 0."
                )
            if "PNPM_BOOTSTRAP_SOFT" in env:
                problems.append(
                    f"{gate_path}: the step {step_name!r} sets "
                    f"PNPM_BOOTSTRAP_SOFT, which downgrades an unreachable "
                    f"bootstrap lockfile to a note. In the one required check "
                    f"on main that turns 'could not verify the pin' into a "
                    f"green run. Only the pre-push hook may opt out."
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
    # A version MISMATCH is always a hard finding. A network FAILURE is one too,
    # BY DEFAULT: "could not check" must not read as "checked", since this is
    # the ONLY assertion that catches the SHA moving to a commit with a
    # different bootstrap -- the shape a Dependabot bump arrives in.
    #
    # PNPM_BOOTSTRAP_SOFT=1 downgrades that to a stderr note, so a push from a
    # plane is not blocked by a check that never actually disagreed. The switch
    # is deliberately an opt-IN to silence rather than an opt-in to strictness.
    # It used to read ambient CI, which meant the strict behaviour depended on
    # a variable no caller declared: tidying an env: block, or moving the step
    # to a job that did not export it, would have turned the one required check
    # on main into a green no-op with no annotation saying so. Now the silent
    # state needs someone to ask for it by name, and the pre-push hook is the
    # only caller that does.
    if os.environ.get("CHECK_PNPM_BOOTSTRAP") == "1":
        if len(pinned) != 1 or len(distinct_defaults) != 1:
            problems.append(
                "CHECK_PNPM_BOOTSTRAP=1 but the SHA or the default is not "
                "single-valued; fix the findings above first."
            )
        else:
            sha, want = pinned[0], next(iter(distinct_defaults))
            # Which lockfile is authoritative depends on the SHAPE of the
            # default, exactly as `run.ts` decides it. A bare major >= 12 takes
            # the native bootstrap and is satisfied by any version inside it; an
            # exact version takes the other two and must equal them. Checking a
            # bare major against pnpm-lock.json would compare "12" to "11.25.0"
            # and report drift that is not there.
            checks = {}
            if BARE_MAJOR.match(want):
                if int(want) < 12:
                    problems.append(
                        f"the `pnpm-version` default is the bare major {want!r}, "
                        f"but action-setup only treats a range as satisfiable on "
                        f"its native bootstrap for pnpm >= 12. Below that a range "
                        f"resolves to the newest matching release and really runs "
                        f"`pnpm self-update`. Use an exact version instead."
                    )
                else:
                    checks = {NATIVE_LOCK[0]: NATIVE_LOCK[1]}
            else:
                checks = dict(BOOTSTRAP_LOCKS)
            for lock, key in checks.items():
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
                    if not os.environ.get("PNPM_BOOTSTRAP_SOFT"):
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
                    if not os.environ.get("PNPM_BOOTSTRAP_SOFT"):
                        problems.append(msg)
                    else:
                        print(f"  note: {msg}", file=sys.stderr)
                elif BARE_MAJOR.match(want):
                    # Containment, not equality: the action short-circuits on
                    # `satisfies(bootstrap, "12")`, which is true for every
                    # 12.x. That is the whole reason the default needs no edit
                    # when Dependabot moves the SHA within the major.
                    if got.split(".")[0] != want:
                        problems.append(
                            f"pnpm-version defaults to the major {want}, but "
                            f"action-setup@{sha[:7]} bootstraps {got} in {lock}, "
                            f"which is outside it. The range would not be "
                            f"satisfied, so self-update would really run. Move "
                            f"the default to the major the SHA bootstraps."
                        )
                elif got != want:
                    problems.append(
                        f"pnpm-version defaults to {want}, but "
                        f"action-setup@{sha[:7]} bootstraps {got} in {lock}. "
                        f"self-update would really run."
                    )

    return problems


# The two repo settings the versioning rule in CLAUDE.md now rests on. Neither
# lives in a file, which is exactly why they are asserted here: the rule "put
# the bump token in the PR title" is single-valued only while the squash subject
# comes from the PR title, and on the HUMAN merge path nothing passes
# `--subject` -- `merge-on-approval.yml` is unreachable for a human PR since
# pr-checks.yml gated the CI review to dependabot[bot]. With GitHub's default
# COMMIT_OR_PR_TITLE a one-commit PR lands the COMMIT's subject, so a #minor
# typed only in the title is dropped and a patch is cut, silently, on a release
# that reaches every caller.
#
# allow_rebase_merge belongs to the same rule: a rebase merge is replayed
# server-side and arrives UNSIGNED, which is what moving to squash was for.
# Leaving it enabled left that hole open on the path a human actually clicks.
# The condition the job carrying CHECK_MERGE_SETTINGS must run under. Pinned
# exactly rather than pattern-matched: the point is that a change to it is a
# change to when the settings are read at all, so it should have to be made
# here and in the fixtures as well as in the workflow.
MERGE_SETTINGS_JOB_IF = "github.event_name == 'push'"

MERGE_SETTINGS_EXPECTED = {
    "squash_merge_commit_title": "PR_TITLE",
    "allow_rebase_merge": False,
    # Without squash the other two say nothing -- and this is the merge method
    # merge-on-approval.yml defaults to, so a repo with it off cannot land a
    # bot merge at all.
    "allow_squash_merge": True,
}


def merge_settings_drift(files):
    """The squash subject and the merge methods are repo SETTINGS, not files.

    Runs only under CHECK_MERGE_SETTINGS=1, the same shape as the pnpm
    bootstrap leg: the pre-push hook cannot reach the API offline, and a check
    whose strictness comes from ambient CI is a green no-op waiting to happen.
    """
    problems = []

    # The gate assertion is EXACTLY-ONE, not every-step -- the opposite shape
    # from CHECK_PNPM_BOOTSTRAP above, and for a measured reason. Reading these
    # settings needs a token with the `contents: write` scope (2026-09-22: with
    # `contents: read` the three keys come back ABSENT; `.permissions.push` is
    # false either way, so the response cannot be used to tell), and permissions
    # are a JOB property. So the leg cannot ride the required `guard` job
    # without either over-scoping it or reddening it on every run. Requiring it
    # on every script-running step would demand exactly that.
    #
    # Requiring at least one is what stops the leg from being quietly dropped:
    # delete the job and nothing else in this file or the suite would notice.
    gate = "guard-tests.yml"
    gate_path = next((p for p in files if p.endswith(gate)), None)
    if gate_path is None:
        # Not borrowed from pnpm_pin_drift's identical branch. Coverage taken
        # from another function's assertion is coverage that disappears when
        # that function is refactored, and this one has its own reason to
        # exist: renaming this file leaves the settings asserted nowhere while
        # every suite stays green.
        problems.append(
            f"{gate} is missing, so the squash subject and the merge methods "
            f"are asserted nowhere in CI."
        )
    else:
        try:
            gate_doc = load(gate_path) or {}
        except yaml.YAMLError:
            gate_doc = {}
        enabled = []
        # The flag must come from the STEP, never from a workflow- or job-level
        # env: those reach EVERY step that runs the script, including the one in
        # the required `guard` job, whose `contents: read` token cannot read the
        # settings at all. That shape passes an exactly-one count while reddening
        # the one required status check on main on every run -- precisely the
        # failure the separate `settings` job exists to avoid, arriving through
        # the assertion meant to protect it.
        for scope_name, scope in (("workflow", gate_doc.get("env")),):
            if isinstance(scope, dict) and "CHECK_MERGE_SETTINGS" in scope:
                problems.append(
                    f"{gate_path}: CHECK_MERGE_SETTINGS is set at {scope_name} "
                    f"level, so it reaches every step that runs "
                    f"check-workflows.py -- including the one in the required "
                    f"`guard` job, which has no token scope to read the settings "
                    f"and would fail on every run. Set it on the settings step."
                )
        for job_name, job in (gate_doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            job_env = job.get("env")
            if isinstance(job_env, dict) and "CHECK_MERGE_SETTINGS" in job_env:
                problems.append(
                    f"{gate_path}: CHECK_MERGE_SETTINGS is set at job level on "
                    f"{job_name!r}, so it reaches every step in that job that "
                    f"runs check-workflows.py rather than the one that has the "
                    f"token scope for it. Set it on the settings step."
                )
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                if not SCRIPT_INVOCATION.search(str(step.get("run") or "")):
                    continue
                env = {}
                for scope in (gate_doc.get("env"), job_env, step.get("env")):
                    if isinstance(scope, dict):
                        env.update(scope)
                name = step.get("name") or "<unnamed step>"
                step_env = step.get("env")
                if isinstance(step_env, dict) and str(
                    step_env.get("CHECK_MERGE_SETTINGS", "")
                ) == "1":
                    enabled.append((job_name, name))
                if "MERGE_SETTINGS_SOFT" in env:
                    problems.append(
                        f"{gate_path}: the step {name!r} sets MERGE_SETTINGS_SOFT, "
                        f"which downgrades an unreadable settings response to a "
                        f"note. Only the pre-push hook may opt out."
                    )
        # Where the flag is WRITTEN is not whether it is ever RUN. The step
        # scoping above rejects an inherited flag and nothing else, so three
        # ordinary edits would leave this function green while the leg reads
        # nothing: swapping the job's `if:` to workflow_dispatch or adding a
        # paths filter to on.push (pr-checks.yml carries one, so it reads as
        # house style) -- silent, the job simply stops being reached on the
        # merges that matter; and cutting `contents: write` back to the repo
        # default as an obvious least-privilege tidy-up, which per the
        # measurement above turns the three keys ABSENT and reds main on every
        # push. The direct case matters most: moving the flag into the `guard`
        # step's own env satisfies an exactly-one count while reddening the one
        # required status check, which is the exact failure the separate job
        # exists to prevent. The permission assertion is what catches it, not
        # the placement one.
        push = (on_block(gate_doc) or {}).get("push")
        for job_name, step_name in enabled:
            job = (gate_doc.get("jobs") or {}).get(job_name) or {}
            perms = job.get("permissions")
            contents = perms.get("contents") if isinstance(perms, dict) else None
            if contents != "write":
                problems.append(
                    f"{gate_path}: the job {job_name!r} enables "
                    f"CHECK_MERGE_SETTINGS on {step_name!r} but grants "
                    f"`contents: {contents}` -- the three merge-setting keys "
                    f"come back ABSENT to anything below `contents: write` "
                    f"(measured 2026-09-22), so the leg can only report "
                    f"'could not check' and fail on every run."
                )
            if str(job.get("if") or "") != MERGE_SETTINGS_JOB_IF:
                problems.append(
                    f"{gate_path}: the job {job_name!r} enables "
                    f"CHECK_MERGE_SETTINGS under `if: {job.get('if')!r}` rather "
                    f"than `if: {MERGE_SETTINGS_JOB_IF}`. The leg exists to catch "
                    f"drift on the push that lands a merge, and a `pull_request` "
                    f"run cannot read the fields at all. Changing the condition "
                    f"deliberately means changing it here and in the fixtures too."
                )
            if not isinstance(push, dict):
                problems.append(
                    f"{gate_path}: the job {job_name!r} enables "
                    f"CHECK_MERGE_SETTINGS but this workflow has no `on.push` "
                    f"mapping, so the one event that reaches the leg never "
                    f"fires it."
                )
                continue
            for key in ("paths", "paths-ignore"):
                if key in push:
                    problems.append(
                        f"{gate_path}: `on.push.{key}` filters the event that "
                        f"reaches {job_name!r}, which enables "
                        f"CHECK_MERGE_SETTINGS. A merge touching no matching "
                        f"path would then land with the merge settings "
                        f"unread, and nothing would say so."
                    )
            branches = push.get("branches")
            if isinstance(branches, list) and "main" not in branches:
                problems.append(
                    f"{gate_path}: `on.push.branches` is {branches!r}, which "
                    f"excludes main -- the branch whose merges cut the "
                    f"releases {job_name!r} checks the settings for."
                )
        if not enabled:
            problems.append(
                f"{gate_path}: no step runs check-workflows.py with "
                f"CHECK_MERGE_SETTINGS=1 in its OWN env, so the squash subject "
                f"and the merge methods -- which decide which text cuts a "
                f"release -- are asserted nowhere in CI."
            )

    if os.environ.get("CHECK_MERGE_SETTINGS") != "1":
        return problems

    slug = os.environ.get("GITHUB_REPOSITORY") or "dustfeather/shared-workflows"
    soft = bool(os.environ.get("MERGE_SETTINGS_SOFT"))
    try:
        data = fetch_json(f"https://api.github.com/repos/{slug}")
    except Exception as exc:  # noqa: BLE001 — network, reported not raised
        msg = (
            f"merge settings: could not read {slug} ({exc}). The squash subject "
            f"decides which text cuts the release."
        )
        if soft:
            print(f"  note: {msg}", file=sys.stderr)
            return problems
        problems.append(msg)
        return problems

    # ABSENT is not FALSE. These fields are omitted entirely for a caller whose
    # token lacks the scope, and the response will NOT tell you which case you
    # are in: measured 2026-09-22 (branch probe/token-visibility, two jobs in
    # one run), an Actions token under `permissions: contents: read` gets a 200
    # with none of the keys while `contents: write` gets all three -- and
    # `.permissions.push` reads false in BOTH, so the body's own push flag is
    # not the discriminator. An unauthenticated GET behaves like the read case.
    # Treating a missing key as a mismatch would red a required check over a
    # token question; treating it as a pass would verify nothing whenever the
    # scope is wrong. Report it as "could not check", which is what it is.
    missing = [k for k in MERGE_SETTINGS_EXPECTED if k not in data]
    if missing:
        msg = (
            f"merge settings: {slug} answered without {', '.join(sorted(missing))} "
            f"-- the API omits these when the token lacks the scope, so nothing "
            f"was verified. In CI give the step a job with `contents: write`; "
            f"locally set MERGE_SETTINGS_SOFT=1."
        )
        if soft:
            print(f"  note: {msg}", file=sys.stderr)
            return problems
        problems.append(msg)
        return problems

    for key, want in MERGE_SETTINGS_EXPECTED.items():
        got = data.get(key)
        if got != want:
            problems.append(
                f"merge settings: {slug} has {key}={got!r}, expected {want!r}. "
                f"See the versioning section of CLAUDE.md -- the bump token is "
                f"read from the PR title, and these settings are what make that "
                f"true on the human merge path."
            )
    return problems


def runner_image_pin_drift(files, dockerfile="runner-image/Dockerfile"):
    """Cross-file: a version pinned in a workflow AND in the runner image.

    A workflow that installs a tool the runner image also bakes carries a
    second copy of that tool's version. Nothing errors when the two drift —
    the ARC pools and the GitHub-hosted runners simply run different builds of
    it, and the symptom is "the review agent got worse on one runner type",
    which nobody traces back to a version literal. That is the same silent
    shape the guard in merge-on-approval.yml and the pnpm three-way agreement
    exist to close, so it gets the same treatment: assert it statically rather
    than leave a comment asking the next reader to remember.

    Absent Dockerfile is not a finding — a checkout without it (a fixture
    tree, a consumer vendoring only the workflows) has nothing to disagree
    with. A literal present in the workflow with no matching ARG IS one: that
    is the shape where the Dockerfile stopped installing the tool and the
    workflow's pin became a lie nothing reads.
    """
    problems = []
    if not os.path.exists(dockerfile):
        return problems

    with open(dockerfile) as fh:
        docker = fh.read()

    for arg, pattern in PINNED_WITH_IMAGE.items():
        arg_match = re.search(rf"^ARG\s+{re.escape(arg)}=(\S+)\s*$", docker, re.M)
        for path in files:
            with open(path) as fh:
                text = fh.read()
            found = pattern.findall(text)
            if not found:
                continue
            if arg_match is None:
                problems.append(
                    f"{path}: pins {arg}={found[0]} but {dockerfile} declares "
                    f"no ARG {arg}. Either the image stopped installing it — "
                    f"in which case the workflow is the only installer and the "
                    f"comment claiming a second copy is wrong — or the ARG was "
                    f"renamed and this pin now tracks nothing."
                )
                continue
            want = arg_match.group(1).strip('"')
            for got in found:
                if got != want:
                    problems.append(
                        f"{path}: pins {arg}={got} but {dockerfile} bakes "
                        f"{want}. Move both together. Nothing fails when these "
                        f"drift: the hosted runners install {got} and the ARC "
                        f"pools already carry {want}, and the only symptom is "
                        f"two runner types quietly behaving differently."
                    )
    return problems


CLUSTER_WORKFLOWS = {"deploy-k8s.yml", "deploy-helm.yml"}


def arc_runner_defaults(files):
    """The v8 invariant, executable: `runner` defaults to a hosted label.

    Since v8 every `runner` input defaults to `ubuntu-latest`, with exactly
    two exceptions -- the workflows that talk to the cluster, which authenticate
    as the runner pod's own ServiceAccount token and dial an RFC1918 address no
    GitHub-hosted runner can route to. That was prose in CLAUDE.md and nothing
    asserted it, so a new workflow added by copying an existing input block
    would reintroduce an `arc-*` default in silence and ship green.

    Asserted in BOTH directions on purpose. An unexpected `arc-*` default is
    the regression; a cluster workflow quietly moved to a hosted default is the
    same mistake from the other side, and it fails at deploy time. Between them
    the exception list stops being a sentence and becomes the one place a third
    exception has to be justified.
    """
    problems = []
    for path in files:
        name = os.path.basename(path)
        try:
            with open(path) as fh:
                doc = yaml.safe_load(fh) or {}
        except yaml.YAMLError:
            continue  # the parse error is reported by main() already
        # `on` is the YAML 1.1 boolean True once parsed, which is why this
        # reads both spellings rather than the obvious one.
        trigger = doc.get("on", doc.get(True)) or {}
        if not isinstance(trigger, dict):
            continue
        call = trigger.get("workflow_call") or {}
        inputs = call.get("inputs") or {}
        # Absent is not the same as present-without-a-default, and conflating
        # them reported six workflows that declare no `runner` input at all.
        if "runner" not in inputs:
            continue
        runner = inputs.get("runner") or {}
        if "default" not in runner:
            # A `runner` input with no default at all is invisible to both
            # branches below, and it is not hypothetical: release-extension.yml
            # shipped `required: true` until v8. Every caller then has to name
            # a label, which in this account means an ARC one -- the v8 default
            # reaching nobody, without a single `arc-` string to match on.
            problems.append(
                f"{path}: declares a `runner` input with no default. Since v8 "
                f"every one defaults to a hosted label; without a default each "
                f"caller must name a pool, which is the pre-v8 fleet wearing a "
                f"different spelling. Give it `default: \"ubuntu-latest\"`."
            )
            continue
        default = str(runner["default"])
        cluster = name in CLUSTER_WORKFLOWS
        if default.startswith("arc-") and not cluster:
            problems.append(
                f"{path}: the `runner` input defaults to {default!r}. Since v8 "
                f"every runner default is a hosted label; the only workflows "
                f"exempt are {sorted(CLUSTER_WORKFLOWS)}, which need the "
                f"cluster's ServiceAccount token and an RFC1918 API address. A "
                f"caller wanting a pool passes the label itself."
            )
        elif cluster and not default.startswith("arc-"):
            problems.append(
                f"{path}: is a cluster workflow but its `runner` input defaults "
                f"to {default!r}. It authenticates as the runner pod's own "
                f"mounted ServiceAccount token and dials an RFC1918 address, "
                f"neither of which a GitHub-hosted runner has. Nothing here "
                f"fails until a deploy does."
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
    found.extend(runner_image_pin_drift(files))
    found.extend(arc_runner_defaults(files))
    found.extend(merge_settings_drift(files))

    for problem in found:
        print(f"  {problem}")

    if found:
        print(f"\n{len(found)} problem(s) in {len(files)} workflow(s)")
        return 1
    print(f"{len(files)} workflows OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
