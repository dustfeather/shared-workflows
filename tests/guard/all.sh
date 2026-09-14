#!/usr/bin/env bash
# Both harnesses for the bump-token guard in merge-on-approval.yml. Each
# re-extracts the code under test from the shipped YAML before running, so a
# stale copy cannot pass: run.sh exercises the SHELL against a stubbed `gh`,
# jqtest.sh exercises the `--jq` program against real jq, which the stub is
# structurally blind to (it only pattern-matches argv, and the jq program rides
# INSIDE argv).
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
"$D/run.sh"
"$D/jqtest.sh"
