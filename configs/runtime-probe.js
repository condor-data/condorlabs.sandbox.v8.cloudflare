// runtime-probe.js — Introspecta o d8 em execucao para detectar flags ativas
//
// Rodar: d8 --allow-natives-syntax --sandbox-testing runtime-probe.js
//        d8 --allow-natives-syntax runtime-probe.js  (para sanitizer builds)
//
// Output: JSON com TODAS as flags detectadas no binario rodando.
// O verify-build.py le este JSON e compara com o YAML.

(function() {
    const report = {
        timestamp: new Date().toISOString(),
        v8_version: typeof version === 'function' ? version() : 'unknown',
        platform: {
            is_64bit: false,
            in_simulator: false,
        },
        build: {
            verify_heap: false,
            sandbox_api: false,
            sandbox_testing_flag: false,
            sandbox_fuzzing_flag: false,
            natives_syntax: false,
            gc_exposed: false,
        },
        compilers: {
            turbofan: false,
            sparkplug: false,
            maglev: false,
        },
        sandbox_api_methods: [],
    };

    // === Platform ===
    try { report.platform.is_64bit = %Is64Bit(); } catch(e) {}
    try { report.platform.in_simulator = %RunningInSimulator(); } catch(e) {}

    // === Build flags (probing via natives) ===

    // verify_heap: se %HeapObjectVerify existe e nao crasha
    try {
        %HeapObjectVerify({});
        report.build.verify_heap = true;
    } catch(e) {
        report.build.verify_heap = false;
    }

    // natives_syntax: se estamos aqui, eh porque --allow-natives-syntax funcionou
    report.build.natives_syntax = true;

    // gc exposed
    report.build.gc_exposed = (typeof gc === 'function');

    // Sandbox API (Memory Corruption API)
    report.build.sandbox_api = (typeof Sandbox !== 'undefined' && typeof Sandbox === 'object');

    if (report.build.sandbox_api) {
        // Enumerar metodos do Sandbox object
        try {
            const props = Object.getOwnPropertyNames(Sandbox).sort();
            report.sandbox_api_methods = props.map(k => {
                let type = typeof Sandbox[k];
                let desc = Object.getOwnPropertyDescriptor(Sandbox, k);
                if (desc && desc.get) type = 'getter';
                if (type === 'function') {
                    try { type = `function(${Sandbox[k].length})`; } catch(e) {}
                }
                return { name: k, type: type };
            });
        } catch(e) {}
    }

    // === Compilers ===
    try { report.compilers.turbofan = %IsTurboFanEnabled(); } catch(e) {}
    try { report.compilers.sparkplug = %IsSparkplugEnabled(); } catch(e) {}
    try { report.compilers.maglev = %IsMaglevEnabled(); } catch(e) {}

    // Output como JSON
    print("RUNTIME_PROBE_JSON_START");
    print(JSON.stringify(report, null, 2));
    print("RUNTIME_PROBE_JSON_END");
})();
