#!/usr/bin/env python3
"""
verify-build.py — Validacao pre e pos-build baseada no YAML config

Tudo vem do YAML. Nada hardcoded. O YAML eh a UNICA fonte de verdade.

Uso:
  # PRE-BUILD: valida YAML + mostra dry-run do args.gn
  python3 verify-build.py pre configs/v8-chrome.yaml fast

  # POST-BUILD: verifica se o d8 compilado corresponde ao YAML
  python3 verify-build.py post configs/v8-chrome.yaml fast /path/to/d8

  # VERIFY-ARGS: le args.gn gerado e compara com YAML
  python3 verify-build.py verify-args configs/v8-chrome.yaml fast /path/to/args.gn

  # UPDATE-STATUS: grava resultado no YAML
  python3 verify-build.py update-status configs/v8-chrome.yaml fast OK "14.7.173.18" "47MB"
"""
import sys
import yaml
import subprocess
import os
import json
from datetime import datetime, timezone
from pathlib import Path


def load_config(yaml_path: str, config_name: str) -> tuple:
    """Carrega YAML e retorna (full_cfg, base, config, merged_gn_args)."""
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)
    if config_name not in cfg.get("configs", {}):
        available = ", ".join(cfg.get("configs", {}).keys())
        print(f"ERRO: Config '{config_name}' nao existe! Disponiveis: {available}", file=sys.stderr)
        sys.exit(1)
    config = cfg["configs"][config_name]
    base = cfg.get("base", {})
    merged = {**base, **config.get("gn_args", {})}
    return cfg, base, config, merged


def gn_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    elif isinstance(v, int):
        return str(v)
    elif isinstance(v, str):
        return f'"{v}"'
    return str(v)


# =============================================================================
# PRE-BUILD: Validacao antes de compilar
# =============================================================================
def cmd_pre(yaml_path: str, config_name: str):
    """Valida YAML e mostra exatamente o que sera gerado."""
    cfg, base, config, merged = load_config(yaml_path, config_name)
    family = config.get("family", "unknown")
    errors = []
    warnings = []

    print(f"=== PRE-BUILD VALIDATION: {config_name} ===")
    print(f"  YAML: {yaml_path}")
    print(f"  Family: {family}")
    print(f"  Description: {config.get('description', 'N/A')}")
    print()

    # 1. Validacao de integridade
    print("[1] Integrity check...")
    if family == "sanitizer":
        if merged.get("v8_enable_memory_corruption_api") is True:
            errors.append("SANITIZER com memory_corruption_api=true! PROIBIDO.")
        has_san = merged.get("is_asan") or merged.get("is_tsan") or merged.get("is_ubsan")
        if not has_san:
            errors.append("SANITIZER sem nenhum sanitizer ativo!")
    elif family == "sandbox":
        if merged.get("v8_enable_memory_corruption_api") is False:
            errors.append("SANDBOX com memory_corruption_api=false! API nao sera exposta.")
        if merged.get("is_asan") is True:
            errors.append("SANDBOX com is_asan=true! ASAN mata o crash filter.")
        if merged.get("is_tsan") is True:
            errors.append("SANDBOX com is_tsan=true! Use family: sanitizer.")
    else:
        errors.append(f"Family desconhecida: '{family}'")

    if errors:
        for e in errors:
            print(f"  ERRO: {e}")
        sys.exit(1)
    print("  OK")

    # 2. Flags requeridas
    print("[2] Required flags...")
    required = ["v8_enable_sandbox", "v8_enable_memory_corruption_api", "target_cpu"]
    for flag in required:
        if flag not in merged:
            errors.append(f"Flag requerida ausente: {flag}")
        else:
            print(f"  {flag} = {gn_value(merged[flag])}")

    if family == "sandbox" and "is_asan" not in merged:
        warnings.append("is_asan nao definido explicitamente (recomendado: false)")

    if errors:
        for e in errors:
            print(f"  ERRO: {e}")
        sys.exit(1)
    for w in warnings:
        print(f"  WARN: {w}")
    print("  OK")

    # 3. Dry-run: args.gn que sera gerado
    print("[3] args.gn que sera gerado:")
    for k, v in merged.items():
        print(f"  {k} = {gn_value(v)}")
    print(f"  Total: {len(merged)} flags")

    # 4. Runtime flags
    print("[4] Runtime flags para d8:")
    runtime = config.get("runtime_flags", [])
    if runtime:
        for f in runtime:
            print(f"  {f}")
    else:
        warnings.append("Nenhum runtime_flag definido no YAML")
    for w in warnings:
        print(f"  WARN: {w}")

    print()
    print(f"PRE-BUILD: PASSED ({config_name})")


