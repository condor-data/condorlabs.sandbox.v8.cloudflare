#!/usr/bin/env python3
"""
extract-runtime-flags-json.py — Parseia d8 --help output para JSON.
DETERMINÍSTICO. Parseia o formato exato do d8 --help.

Formato real:
  --flag-name (description que pode ter (parênteses) internos)
        type: bool  default: --flag-name ou --no-flag-name
  --flag-name (description)
        type: int  default: 123
  --flag-name (description)
        type: string  default: valor

Input: arquivo com output do d8 --help
Output: JSON com todas as flags
"""
import re
import sys
import json
from datetime import datetime, timezone


def parse_d8_help(content: str) -> list[dict]:
    """Parseia output do d8 --help."""
    flags = []
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]

        # Match: "  --flag-name (description...)"
        # A descrição pode conter parênteses! Então match até o último )
        m = re.match(r'^  --([\w-]+)\s+\((.*)\)\s*$', line)
        if m:
            name = m.group(1)
            description = m.group(2)

            # Próxima linha: "        type: TYPE  default: VALUE"
            flag_type = "unknown"
            default = ""
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                tm = re.match(r'^\s+type:\s+(\w+)\s+default:\s*(.*)', next_line)
                if tm:
                    flag_type = tm.group(1)
                    default = tm.group(2).strip()
                    i += 1

            # Filtrar meta-entries do help header (--flag, --no-flag)
            if name not in ('flag', 'no-flag'):
                flags.append({
                    "name": name,
                    "type": flag_type,
                    "default": default,
                    "description": description,
                })

        i += 1

    return flags


def main():
    if '--file' in sys.argv:
        idx = sys.argv.index('--file')
        filepath = sys.argv[idx + 1]
        with open(filepath) as f:
            content = f.read()
    else:
        content = sys.stdin.read()

    flags = parse_d8_help(content)

    # Validação: comparar com count de "type:" lines
    type_count = len(re.findall(r'^\s+type:\s+', content, re.MULTILINE))

    output = {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "total_flags": len(flags),
        "type_lines_in_source": type_count,
        "coverage_pct": round(100 * len(flags) / type_count, 1) if type_count > 0 else 0,
        "source": filepath if '--file' in sys.argv else "stdin",
        "flags": flags,
    }

    json.dump(output, sys.stdout, indent=2)
    print(f"\nTotal: {len(flags)} flags parsed, {type_count} type: lines in source = {output['coverage_pct']}% coverage", file=sys.stderr)

    if len(flags) < type_count:
        missing = type_count - len(flags)
        print(f"WARNING: {missing} flags não foram parseadas!", file=sys.stderr)


if __name__ == '__main__':
    main()
