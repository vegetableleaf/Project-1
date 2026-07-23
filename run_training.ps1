# run_training.ps1
# ---------------------------------------------------------------------------
# Runner used by the "MoneyAgentTraining" scheduled task. It:
#   1. starts the two dashboard reporters (chain_report + earnings_report --watch,
#      on Base MAINNET) so the dashboard's on-chain / earnings panels stay fed, and
#   2. runs continuous, self-resuming offline-ledger training IF this interpreter
#      has torch (skipped otherwise -- the reporters still run).
# All output goes to training.log. Paths are relative ($PSScriptRoot) and it
# prefers the project's .venv, so it is portable across machines.
#
# Training resumes from checkpoint.pth automatically, so a reboot never loses it.
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"

$projectDir = $PSScriptRoot
# Prefer the project's .venv (Python 3.13 with web3 for the reporters); fall back
# to whatever `python` is on PATH. (Old hardcoded per-user paths are gone.)
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
$python     = if (Test-Path $venvPython) { $venvPython } else { "python" }
$log        = Join-Path $projectDir "training.log"

Set-Location $projectDir

# Safety: guarantee only ONE trainer + one reporter run. Kill any stale ones
# still alive from a previous session before starting, so two processes never
# write to the same checkpoint/ledger at the same time.
Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -like '*money_agent.train*' -or $_.CommandLine -like '*money_agent.chain_report*' -or $_.CommandLine -like '*money_agent.earnings_report*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Start each session with a fresh, clean UTF-8 log (avoids mixed encodings).
Remove-Item $log -Force -ErrorAction SilentlyContinue

# Keep the dashboard's on-chain tab fresh: run the balance reporter in the
# background every 30s. It reads only public wallet addresses (no private key).
$env:CHAIN_STATUS_PATH = Join-Path $projectDir "chain_status.json"
Start-Process -FilePath $python `
    -ArgumentList '-m', 'money_agent.chain_report', '--watch', '30' `
    -WorkingDirectory $projectDir -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $projectDir "chain_report.log")

# Keep the dashboard's earnings panel fresh: the CDP wallet's live USDC/ETH
# balance + x402 sales. Reads the address from cdp_wallet.json (set up CDP first).
# CDP_NETWORK = "base" shows real mainnet money; use "base-sepolia" for testnet.
$env:CDP_NETWORK = "base"
Start-Process -FilePath $python `
    -ArgumentList '-m', 'money_agent.earnings_report', '--watch', '30' `
    -WorkingDirectory $projectDir -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $projectDir "earnings_report.log")

# --- training settings (edit these to taste) ---
$env:MONEY_AGENT_FOREVER    = "1"        # never stop; auto-resume from checkpoint
$env:MONEY_AGENT_BACKEND    = "ledger"   # offline & safe (no private key needed)
$env:MONEY_AGENT_LOOP_DELAY = "0.5"      # gentle pause between generations (CPU/log friendly)

$utf8 = New-Object System.Text.UTF8Encoding($false)   # UTF-8, no BOM

# Run the trainer ONLY if this interpreter has torch. The project's .venv (used
# above for the web3 reporters) is torch-free, so a torch-less machine still keeps
# the dashboard fed -- it just skips the optional offline "colony" training.
$env:PYTHONIOENCODING = "utf-8"
# Detect torch without its "not installed" stderr tripping ErrorActionPreference=Stop.
$prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& $python -c "import torch" 2>$null 1>$null
$hasTorch = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEAP
if ($hasTorch) {
    [System.IO.File]::AppendAllText($log,
        "`r`n===== training started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====`r`n", $utf8)
    # cmd's >> writes Python's raw UTF-8 output without PowerShell's UTF-16 re-encoding,
    # so the dashboard can always parse the generation numbers.
    & cmd.exe /c "`"$python`" -m money_agent.train >> `"$log`" 2>&1"
} else {
    [System.IO.File]::AppendAllText($log,
        "`r`n===== reporters running $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); training skipped (no torch in this interpreter) =====`r`n", $utf8)
}
