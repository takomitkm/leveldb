<#
  run-tier1.ps1 - E21 Tier-1: device-level flush evidence on real hardware.

  Answers: does FlushFileBuffers on a PARENT DIRECTORY emit a flush to the
  storage stack, compared with FlushFileBuffers on the VOLUME ROOT (the
  effective call in the QEMU rigs)?

  Four cumulative arms, each traced in its own ETW session (logman +
  tracerpt, both built into Windows):

    A  create+write, no flush                 (baseline / journal noise)
    B  A + FlushFileBuffers(file)             (upstream behaviour)
    C  B + FlushFileBuffers(parent dir)       (leveldb 30b34e0 / SQLite shape)
    D  B + FlushFileBuffers(volume root)      (positive control for the tracer)

  Deltas vs B isolate the contribution of the call added by that arm.

  Works on Windows PowerShell 5.1 and PowerShell 7.  Everything is written
  to a transcript log from the first line, so a crash still leaves evidence.

  Usage:
    # in an already-elevated shell (recommended locally - single window):
    powershell -NoProfile -ExecutionPolicy Bypass -File .\run-tier1.ps1 -Volume D: -NoElevate

    # let the script elevate itself (a second window opens):
    powershell -NoProfile -ExecutionPolicy Bypass -File .\run-tier1.ps1 -Volume D:

    # unattended (CI): no pause, no elevation prompt
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\run-tier1.ps1 -Volume D: -NoPause
#>
param(
  [string]$Volume = 'D:',
  [int]$Reps = 3,
  [int]$TraceLevel = 5,
  [switch]$KeepEtl,
  [switch]$NoElevate,
  [switch]$NoPause,
  [switch]$Elevated
)

$ErrorActionPreference = 'Stop'
$script:Root       = Split-Path -Parent $PSCommandPath
$script:Stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
$script:LogPath    = Join-Path $script:Root ("tier1-log-{0}.txt" -f $script:Stamp)
$script:ReportPath = Join-Path $script:Root ("tier1-report-{0}.txt" -f $script:Stamp)
$script:Failed     = $false
$script:Relaunched = $false
$script:ProvidersUsed = ''

try { Start-Transcript -Path $script:LogPath -Force | Out-Null } catch { }