# =============================================================================
# VERIFY-ARGS: Verifica se args.gn gerado bate com YAML
# =============================================================================
def cmd_verify_args(yaml_path: str, config_name: str, args_gn_path: str):
    """Le args.gn gerado e compara com YAML flag por flag."""
    _, _, config, merged = load_config(yaml_path, config_name)
    errors = []

    print(f"=== VERIFY-ARGS: {config_name} ===")
    print(f"  args.gn: {args_gn_path}")

    if not os.path.exists(args_gn_path):
        print(f"ERRO: args.gn NAO EXISTE: {args_gn_path}")
        sys.exit(1)

    # Parse args.gn
    with open(args_gn_path) as f:
        lines = f.readlines()

    gn_flags = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"')
            # Converter para tipo Python
            if val == "true":
                val = True
            elif val == "false":
                val = False
            elif val.isdigit():
                val = int(val)
            gn_flags[key] = val

    print(f"  Flags no args.gn: {len(gn_flags)}")
    print(f"  Flags no YAML: {len(merged)}")

    # Verificar cada flag do YAML
    for key, expected in merged.items():
        actual = gn_flags.get(key)
        if actual is None:
            errors.append(f"  FALTANDO no args.gn: {key} (YAML diz: {gn_value(expected)})")
        elif actual != expected:
            errors.append(f"  DIVERGENCIA: {key} = {actual} (args.gn) vs {gn_value(expected)} (YAML)")
        else:
            print(f"  OK: {key} = {gn_value(expected)}")

    # Flags no args.gn que nao estao no YAML
    for key in gn_flags:
        if key not in merged:
            print(f"  EXTRA (no args.gn mas nao no YAML): {key} = {gn_flags[key]}")

    if errors:
        print()
        for e in errors:
            print(e)
        print()
        print(f"VERIFY-ARGS: FAILED — {len(errors)} divergencias!")
        sys.exit(1)

    print()
    print(f"VERIFY-ARGS: PASSED — todas as {len(merged)} flags batem")


