param(
    [double]$MaxBudget = 500000,
    [double]$MaxOrderValue = 50000,
    [double]$MaxLoss = 10000,
    [int]$IntervalSeconds = 300,
    [int]$MaxSymbols = 20,
    [int]$MaxNewOrders = 10,
    [int]$QuoteRetries = 3,
    [double]$QuoteRetrySleep = 5,
    [double]$TrendBudgetPct = 0.25,
    [double]$GridBudgetPct = 0.25,
    [double]$TrendMinScore = 68,
    [double]$TrendMinChangePct = 0.8,
    [double]$TrendMaxChangePct = 7,
    [double]$TrendFallbackMinRawScore = 0.65,
    [double]$TrendFallbackMinTurnover = 2000000000,
    [double]$TrendFallbackOrderPct = 0.5,
    [double]$GridStepPct = 1.2,

    [double]$GridTakeProfitPct = 1.8,
    [double]$GridTradePct = 0.25,
    [double]$GridMaxOrderValue = 0,
    [double]$MaxSymbolValuePct = 0.20,
    [switch]$Once,
    [switch]$Background
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunsDir = Join-Path $RepoRoot "state\runs"

if (-not (Test-Path $Python)) {
    $Python = "python"
}
if (-not (Test-Path $RunsDir)) {
    New-Item -ItemType Directory -Force -Path $RunsDir | Out-Null
}

$env:FUTU_TRADE_ENV = "SIMULATE"

$ArgsList = @(
    "scripts/run_hk_futu_sim_session.py",
    "--max-budget", "$MaxBudget",
    "--max-order-value", "$MaxOrderValue",
    "--max-loss", "$MaxLoss",
    "--interval-seconds", "$IntervalSeconds",
    "--max-symbols", "$MaxSymbols",
    "--max-new-orders", "$MaxNewOrders",
    "--quote-retries", "$QuoteRetries",
    "--quote-retry-sleep", "$QuoteRetrySleep",
    "--trend-budget-pct", "$TrendBudgetPct",
    "--grid-budget-pct", "$GridBudgetPct",
    "--trend-min-score", "$TrendMinScore",
    "--trend-min-change-pct", "$TrendMinChangePct",
    "--trend-max-change-pct", "$TrendMaxChangePct",
    "--trend-fallback-min-raw-score", "$TrendFallbackMinRawScore",
    "--trend-fallback-min-turnover", "$TrendFallbackMinTurnover",
    "--trend-fallback-order-pct", "$TrendFallbackOrderPct",
    "--grid-step-pct", "$GridStepPct",

    "--grid-take-profit-pct", "$GridTakeProfitPct",
    "--grid-trade-pct", "$GridTradePct",
    "--grid-max-order-value", "$GridMaxOrderValue",
    "--max-symbol-value-pct", "$MaxSymbolValuePct"
)

if ($Once) {
    $ArgsList += "--force-once"
}

Write-Host "HK Futu SIM session"
Write-Host "RepoRoot       : $RepoRoot"
Write-Host "Python         : $Python"
Write-Host "FUTU_TRADE_ENV : $env:FUTU_TRADE_ENV"
Write-Host "MaxBudget      : $MaxBudget"
Write-Host "MaxOrderValue  : $MaxOrderValue"
Write-Host "MaxLoss        : $MaxLoss"
Write-Host "Trend/Grid/Core: $TrendBudgetPct / $GridBudgetPct / $(1 - $TrendBudgetPct - $GridBudgetPct)"

if ($Background) {
    $Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutLog = Join-Path $RunsDir "hk_futu_sim_session_$Timestamp.out.log"
    $ErrLog = Join-Path $RunsDir "hk_futu_sim_session_$Timestamp.err.log"
    $PidFile = Join-Path $RunsDir "hk_futu_sim_session.pid.json"

    $Process = Start-Process `
        -FilePath $Python `
        -ArgumentList $ArgsList `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -PassThru

    @{
        pid = $Process.Id
        started_at = (Get-Date).ToString("s")
        task = "hk_futu_sim_session"
        futu_trade_env = $env:FUTU_TRADE_ENV
        max_budget = $MaxBudget
        max_order_value = $MaxOrderValue
        max_loss = $MaxLoss
        trend_budget_pct = $TrendBudgetPct
        grid_budget_pct = $GridBudgetPct
        trend_fallback_min_raw_score = $TrendFallbackMinRawScore
        trend_fallback_min_turnover = $TrendFallbackMinTurnover
        trend_fallback_order_pct = $TrendFallbackOrderPct
        grid_step_pct = $GridStepPct
        grid_take_profit_pct = $GridTakeProfitPct

        stdout = $OutLog
        stderr = $ErrLog
        expected_stop = "HK close 16:00 or --force-once"
    } | ConvertTo-Json | Set-Content -Encoding UTF8 $PidFile

    Write-Host "Started PID    : $($Process.Id)"
    Write-Host "PID file       : $PidFile"
    Write-Host "Stdout         : $OutLog"
    Write-Host "Stderr         : $ErrLog"
    Write-Host "Stop command   : Stop-Process -Id $($Process.Id)"
    exit 0
}

Push-Location $RepoRoot
try {
    & $Python @ArgsList
}
finally {
    Pop-Location
}
