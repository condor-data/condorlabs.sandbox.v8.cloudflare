#!/bin/bash
# =============================================================================
# fuzz-mutate.sh -- Combined Mutation + Fuzzing Harness for V8 Sandbox Escape
#
# Generates randomized mutations of a PoC JS file and runs each mutation
# through fuzz-loop.sh, saving the exact mutation + seed that triggers crashes.
#
# Usage: ./fuzz-mutate.sh <d8_path> <poc_template> [iterations] [mutations_per_iter]
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------
RED='\033[1;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
CYAN='\033[1;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ---------------------------------------------------------------------------
# Resolve script directory (for locating sibling scripts)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FUZZ_LOOP="${SCRIPT_DIR}/fuzz-loop.sh"
MUTATE_POC="${SCRIPT_DIR}/mutate-poc.py"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
if [[ $# -lt 2 ]]; then
    echo -e "${RED}ERROR: Missing required arguments.${RESET}"
    echo ""
    echo "Usage: $0 <d8_path> <poc_template> [iterations] [mutations_per_iteration]"
    echo ""
    echo "  d8_path               Path to the d8 binary"
    echo "  poc_template          Base JavaScript PoC file to mutate"
    echo "  iterations            Number of mutation rounds (default: 100)"
    echo "  mutations_per_iter    Runs of fuzz-loop per mutation (default: 100)"
    echo ""
    echo "Total d8 executions = iterations x mutations_per_iter"
    exit 1
fi

D8_PATH="$1"
POC_TEMPLATE="$2"
ITERATIONS="${3:-100}"
MUTATIONS_PER_ITER="${4:-100}"

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------
if [[ ! -x "$D8_PATH" ]]; then
    echo -e "${RED}FATAL: d8 binary not found or not executable: ${D8_PATH}${RESET}"
    exit 1
fi

if [[ ! -f "$POC_TEMPLATE" ]]; then
    echo -e "${RED}FATAL: PoC template not found: ${POC_TEMPLATE}${RESET}"
    exit 1
fi

if [[ ! -f "$FUZZ_LOOP" ]]; then
    echo -e "${RED}FATAL: fuzz-loop.sh not found at: ${FUZZ_LOOP}${RESET}"
    echo "  This script must be in the same directory as fuzz-loop.sh"
    exit 1
fi

if [[ ! -f "$MUTATE_POC" ]]; then
    echo -e "${RED}FATAL: mutate-poc.py not found at: ${MUTATE_POC}${RESET}"
    echo "  This script must be in the same directory as mutate-poc.py"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo -e "${RED}FATAL: python3 not found in PATH.${RESET}"
    exit 1
fi

if ! [[ "$ITERATIONS" =~ ^[0-9]+$ ]] || [[ "$ITERATIONS" -lt 1 ]]; then
    echo -e "${RED}FATAL: iterations must be a positive integer.${RESET}"
    exit 1
fi

if ! [[ "$MUTATIONS_PER_ITER" =~ ^[0-9]+$ ]] || [[ "$MUTATIONS_PER_ITER" -lt 1 ]]; then
    echo -e "${RED}FATAL: mutations_per_iteration must be a positive integer.${RESET}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Working directories
# ---------------------------------------------------------------------------
RUN_TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
WORK_DIR="mutfuzz_${RUN_TIMESTAMP}"
MUTATIONS_DIR="${WORK_DIR}/mutations"
CRASHES_DIR="${WORK_DIR}/crashes"
LOG_FILE="${WORK_DIR}/run.log"

mkdir -p "$MUTATIONS_DIR" "$CRASHES_DIR"

# Copy the original template for reference
cp "$POC_TEMPLATE" "${WORK_DIR}/original_template.js"

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
TOTAL_MUTATIONS=0
TOTAL_D8_RUNS=0
TOTAL_CRASHES=0
TOTAL_BYPASS=0
INTERRUPTED=0

# Intensity cycle -- rotate through mutation strengths
INTENSITIES=("low" "medium" "medium" "high")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
print_final_summary() {
    echo ""
    echo -e "${BOLD}================================================================${RESET}"
    echo -e "${BOLD}  MUTATION FUZZING -- FINAL SUMMARY${RESET}"
    echo -e "${BOLD}================================================================${RESET}"
    echo -e "  d8 binary        : ${D8_PATH}"
    echo -e "  PoC template     : ${POC_TEMPLATE}"
    echo -e "  Work directory   : ${WORK_DIR}"
    echo ""
    echo -e "  Mutation rounds  : ${TOTAL_MUTATIONS} / ${ITERATIONS}"
    echo -e "  Total d8 runs    : ${TOTAL_D8_RUNS}"
    echo -e "  Crash artifacts  : ${TOTAL_CRASHES}"
    echo ""
    if [[ $TOTAL_BYPASS -gt 0 ]]; then
        echo -e "${RED}${BOLD}  *** POTENTIAL SANDBOX BYPASSES: ${TOTAL_BYPASS} ***${RESET}"
        echo -e "${RED}  Check ${CRASHES_DIR}/ for bypass artifacts.${RESET}"
    else
        echo -e "${GREEN}  No sandbox bypass signals detected.${RESET}"
    fi
    if [[ $INTERRUPTED -eq 1 ]]; then
        echo -e "${YELLOW}  (Run was interrupted by user)${RESET}"
    fi
    echo -e "  Full log         : ${LOG_FILE}"
    echo -e "${BOLD}================================================================${RESET}"
}

cleanup() {
    INTERRUPTED=1
    echo ""
    echo -e "${YELLOW}[!] Ctrl+C received -- shutting down mutation fuzzer...${RESET}"
    print_final_summary
    exit 0
}

trap cleanup SIGINT SIGTERM

# ---------------------------------------------------------------------------
# Log helper
# ---------------------------------------------------------------------------
log() {
    echo "[$(date -u +%H:%M:%S)] $*" >> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# MAIN MUTATION FUZZING LOOP
# ---------------------------------------------------------------------------
echo -e "${BOLD}================================================================${RESET}"
echo -e "${BOLD}  V8 SANDBOX ESCAPE -- MUTATION FUZZER${RESET}"
echo -e "${BOLD}================================================================${RESET}"
echo -e "  d8 binary        : ${D8_PATH}"
echo -e "  PoC template     : ${POC_TEMPLATE}"
echo -e "  Mutation rounds  : ${ITERATIONS}"
echo -e "  Runs per mutation: ${MUTATIONS_PER_ITER}"
echo -e "  Total planned    : $((ITERATIONS * MUTATIONS_PER_ITER)) d8 executions"
echo -e "  Work directory   : ${WORK_DIR}"
echo -e "${BOLD}================================================================${RESET}"
echo ""

log "START d8=${D8_PATH} poc=${POC_TEMPLATE} iters=${ITERATIONS} runs_per=${MUTATIONS_PER_ITER}"

for (( round=1; round<=ITERATIONS; round++ )); do
    # Generate a seed for this mutation round
    SEED=$((RANDOM * 32768 + RANDOM))

    # Cycle through intensity levels
    INTENSITY_IDX=$(( (round - 1) % ${#INTENSITIES[@]} ))
    INTENSITY="${INTENSITIES[$INTENSITY_IDX]}"

    MUTATION_FILE="${MUTATIONS_DIR}/mutation_${round}_seed_${SEED}.js"

    echo -e "${CYAN}[Round ${round}/${ITERATIONS}]${RESET} seed=${SEED} intensity=${INTENSITY}"
    log "ROUND ${round} seed=${SEED} intensity=${INTENSITY}"

    # -----------------------------------------------------------------------
    # Step 1: Generate mutation
    # -----------------------------------------------------------------------
    set +e
    # mutate-poc.py writes mutated JS to stdout, diagnostic info to stderr
    MUTATE_STDERR_FILE="$(mktemp)"
    python3 "$MUTATE_POC" "$POC_TEMPLATE" \
        --seed "$SEED" \
        --intensity "$INTENSITY" \
        > "$MUTATION_FILE" 2>"$MUTATE_STDERR_FILE"
    MUTATE_EXIT=$?
    MUTATE_STDERR="$(cat "$MUTATE_STDERR_FILE")"
    rm -f "$MUTATE_STDERR_FILE"
    set -e

    if [[ $MUTATE_EXIT -ne 0 ]]; then
        echo -e "${RED}  [ERROR] Mutation failed (exit ${MUTATE_EXIT}): ${MUTATE_STDERR}${RESET}"
        log "MUTATE_FAIL exit=${MUTATE_EXIT} stderr=${MUTATE_STDERR}"
        continue
    fi

    # Verify the mutation file is not empty
    if [[ ! -s "$MUTATION_FILE" ]]; then
        echo -e "${RED}  [ERROR] Mutation produced empty file, skipping.${RESET}"
        log "MUTATE_EMPTY"
        continue
    fi

    # Save mutation metadata
    cat > "${MUTATION_FILE%.js}.meta" <<METAMETA
round:     ${round}
seed:      ${SEED}
intensity: ${INTENSITY}
template:  ${POC_TEMPLATE}
timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)

REPLAY COMMAND:
  python3 ${MUTATE_POC} ${POC_TEMPLATE} --seed ${SEED} --intensity ${INTENSITY} > replayed.js
  ${FUZZ_LOOP} ${D8_PATH} replayed.js ${MUTATIONS_PER_ITER}
METAMETA

    TOTAL_MUTATIONS=$((TOTAL_MUTATIONS + 1))

    # -----------------------------------------------------------------------
    # Step 2: Run fuzz-loop on the mutation
    # -----------------------------------------------------------------------
    ROUND_CRASH_DIR="${CRASHES_DIR}/round_${round}_seed_${SEED}"

    set +e
    # Run fuzz-loop.sh and capture its exit code
    # fuzz-loop.sh exits 2 on bypass detection, 0 on clean completion
    bash "$FUZZ_LOOP" "$D8_PATH" "$MUTATION_FILE" "$MUTATIONS_PER_ITER" 2>&1 | \
        tee -a "$LOG_FILE" | \
        while IFS= read -r line; do
            # Pass through important lines, suppress verbose progress
            if echo "$line" | grep -qE 'BYPASS|SIGSEGV|SIGBUS|SIGABRT|UNEXPECTED|SUMMARY|ERROR|FATAL'; then
                echo "  $line"
            fi
        done
    FUZZ_EXIT=${PIPESTATUS[0]}
    set -e

    TOTAL_D8_RUNS=$((TOTAL_D8_RUNS + MUTATIONS_PER_ITER))

    if [[ $FUZZ_EXIT -eq 2 ]]; then
        # BYPASS DETECTED -- save everything
        TOTAL_BYPASS=$((TOTAL_BYPASS + 1))
        mkdir -p "$ROUND_CRASH_DIR"

        # Copy the exact mutation that triggered the bypass
        cp "$MUTATION_FILE" "$ROUND_CRASH_DIR/bypass_mutation.js"
        cp "${MUTATION_FILE%.js}.meta" "$ROUND_CRASH_DIR/bypass_mutation.meta"

        # Copy any crash artifacts from fuzz-loop
        if ls crashes/*/iter_*BYPASS* 1>/dev/null 2>&1; then
            cp -r crashes/*/iter_*BYPASS* "$ROUND_CRASH_DIR/" 2>/dev/null || true
        fi

        # Generate replay script
        cat > "$ROUND_CRASH_DIR/replay.sh" <<REPLAY
#!/bin/bash
# Replay the exact mutation that triggered a potential sandbox bypass
# Round: ${round}, Seed: ${SEED}, Intensity: ${INTENSITY}

set -euo pipefail

SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
D8_PATH="${D8_PATH}"
POC_TEMPLATE="${POC_TEMPLATE}"

echo "Regenerating mutation (seed=${SEED}, intensity=${INTENSITY})..."
python3 "${MUTATE_POC}" "\${POC_TEMPLATE}" --seed ${SEED} --intensity ${INTENSITY} > "\${SCRIPT_DIR}/replayed.js"

echo "Running d8 with the bypass mutation..."
"\${D8_PATH}" --sandbox-fuzzing --allow-natives-syntax --expose-gc "\${SCRIPT_DIR}/replayed.js"
echo "Exit code: \$?"
REPLAY
        chmod +x "$ROUND_CRASH_DIR/replay.sh"

        echo ""
        echo -e "${RED}${BOLD}  *** BYPASS in round ${round}! Artifacts saved to: ${ROUND_CRASH_DIR} ***${RESET}"
        echo -e "${RED}  Replay: bash ${ROUND_CRASH_DIR}/replay.sh${RESET}"
        echo ""

        log "BYPASS_DETECTED round=${round} seed=${SEED} artifacts=${ROUND_CRASH_DIR}"

        # Stop on first bypass
        echo -e "${RED}${BOLD}  STOPPING -- analyze the bypass before continuing.${RESET}"
        print_final_summary
        exit 2

    elif [[ $FUZZ_EXIT -ne 0 ]]; then
        # fuzz-loop had some non-bypass crashes or errors
        TOTAL_CRASHES=$((TOTAL_CRASHES + 1))
        mkdir -p "$ROUND_CRASH_DIR"
        cp "$MUTATION_FILE" "$ROUND_CRASH_DIR/crash_mutation.js"
        cp "${MUTATION_FILE%.js}.meta" "$ROUND_CRASH_DIR/crash_mutation.meta"
        log "CRASHES round=${round} seed=${SEED} fuzz_exit=${FUZZ_EXIT}"
    fi

    log "ROUND_DONE ${round} fuzz_exit=${FUZZ_EXIT}"
done

echo ""
print_final_summary
exit 0