# =============================================================================
# POST-BUILD: Verifica d8 compilado contra YAML
# =============================================================================
def cmd_post(yaml_path: str, config_name: str, d8_path: str):
    """Verifica se d8 compilado corresponde ao que o YAML pediu."""
    _, _, config, merged = load_config(yaml_path, config_name)
    family = config.get("family", "unknown")
    runtime = config.get("runtime_flags", [])
    errors = []
    total = 0
    passed = 0

    print(f"=== POST-BUILD VALIDATION: {config_name} ===")
    print(f"  d8: {d8_path}")
    print(f"  Family: {family}")

    if not os.path.exists(d8_path):
        print(f"ERRO: d8 NAO EXISTE: {d8_path}")
        sys.exit(1)

    # 1. d8 executa
    total += 1
    print(f"[1/{7 if family == 'sandbox' else 5}] d8 executa...", end=" ", flush=True)
    r = subprocess.run([d8_path, "-e", "print('OK')"], capture_output=True, text=True, timeout=10)
    if r.returncode == 0 and "OK" in r.stdout:
        print("PASSED")
        passed += 1
    else:
        print(f"FAILED (exit={r.returncode}, out={r.stdout[:100]})")
        errors.append("d8 nao executa")

    # 2. Version
    total += 1
    print(f"[2] d8 version...", end=" ", flush=True)
    r = subprocess.run([d8_path, "-e", "print(version())"], capture_output=True, text=True, timeout=10)
    if r.returncode == 0 and r.stdout.strip():
        print(f"PASSED (V8 {r.stdout.strip()})")
        passed += 1
    else:
        print(f"FAILED")
        errors.append("version() falhou")

    # 3. Runtime flags do YAML funcionam
    total += 1
    test_flags = [f for f in runtime if f.startswith("--")]
    # Filtrar flags que são para uso, não para teste (ex: --trace-turbo pode não ter efeito visível)
    testable_flags = [f for f in test_flags if f in ("--sandbox-testing", "--sandbox-fuzzing",
                                                       "--allow-natives-syntax", "--expose-gc")]
    print(f"[3] Runtime flags do YAML ({len(testable_flags)} testaveis)...", end=" ", flush=True)
    if testable_flags:
        cmd = [d8_path] + testable_flags + ["-e", "print('FLAGS_OK')"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and "FLAGS_OK" in r.stdout:
            print(f"PASSED ({' '.join(testable_flags)})")
            passed += 1
        else:
            print(f"FAILED (flags: {testable_flags}, exit={r.returncode})")
            errors.append(f"Runtime flags falharam: {testable_flags}")
    else:
        print("SKIPPED (nenhuma flag testavel no YAML)")
        passed += 1

    # 4. Component vs static build
    total += 1
    is_component = merged.get("is_component_build", False)
    print(f"[4] Binary type (YAML: component={is_component})...", end=" ", flush=True)
    r = subprocess.run(["file", d8_path], capture_output=True, text=True)
    is_dynamic = "dynamically linked" in r.stdout
    if is_component and is_dynamic:
        print("PASSED (dynamically linked = component build)")
        passed += 1
    elif not is_component and not is_dynamic:
        print("PASSED (statically linked = static build)")
        passed += 1
    elif is_component and not is_dynamic:
        print(f"WARN (YAML diz component=true mas binario eh static)")
        passed += 1  # warn, nao falha
    else:
        print(f"WARN (YAML diz static mas binario eh dynamic)")
        passed += 1

    # 5. Sandbox API (depende da family)
    total += 1
    if family == "sandbox":
        print(f"[5] Sandbox API exposta (family=sandbox)...", end=" ", flush=True)
        r = subprocess.run([d8_path, "--sandbox-testing", "-e", "print(typeof Sandbox)"],
                          capture_output=True, text=True, timeout=10)
        if "object" in r.stdout:
            print("PASSED")
            passed += 1
        else:
            print(f"FAILED (typeof Sandbox = '{r.stdout.strip()}')")
            errors.append("Sandbox API nao exposta em build sandbox!")
    else:
        print(f"[5] Sandbox API AUSENTE (family=sanitizer)...", end=" ", flush=True)
        r = subprocess.run([d8_path, "--sandbox-testing", "-e", "print(typeof Sandbox)"],
                          capture_output=True, text=True, timeout=10)
        if "object" not in r.stdout:
            print("PASSED (ausente, correto)")
            passed += 1
        else:
            print("FAILED (API exposta em sanitizer build!)")
            errors.append("Sandbox API exposta em sanitizer! memory_corruption_api deveria ser false")

    # 6-7: Testes sandbox-specific
    if family == "sandbox":
        # 6. MemoryView
        total += 1
        print(f"[6] Sandbox.MemoryView...", end=" ", flush=True)
        r = subprocess.run([d8_path, "--sandbox-testing", "-e",
                           "new DataView(new Sandbox.MemoryView(0,0x100)); print('MV_OK')"],
                          capture_output=True, text=True, timeout=10)
        if "MV_OK" in r.stdout:
            print("PASSED")
            passed += 1
        else:
            print(f"FAILED")
            errors.append("MemoryView falhou")

        # 7. getAddressOf + getSizeOf
        total += 1
        print(f"[7] addrof + getSizeOf...", end=" ", flush=True)
        r = subprocess.run([d8_path, "--sandbox-testing", "-e",
                           "let a=Sandbox.getAddressOf({}); let s=Sandbox.getSizeOf({}); print('AS_OK:'+typeof a+':'+s)"],
                          capture_output=True, text=True, timeout=10)
        if "AS_OK:bigint:" in r.stdout:
            print("PASSED")
            passed += 1
        else:
            print(f"FAILED ({r.stdout.strip()})")
            errors.append("addrof/getSizeOf falhou")

    print()
    if errors:
        for e in errors:
            print(f"  ERRO: {e}")
        print(f"\nPOST-BUILD: FAILED — {passed}/{total} passed, {len(errors)} errors")
        sys.exit(1)
    else:
        print(f"POST-BUILD: PASSED — {passed}/{total} tests")


# =============================================================================
# UPDATE-STATUS: Grava resultado no YAML
# =============================================================================
def cmd_update_status(yaml_path: str, config_name: str, status: str,
                      version: str = "", size: str = ""):
    """Grava last_build_* no YAML config."""
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)

    if config_name not in cfg.get("configs", {}):
        print(f"ERRO: Config '{config_name}' nao existe!", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg["configs"][config_name]["last_build_status"] = status
    cfg["configs"][config_name]["last_build_version"] = version or None
    cfg["configs"][config_name]["last_build_date"] = now
    cfg["configs"][config_name]["d8_binary_size"] = size or None
    cfg["last_build"] = now

    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"Status updated: {config_name} = {status} ({now})")


# =============================================================================
# MAIN
# =============================================================================
def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    yaml_path = sys.argv[2]
    config_name = sys.argv[3]

    if cmd == "pre":
        cmd_pre(yaml_path, config_name)
    elif cmd == "verify-args":
        if len(sys.argv) < 5:
            print("Falta: path do args.gn", file=sys.stderr)
            sys.exit(1)
        cmd_verify_args(yaml_path, config_name, sys.argv[4])
    elif cmd == "post":
        if len(sys.argv) < 5:
            print("Falta: path do d8", file=sys.stderr)
            sys.exit(1)
        cmd_post(yaml_path, config_name, sys.argv[4])
    elif cmd == "update-status":
        status = sys.argv[4] if len(sys.argv) > 4 else "UNKNOWN"
        version = sys.argv[5] if len(sys.argv) > 5 else ""
        size = sys.argv[6] if len(sys.argv) > 6 else ""
        cmd_update_status(yaml_path, config_name, status, version, size)
    else:
        print(f"Comando desconhecido: {cmd}", file=sys.stderr)
        print("Use: pre | post | verify-args | update-status", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
