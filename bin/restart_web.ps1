param(
  [int]$Port = 9898,
  [string]$HostAddr = "127.0.0.1",
  [switch]$NoReload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

& "$PSScriptRoot\\stop_web.ps1" -Port $Port | Out-Host
Start-Sleep -Milliseconds 300
if ($NoReload) {
  & "$PSScriptRoot\\run_web.ps1" -Port $Port -HostAddr $HostAddr -NoReload
} else {
  & "$PSScriptRoot\\run_web.ps1" -Port $Port -HostAddr $HostAddr
}
