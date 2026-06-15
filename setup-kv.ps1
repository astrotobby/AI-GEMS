# setup-kv.ps1 — creates all 5 KV namespaces and prints the ids for wrangler.toml.
# Run from the project folder in PowerShell:  ./setup-kv.ps1
# Requires: npm i  (installs wrangler)  and  npx wrangler login  (one time)

$bindings = @("MZ_SESSIONS","MZ_LEADS","MZ_CARTS","MZ_VIDEOS","MZ_ANALYTICS")
Write-Host "Creating KV namespaces..." -ForegroundColor Cyan
foreach ($b in $bindings) {
  Write-Host "`n--- $b ---" -ForegroundColor Yellow
  npx wrangler kv namespace create $b
}
Write-Host "`nCopy each 'id' value above into the matching binding in wrangler.toml." -ForegroundColor Green
