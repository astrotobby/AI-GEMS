$ErrorActionPreference = "Stop"
$repo = "C:\Users\chanz\AI-GEMS"
$project = Join-Path $repo "whop-clipper"
$logDir = Join-Path $project "logs\whop-mcp"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("connector-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

# Codex is configured to use the local LLM7 OpenAI-compatible proxy. Never silently
# run a Whop mutation if the agent backend is offline or MCP is unauthenticated.
if (-not (Test-NetConnection 127.0.0.1 -Port 5011 -InformationLevel Quiet)) {
    "[$(Get-Date -Format o)] BLOCKED: Codex model proxy 127.0.0.1:5011 is offline. Start LLM7 Proxy; no Whop changes made." |
        Tee-Object -FilePath $log
    exit 2
}

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
    $prompt | codex exec --sandbox danger-full-access -C $repo - 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) { throw "Codex MCP task exited with code $LASTEXITCODE" }
    "[$(Get-Date -Format o)] MCP task completed." | Add-Content $log
} catch {
    "[$(Get-Date -Format o)] TASK FAILED: $($_.Exception.Message)" | Add-Content $log
    exit 1
}

# Retain two weeks of connector logs.
Get-ChildItem $logDir -File | Where-Object LastWriteTime -lt (Get-Date).AddDays(-14) |
    Remove-Item -Force -ErrorAction SilentlyContinue
