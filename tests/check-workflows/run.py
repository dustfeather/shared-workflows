#!/usr/bin/env python3
"""Fixture cases for the cross-file checks in scripts/check-workflows.py.

Two of them: pnpm_pin_drift() and runner_image_pin_drift(). Both compare a
literal in a workflow against a second copy of the same value living in another
file, both are silent when they drift, and both ride the same required check.

Why this exists: that function rides on the `guard` job, which is the one
required status check on `main`, so a false positive in it reds every PR in
the repo at once and blocks the bot merge -- the same no-retry path tests/guard/
exists for. Until now it asserted only itself: the clean tree passing proved
the happy path and nothing else, and two of its regexes have already shipped
broken in this repo (one double-escaped and matched nothing; one matched prose
instead of `uses:` lines). A regex that silently stops matching is exactly the
shape a green run hides.

`fetch_json` is stubbed in every case that reaches the network, so the suite is
offline and deterministic. The cases that do NOT stub it never set
CHECK_PNPM_BOOTSTRAP, so no case can reach api.github.com by accident.
"""
import importlib.util
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location("cw", ROOT / "scripts" / "check-workflows.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

SHA = "0977fd99725f1db4007ccb2928dbb4e90d06cc86"
OTHER_SHA = "1111111111111111111111111111111111111111"


def workflow(default="9.99.9", ref=SHA, literal="9.99.9", calls=True):
    """A minimal workflow with the three legs of the pin, each independently
    removable -- which is what lets a case isolate one leg at a time."""
    out = [
        "on:",
        "  workflow_call:",
        "    inputs:",
    ]
    if default is not None:
        out += [
            "      pnpm-version:",
            "        type: string",
            f'        default: "{default}"',
        ]
    else:
        out += ["      runner:", "        type: string"]
    out += ["jobs:", "  test:", "    runs-on: ubuntu-latest", "    steps:"]
    if literal is not None:
        out += [
            "      - name: guard",
            "        env:",
            f'          PNPM_BOOTSTRAP_VERSION: "{literal}"',
            "        run: |",
            "          set -euo pipefail",
            "          true",
        ]
    if calls:
        out += [f"      - uses: pnpm/action-setup@{ref}"]
    return "\n".join(out) + "\n"


# The gate file every case needs: pnpm_pin_drift asserts guard-tests.yml sets
# CHECK_PNPM_BOOTSTRAP=1 and not PNPM_BOOTSTRAP_SOFT, so without it EVERY case
# would carry an extra unrelated finding and the assertions would be about the
# wrong thing.
def gate(check="1", soft=None, *, top_env=None, decoy=False, runs=True):
    """`decoy` adds a SECOND python step that does not run the script -- the
    shape that made a file-wide env union wrong, since the variable could sit
    on the wrong step. `top_env` sets workflow-level env, which Actions applies
    BEFORE the step's, so a step value must win over it."""
    def envblock(pairs, indent):
        if not pairs:
            return []
        return [f"{indent}env:"] + [f"{indent}  {k}: \"{v}\"" for k, v in pairs]

    step_env = []
    if check is not None:
        step_env.append(("CHECK_PNPM_BOOTSTRAP", check))
    if soft is not None:
        step_env.append(("PNPM_BOOTSTRAP_SOFT", soft))

    out = ["on:", "  push:"]
    out += envblock(list((top_env or {}).items()), "")
    out += ["jobs:", "  guard:", "    runs-on: ubuntu-latest", "    steps:"]
    if decoy:
        out += [
            "      - name: fixture cases",
            "        run: python3 tests/check-workflows/run.py",
        ]
    out += ["      - name: static checks"]
    out += envblock(step_env, "        ")
    out += [
        "        run: python3 scripts/check-workflows.py"
        if runs
        else "        run: echo 'python3 scripts/check-workflows.py'  # not run"
    ]
    return "\n".join(out) + "\n"


passed = failed = 0


def case(name, workflows, expect, *, env=None, fetch=None):
    """expect: a substring that must appear in the findings, or None for none.
    Prefix with '!' to assert ABSENCE while other findings may exist."""
    global passed, failed
    saved_env = {k: os.environ.get(k) for k in ("CHECK_PNPM_BOOTSTRAP", "PNPM_BOOTSTRAP_SOFT")}
    saved_fetch = cw.fetch_json
    try:
        for k, v in (env or {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in saved_env:
            if k not in (env or {}):
                os.environ.pop(k, None)
        if fetch is not None:
            cw.fetch_json = fetch
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for fname, body in workflows.items():
                p = pathlib.Path(d) / fname
                p.write_text(body)
                paths.append(str(p))
            problems = cw.pnpm_pin_drift(sorted(paths))
        joined = " | ".join(problems)
        if expect is None:
            ok = not problems
        elif expect.startswith("!"):
            ok = expect[1:] not in joined
        else:
            ok = expect in joined
        if ok:
            print(f"PASS  {name}")
            passed += 1
        else:
            print(f"FAIL  {name}\n      expected: {expect!r}\n      got: {joined or '(no findings)'}")
            failed += 1
    finally:
        cw.fetch_json = saved_fetch
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def lockfile(version):
    def _fetch(url):
        key = "@pnpm/exe" if "exe-lock" in url else "pnpm"
        return {"packages": {f"node_modules/{key}": {"version": version}}}
    return _fetch


# --- the happy path, which must stay quiet -------------------------------
case("clean tree: no findings",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()}, None)

# --- one leg at a time ---------------------------------------------------
case("two workflows, disagreeing defaults",
     {"guard-tests.yml": gate(), "a.yml": workflow(default="9.99.9"),
      "b.yml": workflow(default="12.0.0", literal="12.0.0")},
     "the `pnpm-version` defaults disagree")
case("floating ref instead of a SHA",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(ref="v6")},
     "is a floating ref")
case("two different SHAs",
     {"guard-tests.yml": gate(), "a.yml": workflow(), "b.yml": workflow(ref=OTHER_SHA)},
     "pinned to more than one SHA")
case("calls the action but ships no guard",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(literal=None)},
     "has no PNPM_BOOTSTRAP_VERSION guard step")
