#!/usr/bin/env bash
set -euo pipefail
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required.")'
exec python3 "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/setup/linux/play.py" "$@"
