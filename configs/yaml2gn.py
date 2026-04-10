#!/usr/bin/env python3
"""
yaml2gn.py v2 — Config-as-Code com correlation matrix + validacao deterministica

Consome dados DETERMINISTICOS extraidos do V8 source:
  1. reference/v8-gn-args-catalog.json    — 181 GN args de BUILD.gn + v8.gni
  2. reference/v8-runtime-flags-catalog.json — 984 runtime flags de d8 --help
  3. correlation-matrix.yaml              — regras de conflito/implicacao (manual)
  4. v8-chrome.yaml (ou v8-cf.yaml)       — configs de build (editavel)

Zero inferencia de IA. Tudo validado contra dados extraidos do source.

Uso:
  python3 yaml2gn.py configs/v8-chrome.yaml fast         # gerar args.gn
  python3 yaml2gn.py configs/v8-chrome.yaml fast --validate  # so validar
  python3 yaml2gn.py configs/v8-chrome.yaml --validate-all   # validar todos
  python3 yaml2gn.py --list-args                             # listar catalogo
  python3 yaml2gn.py --show-matrix                           # mostrar matrix
  python3 yaml2gn.py --check-runtime configs/v8-chrome.yaml fast  # validar runtime flags
"""
import sys
import json
import yaml
from pathlib import Path
from typing import Any


# =============================================================================
# PATHS — tudo relativo ao diretorio do script
# =============================================================================
SCRIPT_DIR = Path(__file__).parent
GN_CATALOG_PATH = SCRIPT_DIR / "reference" / "v8-gn-args-catalog.json"
RUNTIME_CATALOG_PATH = SCRIPT_DIR / "reference" / "v8-runtime-flags-catalog.json"
MATRIX_PATH = SCRIPT_DIR / "correlation-matrix.yaml"


# =============================================================================
# LOADERS
# =============================================================================
def load_gn_catalog() -> dict:
    """Carrega catalogo de GN args (extraido deterministicamente do V8 source)."""
    if not GN_CATALOG_PATH.exists():
        print(f"WARNING: GN catalog nao encontrado: {GN_CATALOG_PATH}", file=sys.stderr)
        print(f"  Rode: python3 scripts/extract-gn-args-json.py --file BUILD.gn --file gni/v8.gni", file=sys.stderr)
        return {"args": []}
    with open(GN_CATALOG_PATH) as f:
        return json.load(f)


def load_runtime_catalog() -> dict:
    """Carrega catalogo de runtime flags (extraido de d8 --help)."""
    if not RUNTIME_CATALOG_PATH.exists():
        print(f"WARNING: Runtime catalog nao encontrado: {RUNTIME_CATALOG_PATH}", file=sys.stderr)
        print(f"  Rode: d8 --help | python3 scripts/extract-runtime-flags-json.py", file=sys.stderr)
        return {"flags": []}
    with open(RUNTIME_CATALOG_PATH) as f:
        return json.load(f)


def load_matrix() -> dict:
    """Carrega correlation matrix."""
    if not MATRIX_PATH.exists():
        print(f"WARNING: Correlation matrix nao encontrada: {MATRIX_PATH}", file=sys.stderr)
        return {}
    with open(MATRIX_PATH) as f:
        return yaml.safe_load(f)


