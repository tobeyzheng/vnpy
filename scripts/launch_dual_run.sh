#!/usr/bin/env bash
# launch_dual_run.sh — Dual-run SIM session launcher (template, dry-run by default).
#
# Project rule 2: any action that may produce real trading effect, account
# state change, remote state change, or important local artefact change MUST
# be confirmed by the user *before* execution. This script enforces that:
#
#   * Default behaviour is DRY-RUN — it prints the resolved plan and exits 0.
#   * Real launch requires BOTH the --execute flag AND the operator typing
#     the literal phrase "START DUAL RUN" at the interactive prompt.
#   * Pre-flight (scripts/dual_run_preflight.py) is invoked unconditionally;
#     any failed check aborts the launch (no override flag, by design).
#   * Even after launch, the per-side commands DO NOT pass --live-submit and
#     DO NOT set VNPY_LIVE_* env vars — both worktrees still run in dry-run
#     mode at the application level. Real submission still requires the
#     three hard-switches per the project safety boundary.
#
# Usage example:
#
#   scripts/launch_dual_run.sh \
#       --run-a /projects/dual_run/legacy \
#       --run-b /projects/dual_run/vnpy_native \
#       --config configs/classic_multifactor/nvda_g09.json \
#       --symbol-a NVDA \
#       --session-end-bj 04:00 \
#       --session-end-et 16:00
#
# Add --execute to actually launch (still requires interactive confirmation).
#
# Exit codes:
#   0  dry-run rendered cleanly OR launch completed successfully
#   1  preflight failure / launch refused / hard error
#   2  CLI usage error
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
RUN_A=""
RUN_B=""
CONFIG_RELPATH="configs/classic_multifactor/nvda_g09.json"

# Side-A (legacy) entry: scripts/classic_multifactor/run_loop.py; its
# --config takes a *filename* not a path, and times are Beijing local.
LEGACY_ENTRY="scripts/classic_multifactor/run_loop.py"
LEGACY_SYMBOL="NVDA"
LEGACY_CONFIG_NAME="nvda_g09.json"
LEGACY_SESSION_END_BJ="04:00"

# Side-B (vnpy_native) entry: scripts/classic_multifactor/run_intraday_loop.py;
# --config takes a path (relative to its own worktree); times default ET.
VNPY_ENTRY="scripts/classic_multifactor/run_intraday_loop.py"
VNPY_SESSION_END_ET="16:00"

EXECUTE=0
PREFLIGHT_SCRIPT="scripts/dual_run_preflight.py"
EXPECTED_TAG_A="classic-pre-vnpy-rewrite-v1"
EXPECTED_BRANCH_B="classic-vnpy-native-rewrite"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

# Resolve repo root (the directory holding this script's parent).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

LAUNCH_LOG_DIR="${REPO_ROOT}/state/runs/reports"
LAUNCH_LOG="${LAUNCH_LOG_DIR}/launch_dual_run_${TS}.log"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() {
    # Print to stdout and append to the launch log.
    printf '%s\n' "$*"
    if [[ -n "${LAUNCH_LOG:-}" ]]; then
        printf '%s\n' "$*" >>"${LAUNCH_LOG}" 2>/dev/null || true
    fi
}

die() {
    log "ERROR: $*"
    exit "${2:-1}"
}

