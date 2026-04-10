#!/bin/bash
###############################################################################
# extract-lol-html-info.sh — Extrai info do lol-html (features, deps, debug)
#
# Fonte: Cargo.toml, DEVELOPING.md, src/
# Output: configs/reference/lol-html-info.txt
#
# Uso:
#   ./extract-lol-html-info.sh /path/to/lol-html > configs/reference/lol-html-info.txt
#
# Se o lol-html nao estiver clonado localmente, clona do GitHub.
###############################################################################
set -euo pipefail

LOL_SRC="${1:-}"

if [ -z "$LOL_SRC" ] || [ ! -d "$LOL_SRC" ]; then
    echo "lol-html source nao encontrado em '$LOL_SRC'" >&2
    echo "Clonando do GitHub..." >&2
    TMPDIR=$(mktemp -d)
    git clone --depth 1 https://github.com/cloudflare/lol-html.git "$TMPDIR/lol-html" 2>&1 >&2
    LOL_SRC="$TMPDIR/lol-html"
fi

echo "# lol-html Reference"
echo "# Fonte: $LOL_SRC"
echo "# Extraido: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo ""

echo "=== Cargo.toml (root) ==="
if [ -f "$LOL_SRC/Cargo.toml" ]; then
    cat "$LOL_SRC/Cargo.toml"
else
    echo "NAO ENCONTRADO"
fi

echo ""
echo "=== Cargo.toml (lol_html crate) ==="
for f in "$LOL_SRC/lol_html/Cargo.toml" "$LOL_SRC/lol-html/Cargo.toml"; do
    if [ -f "$f" ]; then
        cat "$f"
        break
    fi
done

echo ""
echo "=== Cargo.toml (c-api) ==="
for f in "$LOL_SRC/c-api/Cargo.toml" "$LOL_SRC/lol_html_c_api/Cargo.toml"; do
    if [ -f "$f" ]; then
        cat "$f"
        break
    fi
done

echo ""
echo "=== DEVELOPING.md ==="
if [ -f "$LOL_SRC/DEVELOPING.md" ]; then
    cat "$LOL_SRC/DEVELOPING.md"
elif [ -f "$LOL_SRC/CONTRIBUTING.md" ]; then
    echo "(DEVELOPING.md nao encontrado, mostrando CONTRIBUTING.md)"
    cat "$LOL_SRC/CONTRIBUTING.md"
else
    echo "NAO ENCONTRADO"
fi

echo ""
echo "=== Features (from Cargo.toml [features]) ==="
for f in "$LOL_SRC/Cargo.toml" "$LOL_SRC/lol_html/Cargo.toml"; do
    if [ -f "$f" ]; then
        python3 -c "
import re
with open('$f') as fh:
    content = fh.read()
match = re.search(r'\[features\](.*?)(\n\[|\Z)', content, re.DOTALL)
if match:
    print(match.group(1).strip())
else:
    print('Nenhuma feature section encontrada em $f')
" 2>/dev/null || true
    fi
done

echo ""
echo "=== Fuzz targets ==="
if [ -d "$LOL_SRC/fuzz" ]; then
    ls "$LOL_SRC/fuzz/fuzz_targets/" 2>/dev/null || ls "$LOL_SRC/fuzz/" 2>/dev/null
else
    echo "Diretorio fuzz/ nao encontrado"
fi

echo ""
echo "=== Testes ==="
find "$LOL_SRC" -name "*test*" -type f -not -path "*/target/*" -not -path "*/.git/*" 2>/dev/null | head -20

# Cleanup
if [ -n "${TMPDIR:-}" ]; then
    rm -rf "$TMPDIR"
fi
