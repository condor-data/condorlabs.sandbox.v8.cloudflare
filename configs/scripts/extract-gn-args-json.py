#!/usr/bin/env python3
"""
extract-gn-args-json.py — Extrai TODOS os GN args do V8 source de forma DETERMINÍSTICA.

Parseia declare_args() blocks em BUILD.gn e gni/v8.gni.
Output: JSON com TODOS os args, defaults, e comentários.

Nada de inferência. Regex puro no source.

Uso:
  # Via stdin (pipe do conteúdo do BUILD.gn)
  cat BUILD.gn | python3 extract-gn-args-json.py --source BUILD.gn

  # Via arquivo
  python3 extract-gn-args-json.py --file BUILD.gn --file gni/v8.gni

  # Via GitHub API (base64 decode)
  python3 extract-gn-args-json.py --github condor-data/condorlabs.sandbox.v8.chrome BUILD.gn gni/v8.gni
"""
import re
import sys
import json


def extract_declare_args(content: str, source_file: str) -> list[dict]:
    """
    Extrai TODOS os args de declare_args() blocks.
    Cada arg tem: name, raw_default, comment, source_file, line_number
    """
    args = []

    # Encontrar todos os declare_args() { ... } blocks
    # GN syntax: declare_args() { ... }
    # Pode ter blocks aninhados, mas declare_args não aninha.
    pattern = re.compile(r'declare_args\(\)\s*\{', re.MULTILINE)

    for match in pattern.finditer(content):
        start = match.end()
        # Encontrar o } que fecha este block (contando { e })
        depth = 1
        pos = start
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            pos += 1

        block = content[start:pos-1]
        block_start_line = content[:match.start()].count('\n') + 1

        # Parsear cada assignment dentro do block
        # Padrão: [# comment\n]* var_name = value
        lines = block.split('\n')
        comments = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Coletar comentários
            if line.startswith('#'):
                comments.append(line.lstrip('# '))
                i += 1
                continue

            # Pular linhas vazias (resetam comments)
            if not line:
                if comments:
                    comments = []  # Reset comments se linha vazia entre comment e var
                i += 1
                continue

            # Tentar parsear assignment: var = value
            m = re.match(r'^(\w+)\s*=\s*(.+?)$', line)
            if m:
                name = m.group(1)
                raw_default = m.group(2).strip()

                # Se o valor continua na próxima linha (multi-line)
                # Detectar: valor não termina em algo que parece completo
                while raw_default.count('(') > raw_default.count(')') or \
                      raw_default.count('[') > raw_default.count(']') or \
                      raw_default.endswith('||') or \
                      raw_default.endswith('&&') or \
                      raw_default.endswith('+') or \
                      raw_default.endswith(','):
                    i += 1
                    if i >= len(lines):
                        break
                    raw_default += ' ' + lines[i].strip()

                # Classificar o tipo do default
                arg_type = classify_type(raw_default)

                args.append({
                    "name": name,
                    "raw_default": raw_default,
                    "type": arg_type,
                    "comment": ' '.join(comments) if comments else "",
                    "source_file": source_file,
                    "line": block_start_line + i,
                })
                comments = []
            else:
                # Linha que não é comment nem assignment (ex: if block)
                # Pode ser parte de conditional default — ignorar
                pass

            i += 1

    return args


def classify_type(raw_default: str) -> str:
    """Classifica o tipo GN do valor default."""
    v = raw_default.strip()

    if v in ('true', 'false'):
        return 'bool'

    if v.startswith('"') and v.endswith('"'):
        return 'string'

    if v == '""':
        return 'string'

    # Números
    try:
        int(v)
        return 'int'
    except ValueError:
        pass

    # Expressões que resolvem para bool (contém == ou != ou &&, ||)
    if '==' in v or '!=' in v or '&&' in v or '||' in v or v.startswith('!'):
        return 'bool_expr'

    # Referência a outra variável
    if re.match(r'^[a-z_][a-z_0-9]*$', v):
        return 'ref'

    return 'expr'


def main():
    import subprocess

    all_args = []

    if '--github' in sys.argv:
        # Modo GitHub API: baixar arquivos via gh
        idx = sys.argv.index('--github')
        repo = sys.argv[idx + 1]
        files = sys.argv[idx + 2:]

        for f in files:
            print(f"Fetching {repo}/{f}...", file=sys.stderr)
            result = subprocess.run(
                ['gh', 'api', f'repos/{repo}/contents/{f}', '--jq', '.content'],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"ERRO: Não conseguiu baixar {f}: {result.stderr}", file=sys.stderr)
                continue

            import base64
            content = base64.b64decode(result.stdout.strip()).decode('utf-8')
            args = extract_declare_args(content, f)
            all_args.extend(args)
            print(f"  → {len(args)} args extraídos de {f}", file=sys.stderr)

    elif '--file' in sys.argv:
        # Modo arquivo local
        files = [sys.argv[i+1] for i, a in enumerate(sys.argv) if a == '--file']
        for f in files:
            with open(f) as fh:
                content = fh.read()
            args = extract_declare_args(content, f)
            all_args.extend(args)
            print(f"  → {len(args)} args extraídos de {f}", file=sys.stderr)

    else:
        # Modo stdin
        source = sys.argv[sys.argv.index('--source') + 1] if '--source' in sys.argv else 'stdin'
        content = sys.stdin.read()
        all_args = extract_declare_args(content, source)

    # Dedup por nome (primeiro encontrado ganha)
    seen = set()
    unique_args = []
    for a in all_args:
        if a['name'] not in seen:
            seen.add(a['name'])
            unique_args.append(a)

    # Output
    output = {
        "extracted_at": __import__('datetime').datetime.utcnow().isoformat() + 'Z',
        "total_args": len(unique_args),
        "total_raw": len(all_args),
        "duplicates_removed": len(all_args) - len(unique_args),
        "sources": list(set(a['source_file'] for a in all_args)),
        "args": unique_args,
    }

    print(json.dumps(output, indent=2))
    print(f"\nTotal: {len(unique_args)} unique args ({len(all_args) - len(unique_args)} dupes removed)", file=sys.stderr)


if __name__ == '__main__':
    main()
