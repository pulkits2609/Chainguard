#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
command -v python3 >/dev/null || { echo 'Install Python 3.11+ first.' >&2; exit 1; }
exec python3 deploy.py "$@"
