#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
case "${1:---fast}" in
  --fast)
    if (($#)); then shift; fi
    exec python3 "${ROOT}/tests/fast.py" "$@"
    ;;
  --prepare)
    shift
    exec python3 "${ROOT}/tests/fast.py" --prepare "$@"
    ;;
  --pr)
    shift
    exec python3 "${ROOT}/tests/run.py" --suite pr --layout shared "$@"
    ;;
  --release)
    shift
    exec python3 "${ROOT}/tests/run.py" --suite release --layout ci "$@"
    ;;
  --help|-h)
    cat <<'HELP'
Usage: bash tests/run.sh [--fast | --prepare | --pr | --release] [options]
  --fast     Default: small logic tests and syntax/lint; no application build.
  --prepare  One-time optional development-tool image setup.
  --pr       Core real-application journeys and required isolated packets.
  --release  Full shared journeys, configured clone and required packets.
             Add --upgrade-from IMAGE for the released-image upgrade check.
Legacy integration options (--only, --suite, --layout, etc.) remain explicit.
For all integration options: python3 tests/run.py --help
HELP
    ;;
  *) exec python3 "${ROOT}/tests/run.py" "$@" ;;
esac