case("guard literal disagrees with the default",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(default="9.99.9", literal="10.0.0")},
     "while the `pnpm-version` default is")
case("guard literal with no input to mirror",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(default=None, calls=False)},
     "declares no `pnpm-version` input for it to mirror")

# --- the gate's own assertions -------------------------------------------
case("gate does not enable the bootstrap check",
     {"guard-tests.yml": gate(check=None), "node-test.yml": workflow()},
     "without CHECK_PNPM_BOOTSTRAP=1 in its effective env")
case("gate takes the soft opt-out",
     {"guard-tests.yml": gate(soft="1"), "node-test.yml": workflow()},
     "sets PNPM_BOOTSTRAP_SOFT")
case("gate file missing entirely",
     {"node-test.yml": workflow()},
     "guard-tests.yml is missing")
# The check must read parsed env:, not the file text -- a text scan fires on
# the comment explaining why the variable is absent, and the obvious "fix" is
# to delete the explanation.
case("a COMMENT naming the opt-out is not the opt-out",
     {"guard-tests.yml": gate().replace(
         "      - name: static checks",
         "      # No PNPM_BOOTSTRAP_SOFT here, on purpose.\n      - name: static checks"),
      "node-test.yml": workflow()},
     "!PNPM_BOOTSTRAP_SOFT")

# --- the network leg, stubbed --------------------------------------------
case("bootstrap matches the default",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     None, env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=lockfile("9.99.9"))
case("bootstrap drifted from the default",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     "self-update would really run",
     env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=lockfile("12.5.1"))

# --- the native bootstrap: a bare major is checked by CONTAINMENT ---------
# The default's SHAPE decides which lockfile is authoritative and which
# comparison applies. These cases exist because the failure they guard is
# silent: compare a bare major against the wrong lockfile and the check either
# reports drift that is not there, or passes while self-update really runs.

def native_lockfile(version, seen=None):
    """Stub that also RECORDS which bootstrap lockfile was requested.

    Keys off the URL exactly as `lockfile()` does: exe-lock.json stores the
    version under `@pnpm/exe`, the other two under `pnpm`. A stub that always
    answered `pnpm` would make the exact-version path read None and report
    "could not read the bootstrap version" -- a stub bug wearing the costume of
    a real finding.
    """
    def _fetch(url):
        name = url.rsplit("/", 1)[-1].split("?")[0]
        if seen is not None:
            seen.append(name)
        key = "@pnpm/exe" if "exe-lock" in name else "pnpm"
        return {"packages": {f"node_modules/{key}": {"version": version}}}
    return _fetch

