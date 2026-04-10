#!/bin/bash
# =============================================================================
# fuzz-loop.sh -- V8 Sandbox Escape Fuzzing Harness
#
# Runs a PoC JS file against d8 repeatedly, classifying exit codes to detect
# potential sandbox bypass signals (SIGSEGV/SIGBUS outside sandbox).
#
# Usage: ./fuzz-loop.sh <d8_path> <poc_file> [iterations]
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
# Argument parsing
# ---------------------------------------------------------------------------
if [[ $# -lt 2 ]]; then
    echo -e "${RED}ERROR: Missing required arguments.${RESET}"
    echo "Usage: $0 <d8_path> <poc_file> [iterations]"
    echo ""
    echo "  d8_path      Path to the d8 binary (built with sandbox fuzzing support)"
    echo "  poc_file     JavaScript PoC file to execute"
    echo "  iterations   Number of iterations (default: 10000)"
    exit 1
fi

D8_PATH="$1"
POC_FILE="$2"
ITERATIONS="${3:-10000}"

# ---------------------------------------------------------------------------
# Validate inputs -- fail loud, fail early
# ---------------------------------------------------------------------------
if [[ ! -x "$D8_PATH" ]]; then
    echo -e "${RED}FATAL: d8 binary not found or not executable: ${D8_PATH}${RESET}"
    exit 1
fi

if [[ ! -f "$POC_FILE" ]]; then
    echo -e "${RED}FATAL: PoC file not found: ${POC_FILE}${RESET}"
    exit 1
fi

if ! [[ "$ITERATIONS" =~ ^[0-9]+$ ]] || [[ "$ITERATIONS" -lt 1 ]]; then
    echo -e "${RED}FATAL: iterations must be a positive integer, got: ${ITERATIONS}${RESET}"
    exit 1
fi

# ---------------------------------------------------------------------------
# d8 flags
# ---------------------------------------------------------------------------
D8_FLAGS=(
    --sandbox-fuzzing
    --allow-natives-syntax
    --expose-gc
)

# ---------------------------------------------------------------------------
# Crash output directory
# ---------------------------------------------------------------------------
RUN_TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
CRASH_DIR="crashes/${RUN_TIMESTAMP}"
mkdir -p "$CRASH_DIR"

# ---------------------------------------------------------------------------
# Core dump setup
# ---------------------------------------------------------------------------
CORE_DUMPS_ENABLED=0
CORE_LIMIT="$(ulimit -c 2>/dev/null || echo 0)"
if [[ "$CORE_LIMIT" != "0" ]]; then
    CORE_DUMPS_ENABLED=1
    echo -e "${GREEN}[INIT] Core dumps enabled (ulimit -c = ${CORE_LIMIT})${RESET}"
else
    echo -e "${YELLOW}[INIT] Core dumps DISABLED (ulimit -c = 0). Run 'ulimit -c unlimited' to enable.${RESET}"
fi

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
COUNT_TOTAL=0
COUNT_OK=0
COUNT_SANDBOX_CRASH=0
COUNT_SIGABRT=0
COUNT_SIGSEGV=0
COUNT_SIGBUS=0
COUNT_OTHER=0
BYPASS_DETECTED=0

# ---------------------------------------------------------------------------
# Graceful shutdown on Ctrl+C
# ---------------------------------------------------------------------------
INTERRUPTED=0

print_summary() {
    echo ""
    echo -e "${BOLD}================================================================${RESET}"
    echo -e "${BOLD}  FUZZING SUMMARY${RESET}"
    echo -e "${BOLD}================================================================${RESET}"
    echo -e "  d8 binary   : ${D8_PATH}"
    echo -e "  PoC file    : ${POC_FILE}"
    echo -e "  Crash dir   : ${CRASH_DIR}"
    echo ""
    echo -e "  Total runs  : ${COUNT_TOTAL} / ${ITERATIONS}"
    echo -e "  Clean exits : ${GREEN}${COUNT_OK}${RESET}"
    echo -e "  Sandbox crash (exit 0 w/ crash): ${CYAN}${COUNT_SANDBOX_CRASH}${RESET}"
    echo -e "  SIGABRT (134, DCHECK)          : ${YELLOW}${COUNT_SIGABRT}${RESET}"
    echo -e "  SIGSEGV (139, potential bypass) : ${RED}${COUNT_SIGSEGV}${RESET}"
    echo -e "  SIGBUS  (135, potential bypass) : ${RED}${COUNT_SIGBUS}${RESET}"
    echo -e "  Other/unexpected               : ${YELLOW}${COUNT_OTHER}${RESET}"
    echo ""
    if [[ $BYPASS_DETECTED -gt 0 ]]; then
        echo -e "${RED}${BOLD}  *** POTENTIAL SANDBOX BYPASS DETECTED: ${BYPASS_DETECTED} signal(s) ***${RESET}"
        echo -e "${RED}  Check ${CRASH_DIR}/ for crash artifacts.${RESET}"
    else
        echo -e "${GREEN}  No sandbox bypass signals detected.${RESET}"
    fi
    if [[ $INTERRUPTED -eq 1 ]]; then
        echo -e "${YELLOW}  (Run was interrupted by user)${RESET}"
    fi
    echo -e "${BOLD}================================================================${RESET}"
}

cleanup() {
    INTERRUPTED=1
    echo ""
    echo -e "${YELLOW}[!] Ctrl+C received -- shutting down gracefully...${RESET}"
    print_summary
    exit 0
}

trap cleanup SIGINT SIGTERM

# ---------------------------------------------------------------------------
# Save crash artifact
# ---------------------------------------------------------------------------
save_crash() {
    local exit_code="$1"
    local iter="$2"
    local output="$3"
    local classification="$4"

    local crash_subdir="${CRASH_DIR}/iter_${iter}_exit_${exit_code}_${classification}"
    mkdir -p "$crash_subdir"

    # Save the PoC that was run
    cp "$POC_FILE" "$crash_subdir/poc.js"

    # Save stdout/stderr
    echo "$output" > "$crash_subdir/output.txt"

    # Save metadata
    cat > "$crash_subdir/meta.txt" <<METAEOF
iteration:      ${iter}
exit_code:      ${exit_code}
classification: ${classification}
timestamp:      $(date -u +%Y-%m-%dT%H:%M:%SZ)
d8_binary:      ${D8_PATH}
d8_flags:       ${D8_FLAGS[*]}
poc_file:       ${POC_FILE}
METAEOF

    # Check for core dump
    if [[ $CORE_DUMPS_ENABLED -eq 1 ]]; then
        # Common core dump patterns
        local core_candidates=("core" "core.$$" "core.${iter}")
        for cf in "${core_candidates[@]}"; do
            if [[ -f "$cf" ]]; then
                mv "$cf" "$crash_subdir/core"
                echo -e "${CYAN}  [CORE] Core dump saved: ${crash_subdir}/core${RESET}"
                break
            fi
        done
    fi

    echo "$crash_subdir"
}

# ---------------------------------------------------------------------------
# MAIN FUZZING LOOP
# ---------------------------------------------------------------------------
echo -e "${BOLD}================================================================${RESET}"
echo -e "${BOLD}  V8 SANDBOX ESCAPE FUZZER${RESET}"
echo -e "${BOLD}================================================================${RESET}"
echo -e "  d8 binary  : ${D8_PATH}"
echo -e "  PoC file   : ${POC_FILE}"
echo -e "  Iterations : ${ITERATIONS}"
echo -e "  Flags      : ${D8_FLAGS[*]}"
echo -e "  Crash dir  : ${CRASH_DIR}"
echo -e "  Core dumps : $(if [[ $CORE_DUMPS_ENABLED -eq 1 ]]; then echo 'ENABLED'; else echo 'DISABLED'; fi)"
echo -e "${BOLD}================================================================${RESET}"
echo ""

for (( i=1; i<=ITERATIONS; i++ )); do
    # Live progress (overwrite line)
    printf "\r${CYAN}[%d/%d]${RESET} ok=%d sandbox=%d abort=%d ${RED}segv=%d bus=%d${RESET} other=%d" \
        "$i" "$ITERATIONS" \
        "$COUNT_OK" "$COUNT_SANDBOX_CRASH" "$COUNT_SIGABRT" \
        "$COUNT_SIGSEGV" "$COUNT_SIGBUS" "$COUNT_OTHER"

    # Run d8 and capture output + exit code
    set +e
    OUTPUT="$("$D8_PATH" "${D8_FLAGS[@]}" "$POC_FILE" 2>&1)"
    EXIT_CODE=$?
    set -e

    COUNT_TOTAL=$((COUNT_TOTAL + 1))

    case $EXIT_CODE in
        0)
            # Check if output contains crash indicators even on exit 0
            if echo "$OUTPUT" | grep -qi "sandbox\|crash\|fault\|violation"; then
                COUNT_SANDBOX_CRASH=$((COUNT_SANDBOX_CRASH + 1))
                save_crash "$EXIT_CODE" "$i" "$OUTPUT" "sandbox_crash" > /dev/null
            else
                COUNT_OK=$((COUNT_OK + 1))
            fi
            ;;

        134)
            # SIGABRT -- typically DCHECK failure. Interesting but not a bypass.
            COUNT_SIGABRT=$((COUNT_SIGABRT + 1))
            CRASH_PATH="$(save_crash "$EXIT_CODE" "$i" "$OUTPUT" "SIGABRT_DCHECK")"
            echo ""
            echo -e "${YELLOW}[SIGABRT] Iteration ${i}: DCHECK/abort (not a bypass). Saved to ${CRASH_PATH}${RESET}"
            ;;

        139)
            # SIGSEGV -- POTENTIAL SANDBOX BYPASS
            COUNT_SIGSEGV=$((COUNT_SIGSEGV + 1))
            BYPASS_DETECTED=$((BYPASS_DETECTED + 1))
            CRASH_PATH="$(save_crash "$EXIT_CODE" "$i" "$OUTPUT" "SIGSEGV_BYPASS")"

            echo ""
            echo -e "${RED}${BOLD}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${RESET}"
            echo -e "${RED}${BOLD}!!!  SIGSEGV OUTSIDE SANDBOX -- POTENTIAL BYPASS DETECTED  !!!${RESET}"
            echo -e "${RED}${BOLD}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${RESET}"
            echo -e "${RED}  Iteration : ${i}${RESET}"
            echo -e "${RED}  Exit code : ${EXIT_CODE}${RESET}"
            echo -e "${RED}  Artifacts : ${CRASH_PATH}${RESET}"
            if [[ $CORE_DUMPS_ENABLED -eq 1 ]] && [[ -f "${CRASH_PATH}/core" ]]; then
                echo -e "${RED}  Core dump : ${CRASH_PATH}/core${RESET}"
            fi
            echo -e "${RED}${BOLD}  STOPPING IMMEDIATELY. Analyze this crash.${RESET}"
            echo ""
            print_summary
            exit 2
            ;;

        135|$(( 128 + 10 )) )
            # SIGBUS (135 = 128+7) -- POTENTIAL SANDBOX BYPASS
            # Also catch signal 10 (SIGBUS on some systems)
            COUNT_SIGBUS=$((COUNT_SIGBUS + 1))
            BYPASS_DETECTED=$((BYPASS_DETECTED + 1))
            CRASH_PATH="$(save_crash "$EXIT_CODE" "$i" "$OUTPUT" "SIGBUS_BYPASS")"

            echo ""
            echo -e "${RED}${BOLD}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${RESET}"
            echo -e "${RED}${BOLD}!!!  SIGBUS OUTSIDE SANDBOX -- POTENTIAL BYPASS DETECTED   !!!${RESET}"
            echo -e "${RED}${BOLD}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${RESET}"
            echo -e "${RED}  Iteration : ${i}${RESET}"
            echo -e "${RED}  Exit code : ${EXIT_CODE}${RESET}"
            echo -e "${RED}  Artifacts : ${CRASH_PATH}${RESET}"
            if [[ $CORE_DUMPS_ENABLED -eq 1 ]] && [[ -f "${CRASH_PATH}/core" ]]; then
                echo -e "${RED}  Core dump : ${CRASH_PATH}/core${RESET}"
            fi
            echo -e "${RED}${BOLD}  STOPPING IMMEDIATELY. Analyze this crash.${RESET}"
            echo ""
            print_summary
            exit 2
            ;;

        *)
            COUNT_OTHER=$((COUNT_OTHER + 1))
            CRASH_PATH="$(save_crash "$EXIT_CODE" "$i" "$OUTPUT" "unexpected_exit_${EXIT_CODE}")"
            echo ""
            echo -e "${YELLOW}[UNEXPECTED] Iteration ${i}: exit code ${EXIT_CODE}. Saved to ${CRASH_PATH}${RESET}"
            ;;
    esac
done

# Final newline after progress counter
echo ""

print_summary
exit 0
