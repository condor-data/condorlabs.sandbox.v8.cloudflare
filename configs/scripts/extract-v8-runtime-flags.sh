#!/bin/bash
###############################################################################
# extract-v8-runtime-flags.sh — Extrai TODAS as runtime flags do V8
#
# Fonte: src/flags/flag-definitions.h
# Metodo: grep por DEFINE_BOOL, DEFINE_INT, DEFINE_STRING, etc.
# Output: configs/reference/v8-runtime-flags.txt
#
# Uso:
#   ./extract-v8-runtime-flags.sh /path/to/v8/source > configs/reference/v8-runtime-flags.txt
#   ./extract-v8-runtime-flags.sh /path/to/v8/source --category sandbox
###############################################################################
set -euo pipefail

V8_SRC="${1:?Uso: $0 /path/to/v8/source [--category sandbox|wasm|maglev|turbofan|all]}"
CATEGORY="${2:---category}"
FILTER="${3:-all}"

FLAGS_FILE="$V8_SRC/src/flags/flag-definitions.h"

if [ ! -f "$FLAGS_FILE" ]; then
    echo "ERRO: $FLAGS_FILE nao encontrado!" >&2
    exit 1
fi

echo "# V8 Runtime Flags Reference"
echo "# Fonte: $FLAGS_FILE"
echo "# Extraido: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "# Linhas no arquivo: $(wc -l < "$FLAGS_FILE")"
echo "#"

# Contar por tipo
echo "# Total flags:"
echo "#   DEFINE_BOOL:   $(grep -c 'DEFINE_BOOL(' "$FLAGS_FILE" || echo 0)"
echo "#   DEFINE_INT:    $(grep -c 'DEFINE_INT(' "$FLAGS_FILE" || echo 0)"
echo "#   DEFINE_UINT:   $(grep -c 'DEFINE_UINT(' "$FLAGS_FILE" || echo 0)"
echo "#   DEFINE_FLOAT:  $(grep -c 'DEFINE_FLOAT(' "$FLAGS_FILE" || echo 0)"
echo "#   DEFINE_STRING: $(grep -c 'DEFINE_STRING(' "$FLAGS_FILE" || echo 0)"
echo "#   DEFINE_BOOL_READONLY: $(grep -c 'DEFINE_BOOL_READONLY(' "$FLAGS_FILE" || echo 0)"
echo "#   DEFINE_DEBUG_BOOL: $(grep -c 'DEFINE_DEBUG_BOOL(' "$FLAGS_FILE" || echo 0)"
echo "#   DEFINE_EXPERIMENTAL_FEATURE: $(grep -c 'DEFINE_EXPERIMENTAL_FEATURE(' "$FLAGS_FILE" || echo 0)"
TOTAL=$(grep -cE 'DEFINE_(BOOL|INT|UINT|FLOAT|STRING|BOOL_READONLY|DEBUG_BOOL|EXPERIMENTAL_FEATURE)\(' "$FLAGS_FILE" || echo 0)
echo "#   TOTAL: $TOTAL"
echo ""

case "$FILTER" in
    sandbox)
        echo "=== SANDBOX FLAGS ==="
        grep -n -i 'sandbox' "$FLAGS_FILE" | grep 'DEFINE_'
        ;;
    wasm)
        echo "=== WASM FLAGS ==="
        grep -n -i 'wasm' "$FLAGS_FILE" | grep 'DEFINE_'
        ;;
    maglev)
        echo "=== MAGLEV FLAGS ==="
        grep -n -i 'maglev' "$FLAGS_FILE" | grep 'DEFINE_'
        ;;
    turbofan)
        echo "=== TURBOFAN/TURBOSHAFT FLAGS ==="
        grep -n -i 'turbo' "$FLAGS_FILE" | grep 'DEFINE_'
        ;;
    all|*)
        echo "=== ALL FLAGS (name, type, default, description) ==="
        # Extrair: DEFINE_TYPE(name, default, "description")
        grep -E 'DEFINE_(BOOL|INT|UINT|FLOAT|STRING|BOOL_READONLY|DEBUG_BOOL)\(' "$FLAGS_FILE" | \
            sed 's/.*DEFINE_\([A-Z_]*\)(\([^,]*\),\s*\([^,]*\),\s*\(.*\)/\1 \2 \3 \4/' | \
            sort -k2
        echo ""
        echo "=== EXPERIMENTAL FEATURES ==="
        grep 'DEFINE_EXPERIMENTAL_FEATURE(' "$FLAGS_FILE" | \
            sed 's/.*DEFINE_EXPERIMENTAL_FEATURE(\([^,]*\),\s*\(.*\)/EXPERIMENTAL \1 \2/'
        ;;
esac
