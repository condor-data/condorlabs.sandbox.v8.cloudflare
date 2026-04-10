#!/usr/bin/env python3
"""
mutate-poc.py -- V8 Sandbox PoC Mutation Engine

Takes a JavaScript PoC file targeting the V8 sandbox (using Sandbox.MemoryView,
read64/write64, etc.) and produces randomized mutations to explore nearby
code paths that might reveal sandbox escape vectors.

Usage:
    python3 mutate-poc.py <poc_file> [--seed N] [--intensity low|medium|high]

Output: mutated JS written to stdout.
"""

import argparse
import random
import re
import sys
import os


# ---------------------------------------------------------------------------
# Mutation strategies
# ---------------------------------------------------------------------------

def mutate_numeric_constants(source: str, rng: random.Random, intensity: float) -> str:
    """Randomize numeric literals -- offsets, sizes, indices.

    Targets hex (0x...) and decimal integer literals.  Each literal has a
    probability of being mutated controlled by *intensity* (0.0-1.0).
    """
    def _mutate_hex(m: re.Match) -> str:
        if rng.random() > intensity:
            return m.group(0)
        original = int(m.group(0), 16)
        strategy = rng.choice([
            "shift",       # small +/- offset
            "power",       # nearby power of 2
            "boundary",    # edge values
            "random",      # fully random within reasonable range
            "bitflip",     # flip a random bit
        ])
        if strategy == "shift":
            delta = rng.randint(-16, 16)
            return hex(max(0, original + delta))
        elif strategy == "power":
            exp = rng.randint(0, 20)
            offset = rng.choice([-1, 0, 1])
            return hex(max(0, (1 << exp) + offset))
        elif strategy == "boundary":
            boundaries = [0x0, 0x1, 0x7, 0x8, 0xf, 0xff, 0x100,
                          0xffff, 0x10000, 0x7fffffff, 0x80000000,
                          0xffffffff, 0x100000000]
            return hex(rng.choice(boundaries))
        elif strategy == "random":
            return hex(rng.randint(0, 0xffffffff))
        elif strategy == "bitflip":
            if original == 0:
                return hex(1 << rng.randint(0, 31))
            bit = rng.randint(0, original.bit_length() - 1)
            return hex(original ^ (1 << bit))
        return m.group(0)

    def _mutate_decimal(m: re.Match) -> str:
        if rng.random() > intensity:
            return m.group(0)
        original = int(m.group(0))
        # Skip very small numbers that are likely loop counters or booleans
        if original <= 2 and rng.random() < 0.7:
            return m.group(0)
        strategy = rng.choice(["shift", "double", "boundary", "negate"])
        if strategy == "shift":
            delta = rng.randint(-8, 8)
            return str(max(0, original + delta))
        elif strategy == "double":
            factor = rng.choice([2, 4, 8, 16])
            return str(original * factor)
        elif strategy == "boundary":
            boundaries = [0, 1, -1, 127, 128, 255, 256,
                          65535, 65536, 2147483647, 2147483648]
            return str(rng.choice(boundaries))
        elif strategy == "negate":
            return str(-original) if original > 0 else str(abs(original) + 1)
        return m.group(0)

    # Mutate hex literals first (avoid re-matching inside mutated decimals)
    source = re.sub(r'\b0x[0-9a-fA-F]+\b', _mutate_hex, source)
    # Mutate decimal integers that are standalone (not part of identifiers)
    source = re.sub(r'(?<![a-zA-Z_\.])\b([0-9]{1,10})\b(?![a-zA-Z_x])', _mutate_decimal, source)
    return source


def swap_rw_targets(source: str, rng: random.Random, intensity: float) -> str:
    """Swap read64/write64 target expressions.

    Finds read64(...) and write64(..., ...) calls and swaps their address
    arguments between different call sites.
    """
    if rng.random() > intensity:
        return source

    # Collect all read64/write64 address arguments
    read_pattern = re.compile(r'(read64\s*\()([^)]+)(\))')
    write_pattern = re.compile(r'(write64\s*\()([^,]+)(,)')

    read_matches = list(read_pattern.finditer(source))
    write_matches = list(write_pattern.finditer(source))

    all_addr_args = []
    for m in read_matches:
        all_addr_args.append(m.group(2).strip())
    for m in write_matches:
        all_addr_args.append(m.group(2).strip())

    if len(all_addr_args) < 2:
        return source

    # Shuffle and replace
    shuffled = all_addr_args[:]
    rng.shuffle(shuffled)

    result = source
    idx = 0
    for m in read_matches:
        if idx < len(shuffled):
            result = result[:m.start(2)] + shuffled[idx] + result[m.end(2):]
            # Recalculate offsets after replacement -- simpler to do one pass
            idx += 1

    # Because offsets shift, do a fresh pass for writes on the modified source
    # This is intentionally approximate; exact offset tracking would be fragile
    return result


