$ErrorActionPreference = "Stop"

$base = "http://127.0.0.1:8080"
$apiKey = $env:SANDBOX_API_KEY
if (-not $apiKey) {
  $apiKey = "dev-secret"
}
$headers = @{ "X-Sandbox-Api-Key" = $apiKey }

Invoke-RestMethod "$base/healthz"
Invoke-RestMethod "$base/context" -Headers $headers

Invoke-RestMethod "$base/file/write" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"path":"hello.txt","content":"hello sandbox\n"}'

Invoke-RestMethod "$base/file/read" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"path":"hello.txt"}'

Invoke-RestMethod "$base/shell/exec" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"command":"python --version && ls -la","timeout":10}'

Invoke-RestMethod "$base/shell/exec" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"command":"python -c \"print(''x'' * 40000)\"","timeout":10}'

Invoke-RestMethod "$base/file/list" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"path":"."}'