# =============================================================================
# CORRELATION VALIDATOR
# =============================================================================
class CorrelationValidator:
    """Valida args contra correlation matrix + catalogo JSON."""

    def __init__(self):
        self.matrix = load_matrix()
        self.gn_catalog = load_gn_catalog()
        self.runtime_catalog = load_runtime_catalog()

        # Index: nome → entry do catalogo GN
        self.gn_index: dict[str, dict] = {}
        for arg in self.gn_catalog.get("args", []):
            self.gn_index[arg["name"]] = arg

        # Index: nome → entry do catalogo runtime
        self.runtime_index: dict[str, dict] = {}
        for flag in self.runtime_catalog.get("flags", []):
            self.runtime_index[flag["name"]] = flag

        # Chromium build system args (do correlation-matrix.yaml)
        self.chromium_args = self.matrix.get("chromium_args", {})

    def validate_gn_args(self, config_name: str, family: str, gn_args: dict) -> list[str]:
        """Valida GN args. Retorna lista de erros (vazia = OK)."""
        errors = []

        # 1. Conflitos da matrix
        for conflict in self.matrix.get("conflicts", []):
            a, b = conflict["pair"]
            if gn_args.get(a) is True and gn_args.get(b) is True:
                msg = f"CONFLITO [{config_name}]: {a}=true + {b}=true -- {conflict['reason'].strip()}"
                if conflict.get("severity") == "fatal":
                    errors.append(msg)
                else:
                    print(f"WARNING: {msg}", file=sys.stderr)

        # 2. Implicacoes
        for impl in self.matrix.get("implications", []):
            condition = impl["if"]
            match = True
            for k, v in condition.items():
                if k == "family":
                    if family != v:
                        match = False
                        break
                elif gn_args.get(k) != v:
                    match = False
                    break

            if match:
                for k, v in impl["then"].items():
                    actual = gn_args.get(k)
                    if actual is not None and actual != v:
                        errors.append(
                            f"IMPLICACAO [{config_name}]: "
                            f"{', '.join(f'{k2}={v2}' for k2,v2 in condition.items())} -> "
                            f"exige {k}={v}, mas encontrou {k}={actual} -- {impl['reason'].strip()}"
                        )

        # 3. Requerimentos
        for req in self.matrix.get("requirements", []):
            flag = req["flag"]
            if gn_args.get(flag) is True:
                for dep in req.get("requires", []):
                    if gn_args.get(dep) is not True:
                        errors.append(
                            f"REQUERIMENTO [{config_name}]: {flag}=true requer "
                            f"{dep}=true -- {req['reason'].strip()}"
                        )
                for conflict_flag in req.get("conflicts_with", []):
                    if gn_args.get(conflict_flag) is True:
                        errors.append(
                            f"CONFLITO_REQ [{config_name}]: {flag}=true conflita com "
                            f"{conflict_flag}=true -- {req['reason'].strip()}"
                        )

        # 4. Family enforcement
        if family == "sanitizer":
            if gn_args.get("v8_enable_memory_corruption_api") is True:
                errors.append(
                    f"FAMILY [{config_name}]: family=sanitizer mas "
                    f"v8_enable_memory_corruption_api=true! Sanitizers sequestram sinais."
                )
            has_sanitizer = any(gn_args.get(s) is True for s in ["is_asan", "is_tsan", "is_ubsan"])
            if not has_sanitizer:
                errors.append(
                    f"FAMILY [{config_name}]: family=sanitizer mas nenhum sanitizer ativo! "
                    f"Precisa de is_asan, is_tsan, ou is_ubsan = true."
                )
        elif family == "sandbox":
            if gn_args.get("v8_enable_memory_corruption_api") is False:
                errors.append(
                    f"FAMILY [{config_name}]: family=sandbox mas "
                    f"v8_enable_memory_corruption_api=false! Sem a API, Sandbox.MemoryView nao funciona."
                )
            if gn_args.get("is_asan") is True:
                errors.append(
                    f"FAMILY [{config_name}]: family=sandbox mas is_asan=true! "
                    f"ASAN e incompativel com sandbox crash filter."
                )
        elif family == "workerd":
            # Workerd configs usam Bazel, nao GN. Pular validacao de GN args.
            pass
        elif family != "unknown":
            errors.append(f"FAMILY [{config_name}]: family desconhecida '{family}'. Use: sandbox | sanitizer | workerd")

        # 5. Tipo + existencia (contra catalogo JSON)
        for k, v in gn_args.items():
            spec = None

            # Procurar no catalogo V8
            if k in self.gn_index:
                entry = self.gn_index[k]
                spec = {"type": entry["type"], "description": entry.get("comment", "")}
            # Procurar nos Chromium args
            elif k in self.chromium_args:
                spec = self.chromium_args[k]
            else:
                print(f"INFO [{config_name}]: {k} nao encontrado em nenhum catalogo (V8 nem Chromium)", file=sys.stderr)

            if spec:
                expected_type = spec.get("type")
                if expected_type == "bool" and not isinstance(v, bool):
                    errors.append(f"TIPO [{config_name}]: {k} deve ser bool, recebeu {type(v).__name__}: {v}")
                elif expected_type == "int" and not isinstance(v, int):
                    # YAML pode parsear 0 como int, 1 como int, etc
                    if not (isinstance(v, bool)):  # bool eh subtype de int em Python
                        errors.append(f"TIPO [{config_name}]: {k} deve ser int, recebeu {type(v).__name__}: {v}")
                elif expected_type == "string" and not isinstance(v, str):
                    if not isinstance(v, (bool, int)):  # GN aceita coercao
                        errors.append(f"TIPO [{config_name}]: {k} deve ser string, recebeu {type(v).__name__}: {v}")

                # Validar range (se definido)
                if "range" in spec and isinstance(v, int) and not isinstance(v, bool):
                    if v not in spec["range"]:
                        errors.append(f"RANGE [{config_name}]: {k}={v} fora do range {spec['range']}")

                # Validar values (se definido)
                if "values" in spec and isinstance(v, str):
                    if v not in spec["values"]:
                        errors.append(f"VALOR [{config_name}]: {k}='{v}' nao esta em {spec['values']}")

        return errors

    def validate_runtime_flags(self, config_name: str, runtime_flags: list[str]) -> list[str]:
        """Valida runtime flags contra catalogo de d8 --help."""
        errors = []
        if not self.runtime_index:
            return errors  # Catalogo nao disponivel

        for flag in runtime_flags:
            # Parse: --flag-name ou --flag-name=value
            clean = flag.lstrip("-")
            if "=" in clean:
                name = clean.split("=")[0]
            else:
                name = clean

            if name not in self.runtime_index:
                # Tentar com prefixo "no-" removido
                if name.startswith("no-") and name[3:] in self.runtime_index:
                    continue  # --no-flag eh valido se --flag existe
                print(
                    f"WARNING [{config_name}]: runtime flag --{name} "
                    f"nao encontrada no catalogo de d8 --help ({len(self.runtime_index)} flags)",
                    file=sys.stderr
                )

        return errors

    def get_description(self, arg_name: str) -> str:
        """Retorna descricao do arg (do catalogo JSON ou Chromium)."""
        if arg_name in self.gn_index:
            return self.gn_index[arg_name].get("comment", "")
        if arg_name in self.chromium_args:
            return self.chromium_args[arg_name].get("description", "")
        return ""


