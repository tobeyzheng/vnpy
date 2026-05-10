#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
    cat <<'EOF'
run.sh — safe unified workflow entry for this repository.

Default behavior:
  - preview only
  - prints the resolved command and expected side effects
  - does not execute anything unless --confirm is provided

Supported commands:
  check       Preview or run scripts/run_healthcheck.py
  plan        Preview or run beginner_full workflow summary
  research    Preview or run research_snapshot workflow summary
  sim-gate    Preview or run simulation_gate workflow summary
  live-gate   Preview or run live_gate workflow summary
  backtest    Preview or run the vn.py CTA backtest entry
  us-sim      Preview or run the US SIM task entry

Examples:
  ./run.sh check
  ./run.sh check --confirm
  ./run.sh plan --preferred-market us
  ./run.sh plan --preferred-market us --confirm
  ./run.sh research --max-candidates 3 --confirm
  ./run.sh sim-gate --confirm
  ./run.sh live-gate --confirm
  ./run.sh backtest --symbol NVDA.US --interval 1m --confirm
  ./run.sh us-sim --use-vnpy-mainline --classic-config configs/classic_multifactor/nvda_g09.json --confirm

Notes:
  - All action commands are preview-only by default because the current
    underlying scripts write local artifacts and some commands probe OpenD,
    broker SDK, or account-related state.
  - The workflow summary commands still write artifacts under
    state/runs/quant_workflow/ even when --summary-only is used.
  - This wrapper does not expose US live submission. Use
    scripts/run_us_live_task.py directly after explicit human confirmation.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