def change_object_types(source: str, rng: random.Random, intensity: float) -> str:
    """Swap between Array, Object, and TypedArray constructors."""
    if rng.random() > intensity:
        return source

    typed_arrays = [
        "Uint8Array", "Uint16Array", "Uint32Array",
        "Int8Array", "Int16Array", "Int32Array",
        "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array",
    ]

    replacements = {
        "new Array": [
            "new Array",
            "new Uint8Array",
            "new Uint32Array",
            "new Float64Array",
            "new BigUint64Array",
            "Array.from(new Array",  # wrapped -- closing paren reused
        ],
        "new Object": [
            "new Object",
            "Object.create(null",
            "Object.create(Object.prototype",
        ],
    }

    # Swap typed array constructors
    for ta in typed_arrays:
        pattern = re.compile(r'\b' + re.escape(ta) + r'\b')
        if pattern.search(source) and rng.random() < intensity:
            replacement = rng.choice(typed_arrays)
            source = pattern.sub(replacement, source, count=1)

    # Swap Array/Object constructors
    for original, candidates in replacements.items():
        if original in source and rng.random() < intensity:
            chosen = rng.choice(candidates)
            source = source.replace(original, chosen, 1)

    return source


def mutate_gc_calls(source: str, rng: random.Random, intensity: float) -> str:
    """Add or remove gc() calls at random points."""
    lines = source.split('\n')
    result = []

    for line in lines:
        stripped = line.strip()

        # Randomly remove existing gc() calls
        if stripped == 'gc();' and rng.random() < intensity * 0.4:
            # Skip this line (remove the gc call)
            continue

        result.append(line)

        # Randomly insert gc() after certain statement types
        if rng.random() < intensity * 0.15:
            if any(kw in stripped for kw in [';', '}', 'let ', 'var ', 'const ']):
                indent = len(line) - len(line.lstrip())
                result.append(' ' * indent + 'gc();')

    return '\n'.join(result)


