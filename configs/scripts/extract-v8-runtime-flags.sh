#!/bin/bash
###############################################################################
# extract-v8-runtime-flags.sh — Extrai TODAS as runtime flags do V8
#
# Fonte: src/flags/flag-definitions.h
# Metodo: Python regex multi-linha (sed nao funciona para macros multi-linha)
# Output: nome, tipo, default, descricao — uma flag por linha
#
# Uso:
#   ./extract-v8-runtime-flags.sh /path/to/v8/source
#   ./extract-v8-runtime-flags.sh /path/to/v8/source sandbox
#   ./extract-v8-runtime-flags.sh /path/to/v8/source wasm
###############################################################################
set -euo pipefail

V8_SRC="${1:?ERRO: Uso: $0 /path/to/v8/source [filtro]}"
FILTER="${2:-}"

FLAGS_FILE="$V8_SRC/src/flags/flag-definitions.h"

if [ ! -f "$FLAGS_FILE" ]; then
    echo "ERRO: $FLAGS_FILE nao encontrado!" >&2
    exit 1
fi

python3 << PYEOF
import re, sys

with open("$FLAGS_FILE") as f:
    content = f.read()

# Regex para DEFINE_TYPE(name, default, "description")
# Flags podem estar em múltiplas linhas
patterns = [
    (r'DEFINE_BOOL\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]*)"', "BOOL"),
    (r'DEFINE_BOOL_READONLY\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]*)"', "BOOL_RO"),
    (r'DEFINE_DEBUG_BOOL\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]*)"', "DEBUG_BOOL"),
    (r'DEFINE_INT\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]*)"', "INT"),
    (r'DEFINE_UINT\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]*)"', "UINT"),
    (r'DEFINE_FLOAT\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]*)"', "FLOAT"),
    (r'DEFINE_STRING\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]*)"', "STRING"),
    (r'DEFINE_EXPERIMENTAL_FEATURE\(\s*(\w+)\s*,\s*"([^"]*)"', "EXPERIMENTAL"),
]

flags = {}
for pattern, flag_type in patterns:
    for match in re.finditer(pattern, content, re.DOTALL):
        if flag_type == "EXPERIMENTAL":
            name, desc = match.group(1), match.group(2)
            default = "false"
        else:
            name, default, desc = match.group(1), match.group(2).strip(), match.group(3)
        flags[name] = {"type": flag_type, "default": default, "desc": desc}

# Filtrar se pedido
filt = "$FILTER"
if filt:
    flags = {k: v for k, v in flags.items() if filt.lower() in k.lower() or filt.lower() in v["desc"].lower()}

# Header
print(f"# V8 Runtime Flags Reference")
print(f"# Fonte: flag-definitions.h")
print(f"# Total extraido: {len(flags)} flags")
if filt:
    print(f"# Filtro: {filt}")
print(f"#")
print(f"# Formato: --flag_name  [TYPE]  default=VALUE  // descricao")
print()

# Organizar por categoria
categories = {
    "SANDBOX": lambda k, v: "sandbox" in k,
    "WASM": lambda k, v: "wasm" in k,
    "MAGLEV": lambda k, v: "maglev" in k,
    "TURBOFAN": lambda k, v: "turbo" in k,
    "SPARKPLUG": lambda k, v: "sparkplug" in k,
    "GC": lambda k, v: k.startswith("gc") or "gc_" in k or "scaveng" in k,
    "TRACE": lambda k, v: "trace" in k,
    "PRINT": lambda k, v: "print" in k or "dump" in k,
}

categorized = set()
for cat_name, cat_fn in categories.items():
    cat_flags = {k: v for k, v in flags.items() if cat_fn(k, v)}
    if cat_flags:
        print(f"=== {cat_name} ({len(cat_flags)}) ===")
        for name in sorted(cat_flags):
            f = cat_flags[name]
            print(f"  --{name}  [{f['type']}]  default={f['default']}  // {f['desc']}")
            categorized.add(name)
        print()

# Resto
other = {k: v for k, v in flags.items() if k not in categorized}
if other:
    print(f"=== OTHER ({len(other)}) ===")
    for name in sorted(other):
        f = other[name]
        print(f"  --{name}  [{f['type']}]  default={f['default']}  // {f['desc']}")

print()
print(f"# TOTAL: {len(flags)} flags")
PYEOF