case("bare major satisfied by the native bootstrap",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(default="12", literal="12")},
     None, env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=native_lockfile("12.3.4"))
case("bare major outside the bootstrap's major",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(default="13", literal="13")},
     "which is outside it",
     env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=native_lockfile("12.3.4"))
# A newer PATCH inside the major must stay quiet: that is the property that
# lets Dependabot move the action SHA without touching the default.
case("bootstrap moves within the major: still quiet",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(default="12", literal="12")},
     None, env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=native_lockfile("12.9.9"))
# Below 12 there is no native bootstrap, so a range is NOT satisfiable and the
# action would resolve it to the newest match and self-update.
case("bare major below 12 has no native path",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(default="11", literal="11")},
     "only treats a range as satisfiable on its native bootstrap for pnpm >= 12",
     env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=native_lockfile("12.3.4"))

# The one that catches reading the WRONG file. A bare major must consult
# native-lock.json and nothing else; an exact version must consult the other
# two and never native-lock.json.
_seen_native = []
case("a bare major reads native-lock.json only",
     {"guard-tests.yml": gate(), "node-test.yml": workflow(default="12", literal="12")},
     None, env={"CHECK_PNPM_BOOTSTRAP": "1"},
     fetch=native_lockfile("12.3.4", _seen_native))
assert _seen_native == ["native-lock.json"], (
    f"a bare major must read native-lock.json alone, read {_seen_native}")

_seen_exact = []
case("an exact version never reads native-lock.json",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     None, env={"CHECK_PNPM_BOOTSTRAP": "1"},
     fetch=native_lockfile("9.99.9", _seen_exact))
assert sorted(_seen_exact) == ["exe-lock.json", "pnpm-lock.json"], (
    f"an exact version must read the two non-native locks, read {_seen_exact}")


def _shape_changed(url):
    return {"packages": {"node_modules/pnpm": {"resolution": {}}}}

case("lockfile shape changed: not reported as drift",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     "could not read the bootstrap version",
     env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=_shape_changed)
case("lockfile shape changed: does NOT claim self-update",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     "!self-update would really run",
     env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=_shape_changed)

def _unreachable(url):
    raise OSError("nope")

case("unreachable lockfile is hard by default",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     "pnpm bootstrap left unverified",
     env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=_unreachable)
case("unreachable lockfile names the way out of the wedge",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     "an owner can merge directly meanwhile",
     env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=_unreachable)
case("PNPM_BOOTSTRAP_SOFT downgrades it to a note",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     "!pnpm bootstrap left unverified",
     env={"CHECK_PNPM_BOOTSTRAP": "1", "PNPM_BOOTSTRAP_SOFT": "1"}, fetch=_unreachable)
# A MISMATCH is hard whether or not the soft switch is set: soft covers "could
# not check", never "checked and disagreed".
case("SOFT does not silence a real drift",
     {"guard-tests.yml": gate(), "node-test.yml": workflow()},
     "self-update would really run",
     env={"CHECK_PNPM_BOOTSTRAP": "1", "PNPM_BOOTSTRAP_SOFT": "1"}, fetch=lockfile("12.5.1"))
# Refuse to fetch against a set that is not single-valued: the URL would be
# built from an arbitrary one of several SHAs.
case("bootstrap check refuses on a non-single-valued pin",
     {"guard-tests.yml": gate(), "a.yml": workflow(), "b.yml": workflow(ref=OTHER_SHA)},
     "the SHA or the default is not single-valued",
     env={"CHECK_PNPM_BOOTSTRAP": "1"}, fetch=lockfile("9.99.9"))

# --- the gate assertion must be STEP-scoped, not file-scoped -------------
# A union over every env: map in the file asserts only that the file SOMEWHERE
# sets the variable. guard-tests.yml has two python steps, so the union passes
# while the step that actually runs the script never reads the lockfiles.
DECOY_RUN = "        run: python3 tests/check-workflows/run.py"


def on_decoy(g, *pairs):
    env = "\n".join(f'          {k}: "{v}"' for k, v in pairs)
    return g.replace(DECOY_RUN, f"        env:\n{env}\n{DECOY_RUN}")


