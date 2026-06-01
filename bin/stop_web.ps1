param(
  [int]$Port = 9898
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ListeningPids([int]$p) {
  $matches = netstat -ano | Select-String (":$p\\s")
  if (-not $matches) { return @() }
  $pidList = @()
  foreach ($m in $matches) {
    $parts = ($m.Line -split '\\s+') | Where-Object { $_ -ne '' }
    $pidText = $parts[-1]
    if ($pidText -match '^\\d+$') { $pidList += [int]$pidText }
  }
  return ($pidList | Sort-Object -Unique)
}

$pids = Get-ListeningPids $Port
if (-not $pids -or $pids.Count -eq 0) {
  Write-Host "No listeners found on port $Port"
  exit 0
}

Write-Host ("Stopping listeners on port {0}: {1}" -f $Port, ($pids -join ','))
foreach ($procId in $pids) {
  try {
    Stop-Process -Id $procId -Force -ErrorAction Stop
    Write-Host "Stopped PID $procId"
  } catch {
    Write-Host "Failed to stop PID $procId: $($_.Exception.Message)"
  }
}

