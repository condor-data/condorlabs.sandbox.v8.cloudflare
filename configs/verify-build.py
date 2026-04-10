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

    # 2. Flag dependencies and conflicts (flags que desabilitam/obrigam outras)
    print("[2] Flag dependencies and conflicts...")
    # Mapa de conflitos conhecidos do V8 BUILD.gn e flag-definitions.h:
    # (flag_a, val_a, flag_b, val_b, severidade, mensagem)
    CONFLICTS = [
        ("is_asan", True, "v8_enable_memory_corruption_api", True, "FATAL",
         "V8 BUILD.gn tem assert bloqueando: is_asan + memory_corruption_api"),
        ("is_tsan", True, "v8_enable_memory_corruption_api", True, "FATAL",
         "TSAN sequestra sinais, incompativel com crash filter"),
        ("is_ubsan", True, "v8_enable_memory_corruption_api", True, "FATAL",
         "UBSan incompativel com memory corruption API"),
        ("is_asan", True, "is_tsan", True, "FATAL",
         "ASAN e TSAN nao podem coexistir (ambos interceptam alocacoes)"),
        ("is_asan", True, "is_component_build", True, "WARN",
         "ASAN com component build pode ter falsos positivos em shared libs"),
        ("v8_enable_verify_heap", True, "is_debug", False, "WARN",
         "verify_heap em release build: GC fica 10-50x mais lento"),
        ("v8_no_inline", True, "is_debug", False, "WARN",
         "v8_no_inline em release build: performance -30%, binario nao representa producao"),
    ]
    # Mapa de dependencias (flag_a=val_a OBRIGA flag_b=val_b)
    DEPENDENCIES = [
        ("v8_enable_memory_corruption_api", True, "v8_enable_sandbox", True,
         "memory_corruption_api requer sandbox habilitado"),
        ("is_tsan", True, "is_asan", False,
         "TSAN obriga is_asan=false"),
    ]

    for fa, va, fb, vb, sev, msg in CONFLICTS:
        if merged.get(fa) == va and merged.get(fb) == vb:
            if sev == "FATAL":
                errors.append(f"CONFLITO FATAL: {fa}={va} + {fb}={vb} — {msg}")
            else:
                warnings.append(f"CONFLITO {sev}: {fa}={va} + {fb}={vb} — {msg}")

    for fa, va, fb, vb, msg in DEPENDENCIES:
        if merged.get(fa) == va and merged.get(fb) != vb:
            actual_fb = merged.get(fb, "NOT_SET")
            errors.append(f"DEPENDENCIA: {fa}={va} obriga {fb}={vb} mas esta {fb}={actual_fb} — {msg}")

    if errors:
        for e in errors:
            print(f"  ERRO: {e}")
        sys.exit(1)
    for w in warnings:
        print(f"  WARN: {w}")
    if not errors and not warnings:
        print("  OK (nenhum conflito ou dependencia violada)")

    # 3. Flags requeridas
    print("[3] Required flags...")
    errors = []  # reset errors after conflicts check
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
# INTROSPECT: Descobre flags REAIS do binario compilado
# =============================================================================
def cmd_introspect(yaml_path: str, config_name: str, d8_path: str, build_dir: str):
    """Extrai flags reais do binario e compara com YAML.

    Usa 4 fontes de verdade:
    1. gn args --list --overrides-only  (flags GN custom)
    2. gn desc //v8:d8 defines          (preprocessor defines reais)
    3. nm d8 | grep __*san_init         (sanitizers no binario)
    4. runtime-probe.js no d8           (flags ativas em runtime)

    Output: JSON report salvo em configs/reference/
    """
    _, _, config, merged = load_config(yaml_path, config_name)
    family = config.get("family", "unknown")
    errors = []
    report = {"config": config_name, "family": family, "sources": {}}

    print(f"=== INTROSPECT: {config_name} ===")
    print(f"  d8: {d8_path}")
    print(f"  build_dir: {build_dir}")
    print()

    # --- SOURCE 1: gn args --overrides-only ---
    print("[1/4] gn args --overrides-only...")
    r = subprocess.run(["gn", "args", "--list", "--overrides-only", "--short", build_dir],
                       capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        gn_overrides = {}
        for line in r.stdout.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                gn_overrides[k.strip()] = v.strip().strip('"')
        report["sources"]["gn_overrides"] = gn_overrides
        print(f"  {len(gn_overrides)} overrides detectados")
        for k, v in gn_overrides.items():
            yaml_val = gn_value(merged.get(k, "NOT_IN_YAML"))
            match = "OK" if str(v) == yaml_val.strip('"') else "DIVERGE"
            print(f"  {match}: {k} = {v} (YAML: {yaml_val})")
            if match == "DIVERGE":
                errors.append(f"gn override {k}={v} diverge do YAML ({yaml_val})")
    else:
        print(f"  WARN: gn args falhou (gn nao no PATH?)")
        report["sources"]["gn_overrides"] = {"error": r.stderr[:200]}

    # --- SOURCE 2: gn desc defines ---
    print()
    print("[2/4] gn desc //v8:d8 defines...")
    r = subprocess.run(["gn", "desc", build_dir, "//v8:d8", "defines"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        defines = [d.strip() for d in r.stdout.strip().split("\n") if d.strip()]
        report["sources"]["gn_defines"] = defines

        # Verificar defines esperados baseado no YAML
        checks = {
            "V8_ENABLE_SANDBOX": merged.get("v8_enable_sandbox", True),
            "DCHECK_ALWAYS_ON": merged.get("dcheck_always_on", False),
        }
        if merged.get("v8_enable_memory_corruption_api"):
            checks["V8_ENABLE_MEMORY_CORRUPTION_API"] = True

        for define, expected in checks.items():
            present = any(define in d for d in defines)
            if expected and not present:
                print(f"  FALTANDO: {define} (YAML espera ativo)")
                errors.append(f"Define {define} ausente mas YAML espera ativo")
            elif not expected and present:
                print(f"  INESPERADO: {define} presente (YAML nao pede)")
                errors.append(f"Define {define} presente mas YAML nao pede")
            elif expected and present:
                print(f"  OK: {define} presente (como YAML pede)")
            else:
                print(f"  OK: {define} ausente (como YAML pede)")
    else:
        print(f"  WARN: gn desc falhou")
        report["sources"]["gn_defines"] = {"error": r.stderr[:200]}

    # --- SOURCE 3: nm (sanitizer symbols) ---
    print()
    print("[3/4] Binary sanitizer symbols (nm)...")
    sanitizers_found = {}
    for san_name, san_sym in [("asan", "__asan_init"), ("tsan", "__tsan_init"), ("ubsan", "__ubsan_handle")]:
        r = subprocess.run(["nm", d8_path], capture_output=True, text=True, timeout=30)
        count = r.stdout.count(san_sym) if r.returncode == 0 else 0
        sanitizers_found[san_name] = count > 0

        expected = merged.get(f"is_{san_name}", False)
        if expected and count == 0:
            print(f"  ERRO: {san_name} esperado (YAML) mas NAO encontrado no binario!")
            errors.append(f"{san_name} ausente no binario mas YAML pede is_{san_name}=true")
        elif not expected and count > 0:
            print(f"  ERRO: {san_name} encontrado no binario mas YAML nao pede!")
            errors.append(f"{san_name} presente no binario mas YAML diz is_{san_name}=false")
        elif expected:
            print(f"  OK: {san_name} presente (como YAML pede)")
        else:
            print(f"  OK: {san_name} ausente (como YAML pede)")

    report["sources"]["binary_sanitizers"] = sanitizers_found

    # --- SOURCE 4: runtime-probe.js ---
    print()
    print("[4/4] Runtime probe (d8 + runtime-probe.js)...")
    probe_js = os.path.join(os.path.dirname(yaml_path), "runtime-probe.js")
    if not os.path.exists(probe_js):
        print(f"  WARN: {probe_js} nao encontrado, pulando runtime probe")
        report["sources"]["runtime_probe"] = {"error": "file not found"}
    else:
        runtime_flags = ["--allow-natives-syntax"]
        if family == "sandbox":
            runtime_flags.append("--sandbox-testing")
        if config.get("runtime_flags"):
            for f in config["runtime_flags"]:
                if f not in runtime_flags and f.startswith("--"):
                    # Evitar flags que alteram comportamento (trace, print)
                    if "trace" not in f and "print" not in f:
                        runtime_flags.append(f)

        r = subprocess.run([d8_path] + runtime_flags + [probe_js],
                          capture_output=True, text=True, timeout=15)

        if r.returncode == 0 and "RUNTIME_PROBE_JSON_START" in r.stdout:
            # Extrair JSON do output
            lines = r.stdout.split("\n")
            json_start = lines.index("RUNTIME_PROBE_JSON_START") + 1
            json_end = lines.index("RUNTIME_PROBE_JSON_END")
            probe_json = json.loads("\n".join(lines[json_start:json_end]))
            report["sources"]["runtime_probe"] = probe_json

            # Verificar Sandbox API
            if family == "sandbox" and not probe_json.get("build", {}).get("sandbox_api"):
                print(f"  ERRO: Sandbox API nao detectada em runtime (family=sandbox)!")
                errors.append("Sandbox API ausente em runtime")
            elif family == "sanitizer" and probe_json.get("build", {}).get("sandbox_api"):
                print(f"  ERRO: Sandbox API detectada em runtime (family=sanitizer)!")
                errors.append("Sandbox API presente em sanitizer build")
            else:
                print(f"  OK: Sandbox API = {probe_json.get('build', {}).get('sandbox_api')}")

            # verify_heap
            yaml_vh = merged.get("v8_enable_verify_heap", False)
            probe_vh = probe_json.get("build", {}).get("verify_heap", False)
            if yaml_vh and not probe_vh:
                print(f"  ERRO: verify_heap esperado mas NAO ativo em runtime!")
                errors.append("verify_heap ausente em runtime")
            elif yaml_vh and probe_vh:
                print(f"  OK: verify_heap ativo (como YAML pede)")
            else:
                print(f"  OK: verify_heap = {probe_vh}")

            # Compilers
            for comp in ["turbofan", "sparkplug", "maglev"]:
                val = probe_json.get("compilers", {}).get(comp)
                if val is not None:
                    print(f"  INFO: {comp} = {val}")

            # Sandbox API methods
            methods = probe_json.get("sandbox_api_methods", [])
            if methods:
                print(f"  Sandbox API: {len(methods)} methods")
                for m in methods:
                    print(f"    {m['name']}: {m['type']}")
        else:
            print(f"  WARN: runtime probe falhou (exit={r.returncode})")
            if r.stderr:
                print(f"  stderr: {r.stderr[:200]}")
            report["sources"]["runtime_probe"] = {"error": f"exit {r.returncode}", "stderr": r.stderr[:500]}

    # --- RESULTADO ---
    print()
    if errors:
        for e in errors:
            print(f"  ERRO: {e}")
        print(f"\nINTROSPECT: FAILED — {len(errors)} divergencias entre binario e YAML!")
        # Salvar report mesmo com erros
        _save_introspect_report(yaml_path, config_name, report)
        sys.exit(1)
    else:
        print(f"INTROSPECT: PASSED — binario corresponde ao YAML")
        _save_introspect_report(yaml_path, config_name, report)


def _save_introspect_report(yaml_path: str, config_name: str, report: dict):
    """Salva report em JSON E grava flags descobertas de volta no YAML.

    Isso fecha o ciclo: YAML define flags → build compila → introspect descobre
    flags REAIS → YAML recebe as flags VIVAS do binário.
    """
    # 1. Salvar JSON report em configs/reference/
    ref_dir = os.path.join(os.path.dirname(yaml_path), "reference")
    os.makedirs(ref_dir, exist_ok=True)
    report_path = os.path.join(ref_dir, f"introspect-{config_name}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  Report JSON salvo: {report_path}")

    # 2. Gravar flags descobertas de volta no YAML config
    try:
        with open(yaml_path) as f:
            cfg = yaml.safe_load(f)

        if config_name not in cfg.get("configs", {}):
            print(f"  WARN: config '{config_name}' nao existe no YAML, pulando writeback")
            return

        config = cfg["configs"][config_name]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Seção runtime_discovered: flags que o binário REALMENTE tem
        discovered = {"introspect_date": now}

        # GN overrides reais (do gn args --overrides-only)
        gn_overrides = report.get("sources", {}).get("gn_overrides", {})
        if isinstance(gn_overrides, dict) and "error" not in gn_overrides:
            discovered["gn_overrides_active"] = gn_overrides

        # Sanitizers no binário (do nm)
        binary_san = report.get("sources", {}).get("binary_sanitizers", {})
        if isinstance(binary_san, dict):
            discovered["sanitizers_in_binary"] = binary_san

        # Preprocessor defines (do gn desc)
        gn_defines = report.get("sources", {}).get("gn_defines", [])
        if isinstance(gn_defines, list):
            # Filtrar só os defines relevantes para sandbox/security
            relevant = [d for d in gn_defines if any(k in d for k in [
                "V8_ENABLE_SANDBOX", "DCHECK", "V8_ENABLE_MEMORY_CORRUPTION",
                "ASAN", "TSAN", "UBSAN", "V8_ENABLE_VERIFY_HEAP",
                "COMPONENT_BUILD", "DEBUG"
            ])]
            if relevant:
                discovered["preprocessor_defines"] = relevant

        # Runtime probe (do runtime-probe.js)
        runtime = report.get("sources", {}).get("runtime_probe", {})
        if isinstance(runtime, dict) and "error" not in runtime:
            discovered["runtime"] = {
                "v8_version": runtime.get("v8_version"),
                "is_64bit": runtime.get("platform", {}).get("is_64bit"),
                "sandbox_api_available": runtime.get("build", {}).get("sandbox_api"),
                "verify_heap_active": runtime.get("build", {}).get("verify_heap"),
                "gc_exposed": runtime.get("build", {}).get("gc_exposed"),
                "compilers": runtime.get("compilers", {}),
            }
            # Sandbox API methods descobertos
            methods = runtime.get("sandbox_api_methods", [])
            if methods:
                discovered["sandbox_api_methods"] = [m["name"] for m in methods]

        config["runtime_discovered"] = discovered

        with open(yaml_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        print(f"  Flags vivas gravadas no YAML: {len(discovered)} campos")

    except Exception as e:
        print(f"  ERRO ao gravar flags no YAML: {e}")
        # Não falhar o introspect por causa de writeback — o JSON report já está salvo


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
            print("ERRO: Falta argumento: path do args.gn", file=sys.stderr)
            sys.exit(1)
        cmd_verify_args(yaml_path, config_name, sys.argv[4])
    elif cmd == "post":
        if len(sys.argv) < 5:
            print("ERRO: Falta argumento: path do d8", file=sys.stderr)
            sys.exit(1)
        cmd_post(yaml_path, config_name, sys.argv[4])
    elif cmd == "introspect":
        if len(sys.argv) < 6:
            print("ERRO: Falta argumentos: path do d8 e build_dir", file=sys.stderr)
            print("Uso: introspect <yaml> <config> <d8_path> <build_dir>", file=sys.stderr)
            sys.exit(1)
        cmd_introspect(yaml_path, config_name, sys.argv[4], sys.argv[5])
    elif cmd == "update-status":
        status = sys.argv[4] if len(sys.argv) > 4 else "UNKNOWN"
        version = sys.argv[5] if len(sys.argv) > 5 else ""
        size = sys.argv[6] if len(sys.argv) > 6 else ""
        cmd_update_status(yaml_path, config_name, status, version, size)
    else:
        print(f"ERRO: Comando desconhecido: {cmd}", file=sys.stderr)
        print("Comandos: pre | post | verify-args | introspect | update-status", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