case("env on a DIFFERENT python step does not count",
     {"guard-tests.yml": on_decoy(gate(check=None, decoy=True), ("CHECK_PNPM_BOOTSTRAP", "1")),
      "node-test.yml": workflow()},
     "without CHECK_PNPM_BOOTSTRAP=1 in its effective env")
case("SOFT on a DIFFERENT step does not fire the finding",
     {"guard-tests.yml": on_decoy(gate(decoy=True), ("PNPM_BOOTSTRAP_SOFT", "1")),
      "node-test.yml": workflow()},
     "!PNPM_BOOTSTRAP_SOFT")
# Actions applies workflow env BEFORE the step's, so the step wins. Getting the
# order backwards turns a correct per-step "1" into a false failure on the one
# required check on main.
case("step env beats a wrong workflow-level value",
     {"guard-tests.yml": gate(check="1", top_env={"CHECK_PNPM_BOOTSTRAP": "0"}),
      "node-test.yml": workflow()},
     None)
case("workflow-level value alone still reaches the step",
     {"guard-tests.yml": gate(check=None, top_env={"CHECK_PNPM_BOOTSTRAP": "1"}),
      "node-test.yml": workflow()},
     None)
# Naming the script is not running it: the finding must be about the gate not
# gating, never about a step that merely mentions the path.
case("no step actually runs the script",
     {"guard-tests.yml": gate(runs=False), "node-test.yml": workflow()},
     "no step runs scripts/check-workflows.py")
case("a mention is not an invocation, so no env finding",
     {"guard-tests.yml": gate(runs=False), "node-test.yml": workflow()},
     "!effective env")


# --- runner_image_pin_drift() --------------------------------------------
# Separate harness because this check takes a second file that is NOT a
# workflow, and because a missing one is a legitimate no-finding rather than
# an error -- the distinction the cases below exist to pin down.
def image_case(name, workflows, dockerfile, expect):
    """dockerfile: the ARG file's contents, or None to leave it absent."""
    global passed, failed
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for fname, body in workflows.items():
            p = pathlib.Path(d) / fname
            p.write_text(body)
            paths.append(str(p))
        docker = str(pathlib.Path(d) / "Dockerfile")
        if dockerfile is not None:
            pathlib.Path(docker).write_text(dockerfile)
        problems = cw.runner_image_pin_drift(sorted(paths), dockerfile=docker)
    joined = " | ".join(problems)
    ok = (not problems) if expect is None else (expect in joined)
    if ok:
        print(f"PASS  {name}")
        passed += 1
    else:
        print(f"FAIL  {name}\n      expected: {expect!r}\n      got: {joined or '(no findings)'}")
        failed += 1


def crg_workflow(literal='"2.3.5"', prose=None):
    out = ["jobs:", "  review:", "    runs-on: ubuntu-latest", "    steps:"]
    if literal is not None:
        out += [
            "      - name: Ensure code-review-graph is installed",
            "        env:",
            f"          CRG_VERSION: {literal}",
            "        run: pip install code-review-graph",
        ]
    if prose is not None:
        out += ["      - name: review", "        with:", f"          prompt: {prose}"]
    return "\n".join(out) + "\n"


image_case("agreeing pins: no findings",
           {"a.yml": crg_workflow()}, "ARG CRG_VERSION=2.3.5\n", None)
image_case("drifted pins",
           {"a.yml": crg_workflow(literal='"2.4.0"')}, "ARG CRG_VERSION=2.3.5\n",
           "pins CRG_VERSION=2.4.0")
# A checkout without the image -- a fixture tree, or a consumer vendoring only
# the workflows -- has nothing to disagree with, so it must stay quiet. Getting
# this wrong reds the one required check for everyone who does not build the
# image.
image_case("absent Dockerfile is not a finding",
           {"a.yml": crg_workflow()}, None, None)
# The other direction: the image stopped installing it, so the workflow's
# "second copy" comment is now a lie and the pin tracks nothing.
image_case("Dockerfile present but the ARG is gone",
           {"a.yml": crg_workflow()}, "ARG OTHER=1\n",
           "declares no ARG CRG_VERSION")
image_case("no literal in any workflow: nothing to compare",
           {"a.yml": crg_workflow(literal=None)}, "ARG CRG_VERSION=2.3.5\n", None)
