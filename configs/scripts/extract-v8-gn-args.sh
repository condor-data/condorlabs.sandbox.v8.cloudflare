#!/bin/bash
###############################################################################
# extract-v8-gn-args.sh — Extrai TODAS as GN build args do V8
#
# Fonte: BUILD.gn + gni/v8.gni (declare_args blocks)
# Metodo: regex nos declare_args { } blocks
# Output: configs/reference/v8-gn-args-all.txt
#
# Uso:
#   ./extract-v8-gn-args.sh /path/to/v8/source > configs/reference/v8-gn-args-all.txt
#
# NOTA: Isso extrai do SOURCE. Para a versão RESOLVIDA com defaults,
#       usar: gn args out/temp --list (requer gn + gclient sync)
###############################################################################
set -euo pipefail

V8_SRC="${1:?Uso: $0 /path/to/v8/source}"

echo "# V8 GN Build Args Reference (from source)"
echo "# Extraido: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "# Fonte: $V8_SRC/BUILD.gn + $V8_SRC/gni/v8.gni"
echo "#"

# Processar cada arquivo com declare_args
for gn_file in "$V8_SRC/BUILD.gn" "$V8_SRC/gni/v8.gni"; do
    if [ ! -f "$gn_file" ]; then
        echo "# AVISO: $gn_file nao encontrado" >&2
        continue
    fi

    echo ""
    echo "# === $(basename "$gn_file") ==="

    python3 << PYEOF
import re

with open("$gn_file") as f:
    content = f.read()

# Encontrar blocos declare_args() { ... }
blocks = re.findall(r'declare_args\(\)\s*\{([^}]+)\}', content, re.DOTALL)

args = []
for block in blocks:
    for line in block.split('\n'):
        line = line.strip()
        if '=' in line and not line.startswith('#') and not line.startswith('//'):
            if line.startswith('if ') or line.startswith('} else'):
                continue
            parts = line.split('=', 1)
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip().rstrip(',')
                if key and not key.startswith('#'):
                    args.append((key, val))

for key, val in sorted(args, key=lambda x: x[0]):
    print(f"{key} = {val}")

print(f"\n# Total: {len(args)} args em $(basename '$gn_file')")
PYEOF
done