usage() {
    cat <<'EOF'
launch_dual_run.sh — Dual-run SIM session launcher (template).

Required:
  --run-a <path>             legacy worktree (e.g. /projects/dual_run/legacy)
  --run-b <path>             vnpy_native worktree (e.g. /projects/dual_run/vnpy_native)

Common (defaults shown):
  --config <relpath>         configs/classic_multifactor/nvda_g09.json
                             (used by preflight SHA check; resolved INSIDE each worktree)
  --legacy-config-name <fn>  nvda_g09.json (filename only, legacy run_loop.py joins path)
  --symbol-a <sym>           NVDA  (legacy run_loop.py --symbol)
  --session-end-bj <HH:MM>   04:00 (legacy --session-end, Beijing time)
  --session-end-et <HH:MM>   16:00 (vnpy_native --session-end, America/New_York)
  --legacy-entry <relpath>   scripts/classic_multifactor/run_loop.py
  --vnpy-entry   <relpath>   scripts/classic_multifactor/run_intraday_loop.py
  --expected-tag-a <name>    classic-pre-vnpy-rewrite-v1
  --expected-branch-b <name> classic-vnpy-native-rewrite

Behaviour:
  --execute                  After preflight passes AND interactive
                             "START DUAL RUN" confirmation, launch both sides
                             via nohup (or tmux if available). Without this
                             flag the script prints the plan and exits.

Notes:
  * Always runs preflight first; any fail aborts.
  * Per-side commands intentionally OMIT --live-submit and any VNPY_LIVE_*
    env vars; both sides run in application-level dry-run.
  * Output: state/runs/reports/launch_dual_run_<UTC>.log under the *current*
    working directory of this script (the controller repo, not the worktrees).
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-a) RUN_A="${2:?}"; shift 2;;
        --run-b) RUN_B="${2:?}"; shift 2;;
        --config) CONFIG_RELPATH="${2:?}"; shift 2;;
        --legacy-config-name) LEGACY_CONFIG_NAME="${2:?}"; shift 2;;
        --symbol-a) LEGACY_SYMBOL="${2:?}"; shift 2;;
        --session-end-bj) LEGACY_SESSION_END_BJ="${2:?}"; shift 2;;
        --session-end-et) VNPY_SESSION_END_ET="${2:?}"; shift 2;;
        --legacy-entry) LEGACY_ENTRY="${2:?}"; shift 2;;
        --vnpy-entry) VNPY_ENTRY="${2:?}"; shift 2;;
        --expected-tag-a) EXPECTED_TAG_A="${2:?}"; shift 2;;
        --expected-branch-b) EXPECTED_BRANCH_B="${2:?}"; shift 2;;
        --execute) EXECUTE=1; shift;;
        -h|--help) usage; exit 0;;
        *) usage; die "unknown argument: $1" 2;;
    esac
done

[[ -n "${RUN_A}" ]] || { usage; die "missing --run-a" 2; }
[[ -n "${RUN_B}" ]] || { usage; die "missing --run-b" 2; }

mkdir -p "${LAUNCH_LOG_DIR}"
: >"${LAUNCH_LOG}"

# ---------------------------------------------------------------------------
# Stage 1 — Print resolved plan
# ---------------------------------------------------------------------------
log "============================================================"
log " launch_dual_run.sh  ts=${TS}"
log "============================================================"
log " repo_root            = ${REPO_ROOT}"
log " run-a (legacy)       = ${RUN_A}"
log " run-b (vnpy_native)  = ${RUN_B}"
log " config (preflight)   = ${CONFIG_RELPATH}"
log " expected_tag_a       = ${EXPECTED_TAG_A}"
log " expected_branch_b    = ${EXPECTED_BRANCH_B}"
log " execute              = ${EXECUTE}"
log " launch_log           = ${LAUNCH_LOG}"
log "------------------------------------------------------------"

LEGACY_LOG="${RUN_A}/state/runs/reports/run_${TS}.log"
VNPY_LOG="${RUN_B}/state/runs/reports/run_${TS}.log"
LEGACY_PID_FILE="${RUN_A}/state/runs/reports/run_${TS}.pid"
VNPY_PID_FILE="${RUN_B}/state/runs/reports/run_${TS}.pid"

LEGACY_CMD=(
    python3 "${LEGACY_ENTRY}"
        --symbol "${LEGACY_SYMBOL}"
        --config "${LEGACY_CONFIG_NAME}"
        --session-end "${LEGACY_SESSION_END_BJ}"
        --exit-after-session
)

VNPY_CMD=(
    python3 "${VNPY_ENTRY}"
        --config "${CONFIG_RELPATH}"
        --session-end "${VNPY_SESSION_END_ET}"
        --session-tz "America/New_York"
)

log " planned legacy command (cwd=${RUN_A}):"
log "   ${LEGACY_CMD[*]}"
log "   stdout/stderr -> ${LEGACY_LOG}"
log " planned vnpy_native command (cwd=${RUN_B}):"
log "   ${VNPY_CMD[*]}"
log "   stdout/stderr -> ${VNPY_LOG}"
log "------------------------------------------------------------"
log " NOTE: neither command sets --live-submit nor VNPY_LIVE_* env"
log "       vars; both sides run in application-level dry-run."
log "------------------------------------------------------------"

