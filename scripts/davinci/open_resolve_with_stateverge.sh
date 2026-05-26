#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATEVERGE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export STATEVERGE_ROOT

INIT_PY="${STATEVERGE_ROOT}/scripts/davinci/init_davinci_storage.py"
python3 "${INIT_PY}"

CANONICAL_ROOT="/Volumes/SV_CACHE/davinci"
python3 -c "
import json
import os
import sys
from pathlib import Path

root = Path(os.environ['STATEVERGE_ROOT'])
candidates = (
    Path('/Volumes/SV_CACHE/davinci/logs/davinci_storage_init.json'),
    root / '_storage_fallback/sv_cache/davinci/logs/davinci_storage_init.json',
)
for path in candidates:
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8'))
        eff = data.get('effective_write_root') or data.get('active_root', '')
        if data.get('fallback_used') and eff:
            print(
                'DaVinci storage init used fallback; '
                f'effective_write_root={eff} '
                '(Media Storage prefs target remains /Volumes/SV_CACHE/davinci)',
                file=sys.stderr,
            )
        break
"

echo "ACTIVE_DAVINCI_ROOT=${CANONICAL_ROOT}"
open -a "DaVinci Resolve"
