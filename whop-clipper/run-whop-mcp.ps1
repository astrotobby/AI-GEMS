$ErrorActionPreference = "Stop"
$repo = "C:\Users\chanz\AI-GEMS"
$project = Join-Path $repo "whop-clipper"
$logDir = Join-Path $project "logs\whop-mcp"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("connector-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

# Use the Codex executable bundled with the signed-in desktop app so its ChatGPT login
# and Whop OAuth session are available to the unattended scheduled runner.

$prompt = @"
You are the scheduled Whop MCP worker for this owner's clipping automation.
Read whop-clipper/CONNECTOR_TASK.md in this repository and execute its complete workflow now.
Use the authenticated official Whop MCP server configured as 'whop' in Codex. Do not use the
WHOP_TOKEN environment secret or direct Whop REST calls (the saved token returned 401).
Refresh the Kenya (KE) open campaign snapshot fully with pagination; verify it is fresh and
committed to main. Then inspect the ledger and submit only the explicitly queued statuses
(awaiting_submission and needs_livestream), attach the matching public MP4 release asset and
post URLs, and update the ledger only after Whop confirms success. Use idempotency and reconcile
before retrying to avoid duplicate submissions. Do not create fake proof livestreams, claim
unapproved campaigns, or touch jobs outside the task's permitted statuses. Do not publish social
posts; GitHub Actions owns clip creation and scheduled social publishing. If MCP auth, campaign
refresh, asset download, submission, or push fails, report REFRESH/SUBMISSION FAILED with the
precise cause and last successful timestamp; never mark work successful on a local-only change.
"@

try {
    # Prefer the Codex executable bundled with the signed-in desktop app. This keeps the
    # ChatGPT login and Whop OAuth credentials in the same CODEX_HOME and avoids npm downloads.
    $codexCandidate = Get-ChildItem -Path "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($codexCandidate) {
        $codexExe = $codexCandidate.FullName
    } else {
        $codexCommand = Get-Command codex.exe -ErrorAction Stop
        $codexExe = $codexCommand.Source
    }

    $codexVersion = (& $codexExe --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not start Codex CLI: $codexVersion" }
    "[$(Get-Date -Format o)] Using $codexExe ($codexVersion)" | Add-Content $log

    # Fail closed if Whop OAuth is missing or expired. Never run a job through an unauthenticated
    # MCP server and never fall back to the rejected WHOP_TOKEN.
    $mcpStatus = (& $codexExe mcp list 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $mcpStatus -notmatch '(?m)^whop\s+https://mcp\.whop\.com/mcp.*OAuth') {
        $mcpStatus | Add-Content $log
        throw "Whop MCP OAuth is not authenticated for this Windows account. Run the bundled Codex CLI: codex mcp login whop"
    }

    $prompt | & $codexExe exec -c 'model_provider="openai"' -m gpt-6-luna --sandbox danger-full-access -C $repo - 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) { throw "Codex MCP task exited with code $LASTEXITCODE" }
    "[$(Get-Date -Format o)] MCP task completed." | Add-Content $log
} catch {
    "[$(Get-Date -Format o)] TASK FAILED: $($_.Exception.Message)" | Add-Content $log
    exit 1
}

# Retain two weeks of connector logs.
Get-ChildItem $logDir -File | Where-Object LastWriteTime -lt (Get-Date).AddDays(-14) |
    Remove-Item -Force -ErrorAction SilentlyContinue