try {
  # -------------------------------------------------------------- elevation
  $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  $isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

  if (-not $isAdmin) {
    if ($NoElevate) {
      throw ("Administrator rights are required (ETW kernel providers). " +
             "Open PowerShell with 'Run as administrator' and re-run with -NoElevate.")
    }
    $exe = (Get-Process -Id $PID).Path
    $argLine = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Volume "{1}" -Reps {2} -TraceLevel {3} -Elevated' -f `
        $PSCommandPath, $Volume, $Reps, $TraceLevel
    Write-Host "Not elevated - opening an elevated window..." -ForegroundColor Yellow
    Start-Process -FilePath $exe -ArgumentList $argLine -Verb RunAs | Out-Null
    $script:Relaunched = $true
    Write-Host ("That window runs the test and keeps its own log: {0}" -f $script:LogPath) -ForegroundColor Yellow
    return
  }

  # ---------------------------------------------------------- volume checks
  $Volume = $Volume.TrimEnd('\')
  if ($Volume -notmatch '^[A-Za-z]:$') { throw "Volume must look like D: (got '$Volume')" }
  if ($Volume -ieq $env:SystemDrive)   { throw "Refusing to run on the system volume ($env:SystemDrive). Pick a DATA volume." }

  $disk = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='{0}'" -f $Volume)
  if (-not $disk) {
    $avail = (Get-CimInstance Win32_LogicalDisk | Where-Object { $_.DriveType -eq 3 } | ForEach-Object { $_.DeviceID }) -join ', '
    throw "Volume $Volume not found. Fixed volumes on this machine: $avail"
  }
  if ($disk.FileSystem -ne 'NTFS') {
    throw "Volume $Volume is $($disk.FileSystem); this test requires NTFS"
  }
  if ($disk.FreeSpace -lt 64MB) {
    throw ("Volume $Volume has only {0} MB free" -f [math]::Round($disk.FreeSpace / 1MB))
  }

  $workDir = Join-Path $Volume '\e21'
  New-Item -ItemType Directory -Force -Path $workDir | Out-Null
  Get-ChildItem -Path $workDir -Filter 'a21_*' -ErrorAction SilentlyContinue |
      Remove-Item -Force -ErrorAction SilentlyContinue
  $rootDir = $Volume + '\'

  $os = Get-CimInstance Win32_OperatingSystem
  Write-Host ""
  Write-Host "=== E21 Tier-1: device-level flush evidence ===" -ForegroundColor Cyan
  Write-Host ("Host      : {0}" -f $env:COMPUTERNAME)
  Write-Host ("OS        : {0} (build {1})" -f $os.Caption, [Environment]::OSVersion.Version.Build)
  Write-Host ("PS        : {0}" -f $PSVersionTable.PSVersion)
  Write-Host ("Volume    : {0}  ({1}, {2} GB free)" -f $Volume, $disk.FileSystem, [math]::Round($disk.FreeSpace / 1GB, 1))
  Write-Host ("Scratch   : {0}" -f $workDir)
  Write-Host ("Reps/arm  : {0}   TraceLevel: {1}" -f $Reps, $TraceLevel)
  Write-Host ""

  # ------------------------------------------------------------ P/Invoke API
  if (-not ('E21Native' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class E21Native {
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern IntPtr CreateFileW(string name, uint access, uint share,
      IntPtr sa, uint disp, uint flags, IntPtr tmpl);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool WriteFile(IntPtr h, byte[] buf, int n, out int written, IntPtr ov);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool FlushFileBuffers(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool CloseHandle(IntPtr h);
}
'@
  }

  $GENERIC_WRITE   = 0x40000000
  $SHARE_ALL       = 0x00000007
  $CREATE_ALWAYS   = 2
  $OPEN_EXISTING   = 3
  $FLAG_BACKUP_SEM = 0x02000000

  function Get-Win32Err { [Runtime.InteropServices.Marshal]::GetLastWin32Error() }

  function New-PaddedFile {
    param([string]$Path, [string]$Tag)
    $h = [E21Native]::CreateFileW($Path, $GENERIC_WRITE, 0, [IntPtr]::Zero, $CREATE_ALWAYS, 0, [IntPtr]::Zero)
    if ($h.ToInt64() -eq -1) { throw "CreateFileW($Path) failed, err=$(Get-Win32Err)" }
    $buf = [Text.Encoding]::ASCII.GetBytes(('P' * 2048))
    $tb  = [Text.Encoding]::ASCII.GetBytes($Tag)
    [Array]::Copy($tb, 0, $buf, 0, [Math]::Min($tb.Length, 2048))
    $written = 0
    if (-not [E21Native]::WriteFile($h, $buf, $buf.Length, [ref]$written, [IntPtr]::Zero)) {
      $e = Get-Win32Err; [void][E21Native]::CloseHandle($h)
      throw "WriteFile($Path) failed, err=$e"
    }
    return $h
  }

  function Open-Directory {
    param([string]$Path)
    $h = [E21Native]::CreateFileW($Path, $GENERIC_WRITE, $SHARE_ALL, [IntPtr]::Zero, $OPEN_EXISTING, $FLAG_BACKUP_SEM, [IntPtr]::Zero)
    if ($h.ToInt64() -eq -1) { throw "CreateFileW($Path) failed, err=$(Get-Win32Err)" }
    return $h
  }

  function Measure-Flush {
    param([IntPtr]$Handle, [string]$Label)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $ok = [E21Native]::FlushFileBuffers($Handle)
    $sw.Stop()
    $err = 0; if (-not $ok) { $err = Get-Win32Err }
    return [pscustomobject]@{
      Label = $Label; Ok = [bool]$ok; Err = $err
      Ms    = [math]::Round($sw.Elapsed.TotalMilliseconds, 3)
    }
  }

  # ------------------------------------------------------------ ETW plumbing
  function Start-FlushTrace {
    param([string]$Name, [string]$Etl)
    & logman stop   $Name -ets 2>&1 | Out-Null
    & logman delete $Name     2>&1 | Out-Null
    $sets = @(
      @('Microsoft-Windows-Kernel-Disk','Microsoft-Windows-StorPort','Microsoft-Windows-Kernel-File'),
      @('Microsoft-Windows-StorPort'),
      @('Microsoft-Windows-Kernel-Disk'),
      @('Microsoft-Windows-Kernel-File')
    )
    foreach ($set in $sets) {
      $larg = @('create','trace',$Name,'-o',$Etl,'-ets','-level',$TraceLevel,'-max','256')
      foreach ($p in $set) { $larg += @('-p', $p) }
      & logman @larg 2>&1 | Out-Null
      if ($LASTEXITCODE -eq 0) { $script:ProvidersUsed = ($set -join ','); return }
    }
    throw "logman could not start any ETW provider set (last exit $LASTEXITCODE)"
  }

  function Stop-FlushTrace {
    param([string]$Name)
    & logman stop $Name -ets 2>&1 | Out-Null
    Start-Sleep -Milliseconds 500
    & logman delete $Name 2>&1 | Out-Null
  }

  function Convert-Trace {
    param([string]$Etl, [string]$Csv)
    if (-not (Test-Path $Etl)) { return $null }
    & tracerpt $Etl -o $Csv -of CSV -y 2>&1 | Out-Null
    if (-not (Test-Path $Csv)) { return $null }
    return $Csv
  }

  function Measure-FlushEvents {
    param([string]$Csv)
    $flush = 0; $srb = 0
    $samples = New-Object System.Collections.Generic.List[string]
    if (-not $Csv) {
      return [pscustomobject]@{ FlushEvents = 0; SrbFlush = 0; Samples = @() }
    }
    foreach ($line in [IO.File]::ReadLines($Csv)) {
      $hit = $false
      if ($line -match '(?i)flush') { $flush++; $hit = $true }
      if ($line -match '(?i)SrbFunction' -and $line -match '(?i)(0x0?F\b|,15,|\b15\b)') { $srb++; $hit = $true }
      if ($hit -and $samples.Count -lt 4) {
        $samples.Add($line.Substring(0, [Math]::Min(220, $line.Length))) | Out-Null
      }
    }
    return [pscustomobject]@{ FlushEvents = $flush; SrbFlush = $srb; Samples = $samples.ToArray() }
  }

  # ------------------------------------------------------------------- arms
  $arms = @(
    [pscustomobject]@{ Id = 'A'; Name = 'no-flush        '; UseFile = $false; UseDir = $false; UseRoot = $false },
    [pscustomobject]@{ Id = 'B'; Name = 'flush(file)     '; UseFile = $true;  UseDir = $false; UseRoot = $false },
    [pscustomobject]@{ Id = 'C'; Name = 'flush(dir)      '; UseFile = $true;  UseDir = $true;  UseRoot = $false },
    [pscustomobject]@{ Id = 'D'; Name = 'flush(root)     '; UseFile = $true;  UseDir = $false; UseRoot = $true  }
  )

  $results = New-Object System.Collections.Generic.List[object]

  foreach ($arm in $arms) {
    $etl  = Join-Path $script:Root ("e21-{0}-{1}.etl" -f $arm.Id, $script:Stamp)
    $csv  = Join-Path $script:Root ("e21-{0}-{1}.csv" -f $arm.Id, $script:Stamp)
    $name = "e21trace" + $arm.Id

    Write-Host ("[{0}] {1} tracing..." -f $arm.Id, $arm.Name) -ForegroundColor Yellow
    Start-FlushTrace -Name $name -Etl $etl

    $timings = New-Object System.Collections.Generic.List[object]
    $errors  = New-Object System.Collections.Generic.List[string]

    for ($r = 0; $r -lt $Reps; $r++) {
      $path = Join-Path $workDir ("a21_{0}_r{1}.bin" -f $arm.Id, $r)
      try {
        $fh = New-PaddedFile -Path $path -Tag ("e21-{0}-r{1}" -f $arm.Id, $r)
        if ($arm.UseFile) { $timings.Add((Measure-Flush -Handle $fh -Label 'file')) | Out-Null }
        [void][E21Native]::CloseHandle($fh)

        if ($arm.UseDir) {
          $dh = Open-Directory -Path $workDir
          $timings.Add((Measure-Flush -Handle $dh -Label 'dir')) | Out-Null
          [void][E21Native]::CloseHandle($dh)
        }
        if ($arm.UseRoot) {
          $rh = Open-Directory -Path $rootDir
          $timings.Add((Measure-Flush -Handle $rh -Label 'root')) | Out-Null
          [void][E21Native]::CloseHandle($rh)
        }
      } catch {
        $errors.Add($_.Exception.Message) | Out-Null
      }
    }

    Start-Sleep -Milliseconds 800
    Stop-FlushTrace -Name $name
    $csvOut = Convert-Trace -Etl $etl -Csv $csv
    $ev     = Measure-FlushEvents -Csv $csvOut

    $avgMs = @{}
    foreach ($g in ($timings | Group-Object Label)) {
      $avgMs[$g.Name] = [math]::Round((($g.Group | Measure-Object Ms -Average).Average), 3)
    }

    $row = [pscustomobject]@{
      Arm         = $arm.Id
      Operation   = $arm.Name
      FlushEvents = $ev.FlushEvents
      SrbFlush    = $ev.SrbFlush
      FileMs      = $(if ($avgMs.ContainsKey('file')) { $avgMs['file'] } else { '' })
      DirMs       = $(if ($avgMs.ContainsKey('dir'))  { $avgMs['dir'] }  else { '' })
      RootMs      = $(if ($avgMs.ContainsKey('root')) { $avgMs['root'] } else { '' })
      Errors      = $errors.Count
      Csv         = (Split-Path -Leaf $csvOut)
      Providers   = $script:ProvidersUsed
    }
    $results.Add($row) | Out-Null

    Write-Host ("     flush-events={0}  srb-flush={1}  file={2}ms dir={3}ms root={4}ms  errors={5}" -f `
        $row.FlushEvents, $row.SrbFlush, $row.FileMs, $row.DirMs, $row.RootMs, $row.Errors) -ForegroundColor Gray
    foreach ($s in $ev.Samples) { Write-Host ("     sample: " + $s) -ForegroundColor DarkGray }
  }

  # ------------------------------------------------------------------ report
  $byId = @{}
  foreach ($r in $results) { $byId[$r.Arm] = $r }

  $bSrb = $(if ($byId.ContainsKey('B')) { $byId['B'].SrbFlush }    else { 0 })
  $cSrb = $(if ($byId.ContainsKey('C')) { $byId['C'].SrbFlush }    else { 0 })
  $dSrb = $(if ($byId.ContainsKey('D')) { $byId['D'].SrbFlush }    else { 0 })
  $bEv  = $(if ($byId.ContainsKey('B')) { $byId['B'].FlushEvents } else { 0 })
  $cEv  = $(if ($byId.ContainsKey('C')) { $byId['C'].FlushEvents } else { 0 })
  $dEv  = $(if ($byId.ContainsKey('D')) { $byId['D'].FlushEvents } else { 0 })

  if ($dSrb -gt $bSrb) {
    $metric = 'SrbFlush'
    $cDelta = $cSrb - $bSrb
    $dDelta = $dSrb - $bSrb
  } else {
    $metric = 'FlushEvents'
    $cDelta = $cEv - $bEv
    $dDelta = $dEv - $bEv
  }

  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("E21 Tier-1 report - $($script:Stamp)")
  $lines.Add(("Host   : {0}" -f $env:COMPUTERNAME))
  $lines.Add(("OS     : {0} build {1}" -f $os.Caption, [Environment]::OSVersion.Version.Build))
  $lines.Add(("PS     : {0}" -f $PSVersionTable.PSVersion))
  $lines.Add(("Volume : {0} ({1})" -f $Volume, $disk.FileSystem))
  $lines.Add(("Reps   : {0}   TraceLevel: {1}   Providers: {2}" -f $Reps, $TraceLevel, $script:ProvidersUsed))
  $lines.Add("")
  $lines.Add("arm  operation            flushEvents  srbFlush  fileMs  dirMs  rootMs  errors")
  foreach ($r in $results) {
    $lines.Add(("{0}    {1}  {2,11}  {3,8}  {4,6}  {5,5}  {6,6}  {7,6}" -f `
        $r.Arm, $r.Operation, $r.FlushEvents, $r.SrbFlush, $r.FileMs, $r.DirMs, $r.RootMs, $r.Errors))
  }
  $lines.Add("")
  $lines.Add(("DELTA vs B (flush file only), metric = {0}:" -f $metric))
  $lines.Add(("  C - B (parent directory flush) = {0}" -f $cDelta))
  $lines.Add(("  D - B (volume root flush)      = {0}   <- positive control" -f $dDelta))
  $lines.Add("")
  if ($dDelta -le 0) {
    $lines.Add("VERDICT: INCONCLUSIVE - the volume-root arm (positive control) added no")
    $lines.Add("flush events over the file-only arm, so this trace did not observe")
    $lines.Add("device-level flushes.  Check that the StorPort provider is enabled.")
  } elseif ($cDelta -le 0) {
    $lines.Add("VERDICT: REPRODUCES QEMU - a parent-directory FlushFileBuffers adds NO")
    $lines.Add("flush event on this machine, while a volume-root flush does.")
    $lines.Add("=> the parent-dir flush (leveldb 30b34e0 / SQLite winSyncDirectory)")
    $lines.Add("   does not reach the storage stack here either.")
  } else {
    $lines.Add("VERDICT: CONTRADICTS QEMU - the parent-directory flush DID add flush events")
    $lines.Add("on this machine.  Keep the CSV files: investigate before changing any")
    $lines.Add("patch wording.")
  }
  $lines.Add("")
  $lines.Add("Note: a flush reaching the storage stack is necessary but not sufficient")
  $lines.Add("for power-loss durability (the drive's own cache is a separate layer).")
  $lines.Add("Tier-2 (real power cut) is still required to prove end-to-end survival.")
  $lines.Add(("Transcript: {0}" -f (Split-Path -Leaf $script:LogPath)))

  $lines | Set-Content -Path $script:ReportPath -Encoding UTF8
  Write-Host ""
  Write-Host ("Report: {0}" -f $script:ReportPath) -ForegroundColor Cyan
  $lines | ForEach-Object { Write-Host $_ }

  if (-not $KeepEtl) {
    Get-ChildItem -Path $script:Root -Filter ("e21-*-{0}.etl" -f $script:Stamp) -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
  }
}
catch {
  $script:Failed = $true
  Write-Host ""
  Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
  if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed }
  Write-Host ("Log: " + $script:LogPath) -ForegroundColor Yellow
}
finally {
  try { Stop-Transcript | Out-Null } catch { }
  if (-not $NoPause -and -not $script:Relaunched) {
    [void](Read-Host "Press Enter to close")
  }
}
if ($script:Failed) { exit 1 }
