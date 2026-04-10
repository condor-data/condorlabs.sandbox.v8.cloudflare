#!/bin/bash
###############################################################################
# extract-v8-runtime-flags.sh
#
# Extrai runtime flags do V8 de flag-definitions.h via regex.
#
# LIMITACAO DOCUMENTADA: Extracao estatica NAO captura 100% das flags.
# Flags geradas por macros FOREACH (ex: experimental_wasm_jspi) e flags
# dentro de #ifdef condicionais complexos podem nao ser capturadas.
# Para a lista COMPLETA e AUTORITATIVA, usar:
#   d8 --help           (do binario compilado)
#   gn args --list      (com gn bootstrapped)
#
# Margem de erro estimada: ~5-8% das flags podem nao ser capturadas.
###############################################################################
set -euo pipefail

V8_SRC="${1:?ERRO: Uso: $0 /path/to/v8/source [filtro]}"
FILTER="${2:-}"
FLAGS_FILE="$V8_SRC/src/flags/flag-definitions.h"

if [ ! -f "$FLAGS_FILE" ]; then
    echo "ERRO: $FLAGS_FILE nao encontrado!" >&2
    exit 1
fi

python3 - "$FLAGS_FILE" "$FILTER" << 'PYEOF'
import re, sys

flags_file = sys.argv[1]
filt = sys.argv[2] if len(sys.argv) > 2 else ""

with open(flags_file) as f:
    content = f.read()

# Macro params a excluir
macro_params = set()
for m in re.finditer(r'#define\s+(?:DEFINE|DECL)_\w+\((\w+)', content):
    macro_params.add(m.group(1))

flags = {}

# Extrair com re.DOTALL para pegar multi-linha
# Prioridade: primeiro match por nome ganha (evita duplicatas de #ifdef/#else)
for t in ["BOOL", "BOOL_READONLY", "DEBUG_BOOL", "INT", "UINT", "FLOAT", "STRING"]:
    for m in re.finditer(rf'DEFINE_{t}\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*"([^"]*)"', content, re.DOTALL):
        name = m.group(1)
        if name not in flags and name not in macro_params:
            flags[name] = {"type": t, "default": m.group(2).strip(), "desc": m.group(3)}

for m in re.finditer(r'DEFINE_MAYBE_BOOL\(\s*(\w+)\s*,\s*"([^"]*)"', content, re.DOTALL):
    name = m.group(1)
    if name not in flags and name not in macro_params:
        flags[name] = {"type": "MAYBE_BOOL", "default": "unset", "desc": m.group(2)}

for m in re.finditer(r'DEFINE_EXPERIMENTAL_FEATURE\(\s*(\w+)\s*,\s*"([^"]*)"', content, re.DOTALL):
    name = m.group(1)
    if name not in flags and name not in macro_params:
        flags[name] = {"type": "EXPERIMENTAL", "default": "false", "desc": m.group(2)}

for m in re.finditer(r'DEFINE_ALIAS_BOOL\(\s*(\w+)\s*,\s*(\w+)\s*\)', content):
    name = m.group(1)
    if name not in flags and name not in macro_params:
        flags[name] = {"type": "ALIAS", "default": f"-> {m.group(2)}", "desc": ""}

# Filtrar
if filt:
    flags = {k: v for k, v in flags.items()
             if filt.lower() in k.lower() or filt.lower() in v["desc"].lower()}

# Output
print(f"# V8 Runtime Flags (from flag-definitions.h)")
print(f"# Total: {len(flags)} unique flags (static extraction)")
print(f"# NOTA: ~5-8% de flags podem faltar (macros FOREACH, #ifdef complexos)")
print(f"# Para lista completa: d8 --help (binario compilado)")
if filt:
    print(f"# Filter: {filt}")
print(f"# Format: --name  [TYPE]  default=VALUE  // description")
print()

# Categorizar (primeira match ganha)
categories = [
    ("SANDBOX", lambda k: "sandbox" in k or "memory_corruption" in k),
    ("WASM", lambda k: "wasm" in k and "trace" not in k and "print" not in k),
    ("MAGLEV", lambda k: "maglev" in k and "trace" not in k and "print" not in k),
    ("TURBOFAN/TURBOSHAFT", lambda k: "turbo" in k and "trace" not in k and "print" not in k),
    ("SPARKPLUG", lambda k: "sparkplug" in k),
    ("GC", lambda k: k.startswith("gc") or "scaveng" in k or "sweep" in k),
    ("TRACE", lambda k: "trace" in k),
    ("PRINT/DUMP", lambda k: "print" in k or "dump" in k),
]
categorized = set()
for cat, fn in categories:
    items = sorted(k for k in flags if fn(k) and k not in categorized)
    if items:
        print(f"=== {cat} ({len(items)}) ===")
        for name in items:
            f = flags[name]
            print(f"  --{name}  [{f['type']}]  default={f['default']}  // {f['desc']}")
            categorized.add(name)
        print()
other = sorted(k for k in flags if k not in categorized)
if other:
    print(f"=== OTHER ({len(other)}) ===")
    for name in other:
        f = flags[name]
        print(f"  --{name}  [{f['type']}]  default={f['default']}  // {f['desc']}")
print(f"\n# TOTAL: {len(flags)} unique flags")
PYEOF
