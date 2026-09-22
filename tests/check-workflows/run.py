#!/usr/bin/env python3
"""Fixture cases for pnpm_pin_drift() in scripts/check-workflows.py.

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

print(f"\ncheck-workflows: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