# The anchor is the env KEY, not the version string. This workflow ships a
# prompt quoting an unrelated 2.4.0; reading that as the pin would fail the
# required check over prose, which is the shape that has already shipped twice
# in this file's sibling regexes.
image_case("a version in prose is not the pin",
           {"a.yml": crg_workflow(prose="please use 2.4.0 of something else")},
           "ARG CRG_VERSION=2.3.5\n", None)
image_case("a quoted ARG value still agrees",
           {"a.yml": crg_workflow()}, 'ARG CRG_VERSION="2.3.5"\n', None)
image_case("an unquoted workflow literal still matches",
           {"a.yml": crg_workflow(literal="2.3.5")}, "ARG CRG_VERSION=2.3.5\n", None)

# --- arc_runner_defaults() -----------------------------------------------
# Third harness because the exception list is keyed on the BASENAME, so a case
# is a filename plus a default, and the fixture has to control both.
def arc_case(name, fname, default, expect):
    """default: the `runner` input's default, or None to omit the input."""
    global passed, failed
    out = ["on:", "  workflow_call:"]
    if default is not None:
        out += ["    inputs:", "      runner:", "        type: string",
                f'        default: "{default}"']
    out += ["jobs:", "  a:", "    runs-on: ubuntu-latest", "    steps:",
            "      - run: true"]
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / fname
        p.write_text("\n".join(out) + "\n")
        problems = cw.arc_runner_defaults([str(p)])
    joined = " | ".join(problems)
    ok = (not problems) if expect is None else (expect in joined)
    if ok:
        print(f"PASS  {name}")
        passed += 1
    else:
        print(f"FAIL  {name}\n      expected: {expect!r}\n      got: {joined or '(no findings)'}")
        failed += 1


arc_case("hosted default on an ordinary workflow", "node-test.yml", "ubuntu-latest", None)
# The regression this exists for: a new workflow added by copying an existing
# input block, which ships green while quietly putting its callers back on a
# pool.
arc_case("an arc-* default anywhere else is a finding", "new-thing.yml",
         "arc-df-shared-workflows", "Since v8 every runner default is a hosted label")
arc_case("deploy-k8s keeps its pool", "deploy-k8s.yml", "arc-df-shared-workflows", None)
arc_case("deploy-helm keeps its pool", "deploy-helm.yml", "arc-df-shared-workflows", None)
# The same mistake from the other side: "tidying" a cluster workflow onto the
# hosted default costs nothing here and fails at deploy time, against an
# RFC1918 address with no token.
arc_case("a cluster workflow moved to hosted is a finding", "deploy-k8s.yml",
         "ubuntu-latest", "is a cluster workflow")
arc_case("no runner input at all: nothing to assert", "tag-release.yml", None, None)


def arc_nodefault_case(name, fname, expect):
    """A `runner` input that exists but declares no default -- what
    release-extension.yml shipped until v8, and the shape with no `arc-`
    string for either branch above to match on."""
    global passed, failed
    body = "\n".join([
        "on:", "  workflow_call:", "    inputs:", "      runner:",
        "        type: string", "        required: true",
        "jobs:", "  a:", "    runs-on: ubuntu-latest", "    steps:",
        "      - run: true",
    ]) + "\n"
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / fname
        p.write_text(body)
        problems = cw.arc_runner_defaults([str(p)])
    joined = " | ".join(problems)
    ok = (not problems) if expect is None else (expect in joined)
    if ok:
        print(f"PASS  {name}")
        passed += 1
    else:
        print(f"FAIL  {name}\n      expected: {expect!r}\n      got: {joined or '(no findings)'}")
        failed += 1


arc_nodefault_case("required: true with no default is a finding", "a.yml",
                   "declares a `runner` input with no default")
# Absent is not the same as present-without-a-default: six of this repo's own
# workflows declare no `runner` input, and conflating the two reported every
# one of them.
arc_case("a workflow with no workflow_call trigger is quiet", "tag-release.yml", None, None)
# A self-hosted label that is not an ARC scale set is out of scope on purpose:
# the check asserts the fleet default, not a taxonomy of labels.
arc_case("an unrelated label is not matched", "a.yml", "macos-14", None)

# --- the repo SETTINGS leg: squash subject and merge methods --------------
# These live in no file, so the only thing a fixture can pin is how the check
# reacts to each API shape. The one that matters is the third: the fields are
# ABSENT, not false, for a caller without push access (measured on this public
# repo unauthenticated), so "missing" must read as "could not check" rather
# than as a pass -- otherwise the assertion quietly verifies nothing the day
# the token changes, which is the failure mode it exists to prevent.
GOOD_SETTINGS = {
    "squash_merge_commit_title": "PR_TITLE",
    "allow_rebase_merge": False,
    "allow_squash_merge": True,
}