render_command() {
    if [[ $# -eq 0 ]]; then
        return 0
    fi
    printf '%q' "$1"
    shift
    while [[ $# -gt 0 ]]; do
        printf ' %q' "$1"
        shift
    done
    printf '\n'
}

print_lines() {
    local title="$1"
    shift || true
    printf '%s\n' "$title"
    if [[ $# -eq 0 ]]; then
        printf '  - none\n'
        return 0
    fi
    local item
    for item in "$@"; do
        printf '  - %s\n' "$item"
    done
}

if [[ $# -eq 0 ]]; then
    usage
    exit 0
fi

case "$1" in
    help|-h|--help)
        usage
        exit 0
        ;;
esac

ACTION="$1"
shift
CONFIRM=0
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --confirm)
            CONFIRM=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do
                EXTRA_ARGS+=("$1")
                shift
            done
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

CMD=()
CONNECTS=()
WRITES=()
ORDER_EFFECTS=()
NOTES=()

case "$ACTION" in
    check)
        CMD=("$PYTHON_BIN" "$REPO_ROOT/scripts/run_healthcheck.py" "${EXTRA_ARGS[@]}")
        CONNECTS=(
            "Probes local OpenD reachability."
            "Checks Futu SDK availability."
            "Reads account summary inputs used by HealthcheckService."
        )
        WRITES=(
            "state/runs/healthcheck.json"
        )
        ORDER_EFFECTS=(
            "Does not submit SIM or REAL orders."
        )
        NOTES=(
            "This command is not pure read-only in the current repo: it probes connectivity and writes a healthcheck artifact."
        )
        ;;
    plan)
        CMD=("$PYTHON_BIN" "$REPO_ROOT/scripts/quant_workflow/run_quant_workflow.py" "--preset" "beginner_full" "--summary-only" "${EXTRA_ARGS[@]}")
        CONNECTS=(
            "No direct order submission."
            "May reuse local state/runs inputs such as healthcheck, candidate inputs, and backtest report artifacts."
        )
        WRITES=(
            "state/runs/quant_workflow/*_artifact_*.json"
            "state/runs/quant_workflow/*_workflow_*.json"
            "state/runs/quant_workflow/latest_index.json"
        )
        ORDER_EFFECTS=(
            "Does not submit SIM or REAL orders."
        )
        NOTES=(
            "Even with --summary-only, the workflow service still writes artifacts and latest index metadata."
        )
        ;;
    research)
        CMD=("$PYTHON_BIN" "-m" "scripts.quant_workflow" "--preset" "research_snapshot" "--summary-only" "${EXTRA_ARGS[@]}")
        CONNECTS=(
            "No direct order submission."
            "May reuse local workflow-related inputs already present under state/runs/."
        )
        WRITES=(
            "state/runs/quant_workflow/*_artifact_*.json"
            "state/runs/quant_workflow/*_workflow_*.json"
            "state/runs/quant_workflow/latest_index.json"
        )
        ORDER_EFFECTS=(
            "Does not submit SIM or REAL orders."
        )
        NOTES=(
            "This is a report-oriented workflow run, but it still refreshes local workflow artifacts."
        )
        ;;
    sim-gate)
        CMD=("$PYTHON_BIN" "$REPO_ROOT/scripts/quant_workflow/run_quant_workflow.py" "--preset" "simulation_gate" "--summary-only" "${EXTRA_ARGS[@]}")
        CONNECTS=(
            "No direct order submission."
            "May reuse local workflow-related inputs already present under state/runs/."
        )
        WRITES=(
            "state/runs/quant_workflow/*_artifact_*.json"
            "state/runs/quant_workflow/*_workflow_*.json"
            "state/runs/quant_workflow/latest_index.json"
        )
        ORDER_EFFECTS=(
            "Does not submit SIM or REAL orders."
        )
        NOTES=(
            "Use this to preview the simulation-stage readiness summary before any SIM execution command."
        )
        ;;
    live-gate)
        CMD=("$PYTHON_BIN" "$REPO_ROOT/scripts/quant_workflow/run_quant_workflow.py" "--preset" "live_gate" "--summary-only" "${EXTRA_ARGS[@]}")
        CONNECTS=(
            "No direct order submission."
            "May reuse local workflow-related inputs already present under state/runs/."
        )
        WRITES=(
            "state/runs/quant_workflow/*_artifact_*.json"
            "state/runs/quant_workflow/*_workflow_*.json"
            "state/runs/quant_workflow/latest_index.json"
        )
        ORDER_EFFECTS=(
            "Does not submit SIM or REAL orders."
        )
        NOTES=(
            "Use this to preview the live-stage readiness summary and confirmation boundary."
        )
        ;;
    backtest)
        CMD=("$PYTHON_BIN" "$REPO_ROOT/scripts/classic_multifactor/run_vnpy_cta_backtest.py" "${EXTRA_ARGS[@]}")
        CONNECTS=(
            "May fetch historical market data through the configured data path used by the backtest runner."
        )
        WRITES=(
            "state/runs/classic_multifactor/vnpy_cta_backtest_report.json unless --output overrides it"
        )
        ORDER_EFFECTS=(
            "Does not submit SIM or REAL orders."
        )
        NOTES=(
            "This command can refresh backtest artifacts and may take time depending on symbol, interval, and data fetch needs."
        )
        ;;
    us-sim)
        CMD=("$PYTHON_BIN" "$REPO_ROOT/scripts/run_us_sim_task.py" "${EXTRA_ARGS[@]}")
        CONNECTS=(
            "May connect to Futu/OpenD or the vnpy mainline, depending on the selected downstream path and config."
            "May touch SIM account, report, or strategy state files under state/runs/."
        )
        WRITES=(
            "state/runs/ reports, account artifacts, orders, and related SIM outputs depending on the downstream path"
        )
        ORDER_EFFECTS=(
            "May submit SIM orders if the downstream path is configured to do so."
            "Does not submit REAL orders through this wrapper command."
        )
        NOTES=(
            "Pass --use-vnpy-mainline and --classic-config <path> to forward into scripts/classic_multifactor/run_intraday_loop.py."
        )
        ;;
    *)
        usage
        die "unknown command: $ACTION"
        ;;
esac

printf '============================================================\n'
printf ' run.sh preview\n'
printf '============================================================\n'
printf 'Action: %s\n' "$ACTION"
printf 'Repo root: %s\n' "$REPO_ROOT"
printf 'Resolved command:\n'
printf '  %s\n' "$(render_command "${CMD[@]}")"
print_lines "Connects / touches:" "${CONNECTS[@]}"
print_lines "Expected outputs or writes:" "${WRITES[@]}"
print_lines "Trading effect:" "${ORDER_EFFECTS[@]}"
print_lines "Notes:" "${NOTES[@]}"
printf 'Confirmation flag: %s\n' "$CONFIRM"
printf '============================================================\n'

if [[ "$CONFIRM" -ne 1 ]]; then
    printf 'DRY RUN ONLY — add --confirm to execute this command.\n'
    exit 0
fi

exec "${CMD[@]}"