# =============================================================================
# GN GENERATOR
# =============================================================================
def gn_value(v: Any) -> str:
    """Converte valor Python para formato GN."""
    if isinstance(v, bool):
        return "true" if v else "false"
    elif isinstance(v, int):
        return str(v)
    elif isinstance(v, str):
        return f'"{v}"'
    return str(v)


def generate_args_gn(yaml_path: str, config_name: str, validate_only: bool = False) -> str:
    """Gera conteudo de args.gn a partir do YAML + validacao contra catalogos."""

    validator = CorrelationValidator()

    # Carregar YAML do projeto
    with open(yaml_path) as f:
        project = yaml.safe_load(f)

    if config_name not in project.get("configs", {}):
        available = ", ".join(sorted(project.get("configs", {}).keys()))
        print(f"ERRO: Config '{config_name}' nao existe no YAML!", file=sys.stderr)
        print(f"Configs disponiveis: {available}", file=sys.stderr)
        sys.exit(1)

    config = project["configs"][config_name]
    family = config.get("family", "unknown")
    gn_args = config.get("gn_args", {})
    runtime_flags = config.get("runtime_flags", [])

    # Merge com base do projeto
    base = project.get("base", {})
    merged = {**base, **gn_args}

    # Validar GN args
    errors = validator.validate_gn_args(config_name, family, merged)

    # Validar runtime flags
    rt_errors = validator.validate_runtime_flags(config_name, runtime_flags)
    errors.extend(rt_errors)

    if errors:
        print(f"", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"VALIDACAO FALHOU para config '{config_name}':", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        for e in errors:
            print(f"  X {e}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        sys.exit(1)

    if validate_only:
        print(f"OK: Config '{config_name}' validado (family={family}, {len(merged)} gn_args, {len(runtime_flags)} runtime_flags)")
        return ""

    # Gerar args.gn com descricoes do catalogo
    lines = [
        f"# Auto-generated from {Path(yaml_path).name}, config: {config_name}",
        f"# Family: {family}",
        f"# Description: {config.get('description', 'N/A')}",
        f"# Validated against: v8-gn-args-catalog.json + correlation-matrix.yaml",
        f"# Generated by yaml2gn.py v2 -- NAO EDITAR MANUALMENTE",
        "",
    ]

    for k, v in merged.items():
        desc = validator.get_description(k)
        if desc:
            lines.append(f"# {desc}")
        lines.append(f"{k} = {gn_value(v)}")

    return "\n".join(lines)


# =============================================================================
# COMMANDS
# =============================================================================
def cmd_list_args():
    """Lista todos os args do catalogo JSON."""
    gn_catalog = load_gn_catalog()
    matrix = load_matrix()
    chromium_args = matrix.get("chromium_args", {})

    args = gn_catalog.get("args", [])

    print(f"V8 GN Args Catalog — {len(args)} V8 args + {len(chromium_args)} Chromium args")
    print(f"Source: {GN_CATALOG_PATH}")
    print()

    # V8 args agrupados por source_file
    by_source: dict[str, list] = {}
    for arg in args:
        src = arg.get("source_file", "unknown")
        by_source.setdefault(src, []).append(arg)

    for src, entries in sorted(by_source.items()):
        print(f"=== {src} ({len(entries)} args) ===")
        for entry in sorted(entries, key=lambda e: e["name"]):
            t = entry.get("type", "?")
            default = entry.get("raw_default", "?")
            comment = entry.get("comment", "")
            print(f"  {entry['name']:45s} {t:10s} default={default}")
            if comment:
                print(f"    # {comment}")
        print()

    if chromium_args:
        print(f"=== CHROMIUM BUILD SYSTEM ({len(chromium_args)} args) ===")
        for name, spec in sorted(chromium_args.items()):
            t = spec.get("type", "?")
            desc = spec.get("description", "")
            print(f"  {name:45s} {t:10s}")
            if desc:
                print(f"    # {desc}")
        print()


def cmd_show_matrix():
    """Mostra a correlation matrix."""
    matrix = load_matrix()

    print("=== CORRELATION MATRIX ===")
    print(f"Source: {MATRIX_PATH}")
    print()

    conflicts = matrix.get("conflicts", [])
    print(f"CONFLITOS ({len(conflicts)}):")
    for c in conflicts:
        a, b = c["pair"]
        sev = c.get("severity", "warning")
        print(f"  [{sev:7s}] {a} X {b}")
        print(f"           {c['reason'].strip()}")
    print()

    implications = matrix.get("implications", [])
    print(f"IMPLICACOES ({len(implications)}):")
    for impl in implications:
        cond = " + ".join(f"{k}={v}" for k, v in impl["if"].items())
        then = " + ".join(f"{k}={v}" for k, v in impl["then"].items())
        print(f"  {cond} -> {then}")
        print(f"    {impl['reason'].strip()}")
    print()

    requirements = matrix.get("requirements", [])
    print(f"REQUERIMENTOS ({len(requirements)}):")
    for req in requirements:
        deps = ", ".join(req.get("requires", []))
        conflicts = ", ".join(req.get("conflicts_with", []))
        print(f"  {req['flag']} -> requires [{deps}]")
        if conflicts:
            print(f"  {req['flag']} -> conflicts_with [{conflicts}]")
        print(f"    {req['reason'].strip()}")


def cmd_validate_all(yaml_path: str):
    """Valida TODOS os configs de um YAML."""
    validator = CorrelationValidator()

    with open(yaml_path) as f:
        project = yaml.safe_load(f)

    configs = project.get("configs", {})
    base = project.get("base", {})

    passed = 0
    failed = 0
    total_errors = []

    print(f"Validando {len(configs)} configs de {yaml_path}...")
    print(f"  GN catalog: {len(validator.gn_index)} V8 args")
    print(f"  Runtime catalog: {len(validator.runtime_index)} flags")
    print(f"  Chromium args: {len(validator.chromium_args)} args")
    print()

    for name, config in sorted(configs.items()):
        family = config.get("family", "unknown")
        gn_args = config.get("gn_args", {})
        runtime_flags = config.get("runtime_flags", [])
        merged = {**base, **gn_args}

        errors = validator.validate_gn_args(name, family, merged)
        errors.extend(validator.validate_runtime_flags(name, runtime_flags))

        if errors:
            print(f"  X {name:20s} ({family}) -- {len(errors)} erros:")
            for e in errors:
                print(f"      {e}")
            failed += 1
            total_errors.extend(errors)
        else:
            flag_count = len(merged)
            rt_count = len(runtime_flags)
            print(f"  OK {name:20s} ({family}) -- {flag_count} gn_args, {rt_count} runtime_flags")
            passed += 1

    print()
    print(f"Resultado: {passed} passed, {failed} failed de {len(configs)} configs")
    if failed > 0:
        sys.exit(1)


def cmd_check_runtime(yaml_path: str, config_name: str):
    """Valida runtime flags de um config contra o catalogo."""
    validator = CorrelationValidator()

    with open(yaml_path) as f:
        project = yaml.safe_load(f)

    if config_name not in project.get("configs", {}):
        available = ", ".join(sorted(project.get("configs", {}).keys()))
        print(f"ERRO: Config '{config_name}' nao existe!", file=sys.stderr)
        print(f"Disponíveis: {available}", file=sys.stderr)
        sys.exit(1)

    config = project["configs"][config_name]
    runtime_flags = config.get("runtime_flags", [])

    print(f"Runtime flags para '{config_name}': {len(runtime_flags)}")
    print()

    for flag in runtime_flags:
        clean = flag.lstrip("-")
        if "=" in clean:
            name, value = clean.split("=", 1)
        else:
            name, value = clean, None

        if name in validator.runtime_index:
            entry = validator.runtime_index[name]
            status = "OK"
            ftype = entry.get("type", "?")
            desc = entry.get("description", "")
            default = entry.get("default", "")
            print(f"  [{status}] {flag}")
            print(f"       type={ftype}  default={default}")
            if desc:
                print(f"       {desc[:100]}")
        elif name.startswith("no-") and name[3:] in validator.runtime_index:
            entry = validator.runtime_index[name[3:]]
            print(f"  [OK] {flag} (negation of --{name[3:]})")
            print(f"       type={entry.get('type', '?')}  default={entry.get('default', '?')}")
        else:
            print(f"  [??] {flag} -- NAO ENCONTRADO no catalogo")

    print()
    print(f"Catalogo: {len(validator.runtime_index)} flags de {RUNTIME_CATALOG_PATH.name}")


# =============================================================================
# MAIN
# =============================================================================
def main():
    if len(sys.argv) < 2:
        print("yaml2gn.py v2 -- Config-as-Code com validacao deterministica", file=sys.stderr)
        print("", file=sys.stderr)
        print("Uso:", file=sys.stderr)
        print("  python3 yaml2gn.py <yaml> <config>              Gerar args.gn", file=sys.stderr)
        print("  python3 yaml2gn.py <yaml> <config> --validate   So validar", file=sys.stderr)
        print("  python3 yaml2gn.py <yaml> --validate-all        Validar todos", file=sys.stderr)
        print("  python3 yaml2gn.py --list-args                  Listar catalogo", file=sys.stderr)
        print("  python3 yaml2gn.py --show-matrix                Mostrar matrix", file=sys.stderr)
        print("  python3 yaml2gn.py --check-runtime <yaml> <cfg> Validar runtime flags", file=sys.stderr)
        sys.exit(1)

    # Comandos sem YAML
    if sys.argv[1] == "--list-args":
        cmd_list_args()
        return
    elif sys.argv[1] == "--show-matrix":
        cmd_show_matrix()
        return
    elif sys.argv[1] == "--check-runtime":
        if len(sys.argv) < 4:
            print("ERRO: --check-runtime precisa de <yaml> <config>", file=sys.stderr)
            sys.exit(1)
        cmd_check_runtime(sys.argv[2], sys.argv[3])
        return

    yaml_path = sys.argv[1]
    if not Path(yaml_path).exists():
        print(f"ERRO: YAML nao encontrado: {yaml_path}", file=sys.stderr)
        sys.exit(1)

    # --validate-all
    if "--validate-all" in sys.argv:
        cmd_validate_all(yaml_path)
        return

    if len(sys.argv) < 3 or sys.argv[2].startswith("--"):
        print("ERRO: Falta o nome do config", file=sys.stderr)
        sys.exit(1)

    config_name = sys.argv[2]
    validate_only = "--validate" in sys.argv

    result = generate_args_gn(yaml_path, config_name, validate_only)
    if result:
        print(result)


if __name__ == "__main__":
    main()