# ---------------------------------------------------------------------------
# Stage 2 — Preflight (always; no override)
# ---------------------------------------------------------------------------
PREFLIGHT_ABS="${REPO_ROOT}/${PREFLIGHT_SCRIPT}"
[[ -f "${PREFLIGHT_ABS}" ]] || die "preflight script not found: ${PREFLIGHT_ABS}"

log " running preflight ..."
set +e
python3 "${PREFLIGHT_ABS}" \
    --run-a "${RUN_A}" \
    --run-b "${RUN_B}" \
    --expected-tag-a "${EXPECTED_TAG_A}" \
    --expected-branch-b "${EXPECTED_BRANCH_B}" \
    --config "${CONFIG_RELPATH}" 2>&1 | tee -a "${LAUNCH_LOG}"
PREFLIGHT_RC=${PIPESTATUS[0]}
set -e

if [[ "${PREFLIGHT_RC}" -ne 0 ]]; then
    log "preflight exit=${PREFLIGHT_RC}; refusing to launch."
    exit 1
fi
log " preflight OK"
log "------------------------------------------------------------"

# ---------------------------------------------------------------------------
# Stage 3 — Dry-run gate
# ---------------------------------------------------------------------------
if [[ "${EXECUTE}" -eq 0 ]]; then
    log " DRY RUN — no session started. Add --execute to launch."
    log " (preflight already verified the launch preconditions.)"
    exit 0
fi

# ---------------------------------------------------------------------------
# Stage 4 — Interactive confirmation (project rule 2)
# ---------------------------------------------------------------------------
log " --execute supplied. Interactive confirmation required."
printf '\nType the literal phrase:  START DUAL RUN\nto launch both sessions, or anything else to abort.\n> '
read -r CONFIRM
if [[ "${CONFIRM}" != "START DUAL RUN" ]]; then
    log " confirmation phrase mismatch (got: '${CONFIRM}'); aborting."
    exit 1
fi
log " confirmation accepted at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------------------
# Stage 5 — Launch (nohup ... &; PID file per side)
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "${LEGACY_LOG}")"
mkdir -p "$(dirname "${VNPY_LOG}")"

# Sanity: refuse to overwrite an active pid file if a process is still alive.
for pf in "${LEGACY_PID_FILE}" "${VNPY_PID_FILE}"; do
    if [[ -f "${pf}" ]]; then
        existing_pid=$(cat "${pf}" 2>/dev/null || echo "")
        if [[ -n "${existing_pid}" ]] && kill -0 "${existing_pid}" 2>/dev/null; then
            die "stale pid file with live process: ${pf} pid=${existing_pid}"
        fi
    fi
done

log " starting LEGACY side ..."
(
    cd "${RUN_A}"
    nohup "${LEGACY_CMD[@]}" >"${LEGACY_LOG}" 2>&1 &
    echo $! >"${LEGACY_PID_FILE}"
)
LEGACY_PID="$(cat "${LEGACY_PID_FILE}")"
log "   legacy pid=${LEGACY_PID}  log=${LEGACY_LOG}"

log " starting VNPY_NATIVE side ..."
(
    cd "${RUN_B}"
    nohup "${VNPY_CMD[@]}" >"${VNPY_LOG}" 2>&1 &
    echo $! >"${VNPY_PID_FILE}"
)
VNPY_PID="$(cat "${VNPY_PID_FILE}")"
log "   vnpy_native pid=${VNPY_PID}  log=${VNPY_LOG}"

log "------------------------------------------------------------"
log " Both sides launched. Tail the logs:"
log "   tail -f ${LEGACY_LOG}"
log "   tail -f ${VNPY_LOG}"
log " To stop:"
log "   kill ${LEGACY_PID} ${VNPY_PID}"
log " End-of-day reconciliation:"
log "   python3 scripts/diff_dual_run.py --run-a ${RUN_A} --run-b ${RUN_B} --strict-rids --markdown"
log "============================================================"
exit 0
