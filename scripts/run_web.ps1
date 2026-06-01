param(
  [int]$Port = 9898,
  [string]$HostAddr = "127.0.0.1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSCommandPath)\\..

Write-Host "Starting Godotter Web Console on http://$HostAddr`:$Port"
uv run --extra web uvicorn godotter_web.app:app --host $HostAddr --port $Port