def merge_gate(check="1", soft=None, *, top_env=None, job_env=None):
    """guard-tests.yml as merge_settings_drift() reads it: one step running the
    script, carrying (or not) the two variables this leg is gated on."""
    env = []
    if check is not None:
        env.append(("CHECK_MERGE_SETTINGS", check))
    if soft is not None:
        env.append(("MERGE_SETTINGS_SOFT", soft))
    out = ["on:", "  push:"]
    if top_env:
        out += ["env:"] + [f'  {k}: "{v}"' for k, v in top_env.items()]
    out += ["jobs:", "  guard:", "    runs-on: ubuntu-latest"]
    if job_env:
        out += ["    env:"] + [f'      {k}: "{v}"' for k, v in job_env.items()]
    out += ["    steps:", "      - name: static checks"]
    if env:
        out += ["        env:"] + [f'          {k}: "{v}"' for k, v in env]
    out += ["        run: python3 scripts/check-workflows.py"]
    return "\n".join(out) + "\n"


def merge_case(name, expect, *, settings=GOOD_SETTINGS, env=None, gate_yaml=None,
               raise_exc=None, no_gate=False):
    global passed, failed
    keys = ("CHECK_MERGE_SETTINGS", "MERGE_SETTINGS_SOFT", "GITHUB_REPOSITORY")
    saved = {k: os.environ.get(k) for k in keys}
    saved_fetch = cw.fetch_json
    try:
        for k in keys:
            os.environ.pop(k, None)
        for k, v in ({"CHECK_MERGE_SETTINGS": "1"} | (env or {})).items():
            if v is not None:
                os.environ[k] = v
        os.environ["GITHUB_REPOSITORY"] = "o/r"

        def _fetch(url):
            # Pin the URL. A stub answering ANY url leaves every case below
            # passing unchanged if the check reads the wrong endpoint, or lets
            # the hardcoded "dustfeather/shared-workflows" fallback win over a
            # misspelled GITHUB_REPOSITORY -- and the "could not read o/r"
            # assertions would not catch that, because the message is formatted
            # from the slug rather than from what was actually fetched.
            assert url == "https://api.github.com/repos/o/r", url
            if raise_exc is not None:
                raise raise_exc
            return dict(settings)
        cw.fetch_json = _fetch
        with tempfile.TemporaryDirectory() as d:
            # no_gate writes the same YAML under a DIFFERENT name: the point is
            # a repo where guard-tests.yml is absent, not one with no workflows
            # at all, so the case cannot pass for the trivial reason.
            gp = pathlib.Path(d) / ("other.yml" if no_gate else "guard-tests.yml")
            gp.write_text(gate_yaml if gate_yaml is not None else merge_gate())
            problems = cw.merge_settings_drift([str(gp)])
        joined = " | ".join(problems)
        ok = (not problems) if expect is None else (expect in joined)
        if ok:
            print(f"PASS  {name}")
            passed += 1
        else:
            print(f"FAIL  {name}\n      expected: {expect!r}\n      got: {joined or '(no findings)'}")
            failed += 1
    finally:
        cw.fetch_json = saved_fetch
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


merge_case("settings as documented: quiet", None)
merge_case("GitHub's default squash title is a finding", "squash_merge_commit_title='COMMIT_OR_PR_TITLE'",
           settings=GOOD_SETTINGS | {"squash_merge_commit_title": "COMMIT_OR_PR_TITLE"})
# An unsigned replay on the path a human actually clicks -- the hole the move
# to squash was for.
merge_case("rebase merge re-enabled is a finding", "allow_rebase_merge=True",
           settings=GOOD_SETTINGS | {"allow_rebase_merge": True})
merge_case("squash turned off is a finding", "allow_squash_merge=False",
           settings=GOOD_SETTINGS | {"allow_squash_merge": False})
# The important one. Absent fields must not read as a pass.
merge_case("fields absent (no push access) is 'could not check', not silence",
           "nothing was verified", settings={"name": "r"})
