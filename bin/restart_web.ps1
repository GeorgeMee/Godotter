param(
  [int]$Port = 9898,
  [string]$HostAddr = "127.0.0.1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

& "$PSScriptRoot\\stop_web.ps1" -Port $Port | Out-Host
Start-Sleep -Milliseconds 300
& "$PSScriptRoot\\run_web.ps1" -Port $Port -HostAddr $HostAddr