def mutate_memory_view_offsets(source: str, rng: random.Random, intensity: float) -> str:
    """Modify Sandbox.MemoryView offset arguments."""
    if rng.random() > intensity:
        return source

    def _mutate_memview(m: re.Match) -> str:
        prefix = m.group(1)
        offset_str = m.group(2).strip()
        suffix = m.group(3)

        # Try to parse and mutate the offset
        strategy = rng.choice(["shift", "align", "boundary", "wrap"])

        try:
            if offset_str.startswith("0x"):
                offset = int(offset_str, 16)
            else:
                offset = int(offset_str)
        except ValueError:
            # It's an expression, not a literal -- add a small delta
            delta = rng.choice(["-1", "-4", "-8", "+1", "+4", "+8",
                                "- 0x8", "+ 0x8", "- 0x10", "+ 0x10"])
            return f"{prefix}{offset_str} {delta}{suffix}"

        if strategy == "shift":
            delta = rng.choice([-0x10, -0x8, -0x4, -1, 1, 0x4, 0x8, 0x10])
            new_offset = max(0, offset + delta)
        elif strategy == "align":
            alignment = rng.choice([4, 8, 16, 0x1000])
            new_offset = (offset // alignment) * alignment
        elif strategy == "boundary":
            new_offset = rng.choice([0, 0x8, 0x10, 0x100, 0x1000,
                                     0x10000, 0x40000000])
        elif strategy == "wrap":
            # Try offsets near 32-bit boundaries
            new_offset = rng.choice([
                0x7fffffff, 0x80000000, 0xffffffff,
                offset ^ 0x80000000, offset | 0x40000000
            ])
        else:
            new_offset = offset

        return f"{prefix}{hex(new_offset)}{suffix}"

    # Match Sandbox.MemoryView(..., OFFSET, ...) or new Sandbox.MemoryView(...)
    pattern = re.compile(
        r'(Sandbox\.MemoryView\s*\([^,]*,\s*)'
        r'([^,\)]+)'
        r'(\s*[,\)])'
    )
    source = pattern.sub(_mutate_memview, source)

    # Also match .getFloat64(OFFSET), .getUint32(OFFSET), etc.
    accessor_pattern = re.compile(
        r'(\.\s*(?:get|set)(?:Float64|Float32|Uint32|Int32|Uint8|BigUint64|BigInt64)\s*\()'
        r'([^,\)]+)'
        r'(\s*[,\)])'
    )
    source = accessor_pattern.sub(_mutate_memview, source)

    return source


def add_noise_operations(source: str, rng: random.Random, intensity: float) -> str:
    """Insert heap noise operations to change GC / allocation behavior."""
    if rng.random() > intensity * 0.5:
        return source

    noise_snippets = [
        "{ let _noise = []; for (let i = 0; i < {n}; i++) _noise.push({{}}); }",
        "{ let _buf = new ArrayBuffer({n}); }",
        "{ let _arr = new Array({n}).fill(0); }",
        "{ let _ta = new Uint32Array({n}); for (let i = 0; i < {n}; i++) _ta[i] = i; }",
        "{ for (let i = 0; i < {n}; i++) {{ let _x = Object.create(null); }} }",
        "{ let _str = 'A'.repeat({n}); }",
    ]

    lines = source.split('\n')
    insert_points = []

    for idx, line in enumerate(lines):
        stripped = line.strip()
        # Insert after semicolons that end "interesting" lines
        if stripped.endswith(';') and any(kw in stripped for kw in
                ['write', 'read', 'MemoryView', 'getFloat', 'setFloat',
                 'getUint', 'setUint', 'DataView']):
            if rng.random() < intensity * 0.3:
                insert_points.append(idx)

    # Insert in reverse order to preserve indices
    for idx in reversed(insert_points):
        n = rng.choice([16, 64, 256, 1024, 4096])
        snippet = rng.choice(noise_snippets).replace("{n}", str(n))
        indent = len(lines[idx]) - len(lines[idx].lstrip())
        lines.insert(idx + 1, ' ' * indent + snippet)

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main mutation pipeline
# ---------------------------------------------------------------------------

INTENSITY_MAP = {
    "low": 0.15,
    "medium": 0.35,
    "high": 0.60,
}


def mutate(source: str, rng: random.Random, intensity: float) -> str:
    """Apply the full mutation pipeline to a PoC source string."""
    # Each mutation stage runs independently
    source = mutate_numeric_constants(source, rng, intensity)
    source = swap_rw_targets(source, rng, intensity)
    source = change_object_types(source, rng, intensity)
    source = mutate_gc_calls(source, rng, intensity)
    source = mutate_memory_view_offsets(source, rng, intensity)
    source = add_noise_operations(source, rng, intensity)
    return source


def main():
    parser = argparse.ArgumentParser(
        description="Mutate a V8 sandbox PoC JS file for fuzz testing."
    )
    parser.add_argument("poc_file", help="Path to the JavaScript PoC file")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for reproducibility (default: random)")
    parser.add_argument("--intensity", choices=["low", "medium", "high"],
                        default="medium",
                        help="Mutation intensity (default: medium)")
    args = parser.parse_args()

    if not os.path.isfile(args.poc_file):
        print(f"FATAL: PoC file not found: {args.poc_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.poc_file, 'r') as f:
        source = f.read()

    if not source.strip():
        print("FATAL: PoC file is empty.", file=sys.stderr)
        sys.exit(1)

    # Seed setup
    seed = args.seed if args.seed is not None else random.randint(0, 2**32 - 1)
    rng = random.Random(seed)
    intensity = INTENSITY_MAP[args.intensity]

    # Print seed to stderr so it can be captured for reproducibility
    print(f"// mutate-poc.py seed={seed} intensity={args.intensity}", file=sys.stderr)

    # Run mutation pipeline
    mutated = mutate(source, rng, intensity)

    # Add header comment for traceability
    header = (
        f"// ===========================================\n"
        f"// MUTATED PoC -- generated by mutate-poc.py\n"
        f"// seed     : {seed}\n"
        f"// intensity: {args.intensity}\n"
        f"// original : {args.poc_file}\n"
        f"// ===========================================\n"
    )

    sys.stdout.write(header + mutated)


if __name__ == "__main__":
    main()
