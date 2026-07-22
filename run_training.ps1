# run_training.ps1
# ---------------------------------------------------------------------------
# Runner used by the "MoneyAgentTraining" scheduled task. It starts continuous,
# self-resuming training in the SAFE offline ledger backend (no wallet key) and
# writes all output to training.log so you can watch progress.
#
# It resumes from checkpoint.pth automatically, so a reboot never loses progress.
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"

$projectDir = "C:\Users\bzhu\Documents\Project1"
$python     = "C:\Users\bzhu\AppData\Local\Python\pythoncore-3.14-64\python.exe"
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
[System.IO.File]::AppendAllText($log,
    "`r`n===== training started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====`r`n", $utf8)

# Run the trainer, appending stdout+stderr to the log as clean UTF-8 bytes.
# (cmd's >> writes Python's raw UTF-8 output without PowerShell's UTF-16 re-encoding,
#  so the dashboard can always parse the generation numbers.)
$env:PYTHONIOENCODING = "utf-8"
& cmd.exe /c "`"$python`" -m money_agent.train >> `"$log`" 2>&1"
