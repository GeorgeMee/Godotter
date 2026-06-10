param(
  [int]$Port = 9898
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ListeningPids([int]$p) {
  $connections = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  if ($connections) {
    return ($connections | Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique)
  }

  $matches = netstat -ano | Select-String (":$p\s")
  if (-not $matches) {
    return @()
  }

  $pidList = @()
  foreach ($m in $matches) {
    if ($m.Line -notmatch 'LISTENING') {
      continue
    }
    $parts = ($m.Line -split '\s+') | Where-Object { $_ -ne '' }
    $pidText = $parts[-1]
    if ($pidText -match '^\d+$') {
      $pidList += [int]$pidText
    }
  }
  return ($pidList | Sort-Object -Unique)
}

function Get-WebCommandPids([int]$p) {
  $matches = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and
      ($_.CommandLine -match 'godotter_web\.app:app' -or $_.CommandLine -match 'uvicorn') -and
      ($_.CommandLine -match "--port\s+$p" -or $_.CommandLine -match "--port\s+`"$p`"")
    } |
    Select-Object -ExpandProperty ProcessId

  if (-not $matches) {
    return @()
  }
  return ($matches | Sort-Object -Unique)
}

function Get-DescendantPids([int[]]$roots) {
  $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $known = @{}
  $queue = New-Object System.Collections.Queue
  foreach ($root in $roots) {
    $known[$root] = $true
    $queue.Enqueue($root)
  }

  while ($queue.Count -gt 0) {
    $parent = [int]$queue.Dequeue()
    foreach ($proc in ($all | Where-Object { $_.ParentProcessId -eq $parent })) {
      $child = [int]$proc.ProcessId
      if (-not $known.ContainsKey($child)) {
        $known[$child] = $true
        $queue.Enqueue($child)
      }
    }
  }

  return @($known.Keys | ForEach-Object { [int]$_ } | Sort-Object -Unique)
}

$listenerPids = @(Get-ListeningPids $Port)
$commandPids = @(Get-WebCommandPids $Port)
$rootPids = @($listenerPids + $commandPids | Sort-Object -Unique)
$pids = @(Get-DescendantPids $rootPids)
if (-not $pids -or $pids.Count -eq 0) {
  Write-Host "No listeners found on port $Port"
  exit 0
}

Write-Host ("Stopping web processes on port {0}: {1}" -f $Port, ($pids -join ','))
foreach ($procId in $pids) {
  try {
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if (-not $existing) {
      Write-Host "Skipped vanished PID $procId"
      continue
    }
    taskkill.exe /PID $procId /T /F | Out-Null
    Write-Host "Stopped PID $procId"
  } catch {
    Write-Host "Failed to stop PID ${procId}: $($_.Exception.Message)"
  }
}
