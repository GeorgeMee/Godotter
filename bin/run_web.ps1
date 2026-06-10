param(
  [int]$Port = 9898,
  [string]$HostAddr = "127.0.0.1",
  [switch]$NoReload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) '..')
Set-Location $repoRoot

Write-Host "Starting Godotter Web Console on http://$HostAddr`:$Port"
$args = @(
  "run",
  "--extra",
  "web",
  "uvicorn",
  "godotter_web.app:app",
  "--host",
  $HostAddr,
  "--port",
  "$Port"
)
if (-not $NoReload) {
  $args += @("--reload", "--reload-dir", "src", "--reload-dir", "templates")
}
uv @args