merge_case("absent fields are a NOTE under the local opt-out", None,
           settings={"name": "r"}, env={"MERGE_SETTINGS_SOFT": "1"})
merge_case("an unreachable API is a finding by default", "could not read o/r",
           raise_exc=RuntimeError("boom"))
merge_case("an unreachable API is a note under the local opt-out", None,
           raise_exc=RuntimeError("boom"), env={"MERGE_SETTINGS_SOFT": "1"})
# Nothing is read at all without the flag, so the pre-push hook stays offline.
merge_case("no flag: the network leg does not run", None,
           env={"CHECK_MERGE_SETTINGS": None}, gate_yaml=merge_gate(),
           raise_exc=RuntimeError("fetch_json must not be called"))
# The gate assertions, same shape as the pnpm pair: strictness that the gate
# itself can silently drop is not strictness.
# The gate assertion is exactly-ONE, not every-step: the leg needs a job with
# contents: write to read anything, so it cannot ride the required guard job.
merge_case("no step enables the leg at all is a finding",
           "asserted nowhere in CI", gate_yaml=merge_gate(check=None))
merge_case("the gate taking the opt-out is a finding", "sets MERGE_SETTINGS_SOFT",
           gate_yaml=merge_gate(soft="1"))
# The shape this repo actually ships: two script-running steps, only the second
# carrying the flag. Requiring it on EVERY step would report this as broken,
# which is how the check would have forced itself onto the required job.
merge_case("a second script step without the flag is not a finding", None,
           gate_yaml=merge_gate(check=None) + "\n".join([
               "  settings:",
               "    runs-on: ubuntu-latest",
               "    steps:",
               "      - name: settings",
               "        env:",
               '          CHECK_MERGE_SETTINGS: "1"',
               "        run: python3 scripts/check-workflows.py",
           ]) + "\n")
# ...and the opt-out is still refused wherever it sits, including on that
# second step, so "put it on the other one" is not a way round it.
merge_case("the opt-out on the SECOND step is still a finding",
           "sets MERGE_SETTINGS_SOFT",
           gate_yaml=merge_gate(check=None) + "\n".join([
               "  settings:",
               "    runs-on: ubuntu-latest",
               "    steps:",
               "      - name: settings",
               "        env:",
               '          CHECK_MERGE_SETTINGS: "1"',
               '          MERGE_SETTINGS_SOFT: "1"',
               "        run: python3 scripts/check-workflows.py",
           ]) + "\n")
# A step that only MENTIONS the script is not a step that runs it -- the same
# trap the pnpm gate assertion has a case for.
# Scope matters, not just presence. A workflow- or job-level flag reaches EVERY
# script step -- including the one in the required `guard` job, whose
# `contents: read` token cannot read the settings -- so it satisfies a naive
# exactly-one count while reddening the required check on main on every run.
# That is the failure the separate `settings` job exists to prevent, arriving
# through the assertion meant to protect it.
merge_case("a workflow-level flag is a finding", "set at workflow level",
           gate_yaml=merge_gate(check=None,
                                top_env={"CHECK_MERGE_SETTINGS": "1"}))
merge_case("a job-level flag is a finding", "set at job level on 'guard'",
           gate_yaml=merge_gate(check=None,
                                job_env={"CHECK_MERGE_SETTINGS": "1"}))
# ...and inheriting it does NOT satisfy the exactly-one requirement either, or
# the finding above would be paired with a silent pass on the real question.
merge_case("an inherited flag does not count as enabling the leg",
           "asserted nowhere in CI",
           gate_yaml=merge_gate(check=None,
                                top_env={"CHECK_MERGE_SETTINGS": "1"}))
# The gate file itself gone. pnpm_pin_drift reports this too, so today the repo
# would not be blind -- but borrowed coverage vanishes when the lender is
# refactored.
merge_case("guard-tests.yml absent entirely is a finding",
           "guard-tests.yml is missing", no_gate=True)
merge_case("a mention of the script does not count as enabling the leg",
           "asserted nowhere in CI",
           gate_yaml="\n".join([
               "on:", "  push:", "jobs:", "  guard:",
               "    runs-on: ubuntu-latest", "    steps:",
               "      - name: not really",
               "        env:",
               '          CHECK_MERGE_SETTINGS: "1"',
               "        run: echo 'python3 scripts/check-workflows.py'",
           ]) + "\n")

print(f"\ncheck-workflows: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
