<#
  run-tier1.ps1 (v3) - E21 Tier-1: device-level flush evidence on real hardware.

  Answers: does FlushFileBuffers on a PARENT DIRECTORY emit a flush to the
  storage stack, compared with FlushFileBuffers on the VOLUME ROOT (the
  effective call in the QEMU rigs)?

  Four cumulative arms:

    A  create+write, no flush                 (baseline / journal noise)
    B  A + FlushFileBuffers(file)             (upstream behaviour)
    C  B + FlushFileBuffers(parent dir)       (leveldb 30b34e0 / SQLite shape)
    D  B + FlushFileBuffers(volume root)      (positive control for the tracer)

  Tracing: probes which ETW providers this build actually registers
  (logman query providers), then tries logman, then wpr.  Every tool error
  is captured into the log/report instead of being swallowed.

  Always available signal: the wall-clock duration of each flush call.
  A directory flush that is a device-level no-op is typically one to two
  orders of magnitude faster than a file flush that really waits for a
  barrier.  The report prints that ratio even when tracing fails.

  Works on Windows PowerShell 5.1 and 7.

  Usage:
    # already-elevated shell, single window:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\run-tier1.ps1 -Volume D: -NoElevate
    # let the script elevate itself:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\run-tier1.ps1 -Volume D:
    # unattended (CI):
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\run-tier1.ps1 -Volume D: -NoPause
#>
param(
  [string]$Volume = 'D:',
  [int]$Reps = 25,
  [int]$TraceLevel = 5,
  [switch]$KeepEtl,
  [switch]$NoElevate,
  [switch]$NoPause,
  [switch]$Elevated,
  [switch]$Diagnose
)

$ErrorActionPreference = 'Stop'
$script:Root       = Split-Path -Parent $PSCommandPath
$script:Stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
$script:LogPath    = Join-Path $script:Root ("tier1-log-{0}.txt" -f $script:Stamp)
$script:ReportPath = Join-Path $script:Root ("tier1-report-{0}.txt" -f $script:Stamp)
$script:Failed     = $false
$script:Relaunched = $false
$script:ProvidersUsed = ''
$script:LogmanForm    = ''
$script:Tracer        = 'none'
$script:ToolErrors    = New-Object System.Collections.Generic.List[string]
$script:ProviderAvail = @{}

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
    Write-Host ("That window keeps its own log: {0}" -f $script:LogPath) -ForegroundColor Yellow
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
  if ($disk.FileSystem -ne 'NTFS') { throw "Volume $Volume is $($disk.FileSystem); this test requires NTFS" }
  if ($disk.FreeSpace -lt 64MB) {
    throw ("Volume $Volume has only {0} MB free" -f [math]::Round($disk.FreeSpace / 1MB))
  }

  # physical disk behind the target volume - used to filter DiskIo events
  $diskNumber = -1
  try {
    $letter = $Volume.Substring(0, 1)
    $part   = Get-Partition -DriveLetter $letter -ErrorAction Stop
    $diskNumber = [int]$part.DiskNumber
  } catch { }

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

  # ------------------------------------------------- tracer selection (v3)
  # 1) find out which providers this build registers
  Write-Host "Probing ETW providers..." -ForegroundColor Yellow
  $provOut = @()
  try { $provOut = & logman query providers 2>&1 } catch { }
  foreach ($n in @('Microsoft-Windows-Kernel-Disk','Microsoft-Windows-StorPort','Microsoft-Windows-Kernel-File')) {
    $hit = $false
    foreach ($line in $provOut) { if ("$line" -like "*$n*") { $hit = $true; break } }
    $script:ProviderAvail[$n] = $hit
    Write-Host ("  {0,-38} {1}" -f $n, $(if ($hit) { 'registered' } else { 'NOT FOUND' })) -ForegroundColor Gray
  }

  $available = @()
  foreach ($n in @('Microsoft-Windows-Kernel-Disk','Microsoft-Windows-StorPort','Microsoft-Windows-Kernel-File')) {
    if ($script:ProviderAvail[$n]) { $available += $n }
  }

  # ------------------------------------------------------------ diagnose mode
  if ($Diagnose) {
    Write-Host ""
    Write-Host "=== DIAGNOSE: logman / wpr capability battery ===" -ForegroundColor Cyan
    $d = $script:Root
    $p1 = 'Microsoft-Windows-Kernel-File'
    $battery = @(
      @{ Label = '1 create -ets (no provider)';      Args = @('create','trace','e21d1','-o',"$d\d1.etl",'-ets') },
      @{ Label = '2 create -ets -p plain';           Args = @('create','trace','e21d2','-o',"$d\d2.etl",'-ets','-p',$p1) },
      @{ Label = '3 create -ets -p flags level';     Args = @('create','trace','e21d3','-o',"$d\d3.etl",'-ets','-p',$p1,'0xFFFFFFFF','5') },
      @{ Label = '4 create (stored) -p plain';       Args = @('create','trace','e21d4','-o',"$d\d4.etl",'-p',$p1) },
      @{ Label = '5 create (stored) -p flags level'; Args = @('create','trace','e21d5','-o',"$d\d5.etl",'-p',$p1,'0xFFFFFFFF','5') },
      @{ Label = '6 create -ets -f bin -p plain';    Args = @('create','trace','e21d6','-o',"$d\d6.etl",'-f','bin','-ets','-p',$p1) }
    )
    foreach ($b in $battery) {
      $out = & logman @($b.Args) 2>&1
      $rc  = $LASTEXITCODE
      $txt = (($out | ForEach-Object { "$_" }) -join ' | ')
      if ($txt.Length -gt 300) { $txt = $txt.Substring(0, 300) }
      Write-Host ("  [{0}] rc={1}  {2}" -f $b.Label, $rc, $txt) -ForegroundColor Gray
      Write-Host ("       cmd: logman " + ($b.Args -join ' ')) -ForegroundColor DarkGray
      if ($rc -eq 0) {
        # try to start it if it is a stored collector (forms 4/5)
        if ($b.Label -match 'stored') {
          $out2 = & logman start ($b.Args[2]) 2>&1
          Write-Host ("       start rc={0} {1}" -f $LASTEXITCODE, (($out2 | ForEach-Object { "$_" }) -join ' | ')) -ForegroundColor DarkGray
          & logman stop ($b.Args[2]) 2>&1 | Out-Null
        } else {
          & logman stop ($b.Args[2]) -ets 2>&1 | Out-Null
        }
        & logman delete ($b.Args[2]) 2>&1 | Out-Null
        Write-Host ("       etl exists: {0}" -f (Test-Path $b.Args[4])) -ForegroundColor DarkGray
        Remove-Item $b.Args[4] -Force -ErrorAction SilentlyContinue
      } else {
        & logman delete ($b.Args[2]) 2>&1 | Out-Null
      }
    }

    $wpr = Join-Path $env:SystemRoot 'System32\wpr.exe'
    Write-Host ("  wpr present: {0} ({1})" -f (Test-Path $wpr), $wpr) -ForegroundColor Gray
    if (Test-Path $wpr) {
      & $wpr -cancel 2>&1 | Out-Null
      $out = & $wpr -start DiskIO -filemode 2>&1
      Write-Host ("  wpr start rc={0} {1}" -f $LASTEXITCODE, (($out | ForEach-Object { "$_" }) -join ' | ')) -ForegroundColor Gray
      Start-Sleep -Milliseconds 400
      $out = & $wpr -stop "$d\dw.etl" 2>&1
      Write-Host ("  wpr stop  rc={0} etl={1} {2}" -f $LASTEXITCODE, (Test-Path "$d\dw.etl"), (($out | ForEach-Object { "$_" }) -join ' | ')) -ForegroundColor Gray
      Remove-Item "$d\dw.etl" -Force -ErrorAction SilentlyContinue
    }
    Write-Host ""
    Write-Host ("Diagnose log: {0}" -f $script:LogPath) -ForegroundColor Cyan
    return
  }

  $wprPath = Join-Path $env:SystemRoot 'System32\wpr.exe'

  # 2) wpr first: its DiskIO profile emits DiskIo/FlushBuffers events
  if (Test-Path $wprPath) {
    $testEtl = Join-Path $script:Root ("e21-selftest-{0}.etl" -f $script:Stamp)
    Remove-Item $testEtl -Force -ErrorAction SilentlyContinue
    $out = & $wprPath -start DiskIO -filemode 2>&1
    $rc  = $LASTEXITCODE
    if ($rc -eq 0) {
      Start-Sleep -Milliseconds 400
      & $wprPath -stop $testEtl 2>&1 | Out-Null
      if (Test-Path $testEtl) {
        $script:Tracer = 'wpr'
        $script:ProvidersUsed = 'wpr DiskIO profile'
        Write-Host "  tracer: wpr OK (DiskIO profile)" -ForegroundColor Green
      } else {
        $script:ToolErrors.Add('wpr produced no .etl') | Out-Null
        Write-Host "  tracer: wpr produced no .etl" -ForegroundColor DarkYellow
      }
    } else {
      $msg = ("wpr rc={0} : {1}" -f $rc, (($out | ForEach-Object { "$_" }) -join ' | '))
      $script:ToolErrors.Add($msg) | Out-Null
      Write-Host ("  tracer: wpr FAILED - " + $msg) -ForegroundColor DarkYellow
    }
    Remove-Item $testEtl -Force -ErrorAction SilentlyContinue
  }

  # 3) fall back to logman (one provider per session)
  #    This logman build rejects more than one -p per session:
  #    "Argument 'p' has been defined too many times." (E_INVALIDARG)
  #    So: ONE provider per session, the decisive one (StorPort) first.
  if ($script:Tracer -eq 'none' -and $available.Count -gt 0) {
    $order  = @('Microsoft-Windows-StorPort','Microsoft-Windows-Kernel-Disk','Microsoft-Windows-Kernel-File')
    $picked = $false
    foreach ($prov in $order) {
      if ($picked) { break }
      if (-not $script:ProviderAvail[$prov]) { continue }
      foreach ($form in @('flags+level','plain')) {
        if ($picked) { break }
        $testEtl = Join-Path $script:Root ("e21-selftest-{0}.etl" -f $script:Stamp)
        Remove-Item $testEtl -Force -ErrorAction SilentlyContinue
        $larg = @('create','trace','e21selftest','-o',$testEtl,'-ets')
        if ($form -eq 'flags+level') { $larg += @('-p', $prov, '0xFFFFFFFF', '5') }
        else                          { $larg += @('-p', $prov) }
        $out = & logman @larg 2>&1
        $rc  = $LASTEXITCODE
        & logman stop   e21selftest -ets 2>&1 | Out-Null
        & logman delete e21selftest      2>&1 | Out-Null
        Remove-Item $testEtl -Force -ErrorAction SilentlyContinue
        if ($rc -eq 0) {
          $script:Tracer        = 'logman'
          $script:LogmanForm    = $form
          $script:ProvidersUsed = $prov
          $picked = $true
          Write-Host ("  tracer: logman OK (provider={0}; form={1})" -f $prov, $form) -ForegroundColor Green
        } else {
          $hex = ('0x{0:X8}' -f ([int64]$rc -band [int64]0xFFFFFFFF))
          $msg = ("logman[{0}/{1}] rc={2} {3} : {4}" -f $prov, $form, $rc, $hex, (($out | ForEach-Object { "$_" }) -join ' | '))
          $script:ToolErrors.Add($msg) | Out-Null
          Write-Host ("  tracer: " + $msg) -ForegroundColor DarkYellow
        }
      }
    }
  } else {
    $script:ToolErrors.Add('no candidate ETW provider is registered on this build') | Out-Null
    Write-Host "  tracer: no candidate provider registered" -ForegroundColor DarkYellow
  }

  if ($script:Tracer -eq 'none') {
    Write-Host "  => no tracer available; falling back to call-duration evidence only." -ForegroundColor DarkYellow
  }

  function Start-FlushTrace {
    param([string]$Name, [string]$Etl)
    if ($script:Tracer -eq 'logman') {
      & logman stop   $Name -ets 2>&1 | Out-Null
      & logman delete $Name     2>&1 | Out-Null
      $larg = @('create','trace',$Name,'-o',$Etl,'-ets')
      if ($script:LogmanForm -eq 'flags+level') {
        $larg += @('-p', $script:ProvidersUsed, '0xFFFFFFFF', '5')
      } else {
        $larg += @('-p', $script:ProvidersUsed)
      }
      & logman @larg 2>&1 | Out-Null
      if ($LASTEXITCODE -ne 0) { throw "logman create trace failed (rc=$LASTEXITCODE)" }
    } elseif ($script:Tracer -eq 'wpr') {
      $out = & (Join-Path $env:SystemRoot 'System32\wpr.exe') -start DiskIO -filemode 2>&1
      if ($LASTEXITCODE -ne 0) { throw ("wpr start failed: " + (($out | ForEach-Object { "$_" }) -join ' | ')) }
    }
  }

  function Stop-FlushTrace {
    param([string]$Name, [string]$Etl)
    if ($script:Tracer -eq 'logman') {
      & logman stop $Name -ets 2>&1 | Out-Null
      Start-Sleep -Milliseconds 500
      & logman delete $Name 2>&1 | Out-Null
    } elseif ($script:Tracer -eq 'wpr') {
      Start-Sleep -Milliseconds 300
      & (Join-Path $env:SystemRoot 'System32\wpr.exe') -stop $Etl 2>&1 | Out-Null
    }
  }

  function Convert-Trace {
    param([string]$Etl, [string]$Csv)
    if (-not (Test-Path $Etl)) { return $null }
    & tracerpt $Etl -o $Csv -of CSV -y 2>&1 | Out-Null
    if (-not (Test-Path $Csv)) { return $null }
    return $Csv
  }

  function Measure-FlushEvents {
    param([string]$Csv, [int]$DiskNumber = -1)
    $fbAll = 0; $fbDisk = 0; $fiDisk = 0; $wrAll = 0; $rdAll = 0
    $samples = New-Object System.Collections.Generic.List[string]
    if (-not $Csv) {
      return [pscustomobject]@{ FbAll = 0; FbDisk = 0; FiDisk = 0; WrAll = 0; RdAll = 0; Samples = @(); Header = '' }
    }
    $diskIdx = -1
    $first   = $true
    $headerLine = ''
    foreach ($line in [IO.File]::ReadLines($Csv)) {
      if ($first) {
        $first = $false
        $hdr = $line -split ','
        for ($i = 0; $i -lt $hdr.Count; $i++) {
          $headerLine = $line.Trim()
        if ($hdr[$i].Trim().Trim('"') -ieq 'DiskNumber') { $diskIdx = $i }
        }
        continue
      }
      if ($line -match '(?i)^[^,]*,\s*Write\s*,') { $wrAll++ }
      if ($line -match '(?i)^[^,]*,\s*Read\s*,')  { $rdAll++ }
      if ($line -notmatch '(?i)FlushBuffers|FlushInit') { continue }
      $isFb = $line -match '(?i)FlushBuffers'
      $isFi = $line -match '(?i)FlushInit'
      $dn   = -1
      if ($diskIdx -ge 0) {
        $cols = $line -split ','
        if ($cols.Count -gt $diskIdx) {
          $v = $cols[$diskIdx].Trim().Trim('"')
          [void][int]::TryParse($v, [ref]$dn)
        }
      }
      if ($isFb) {
        $fbAll++
        if ($DiskNumber -ge 0 -and $dn -eq $DiskNumber) { $fbDisk++ }
      }
      if ($isFi -and $DiskNumber -ge 0 -and $dn -eq $DiskNumber) { $fiDisk++ }
      if ($samples.Count -lt 2 -and $isFb) {
        $samples.Add($line.Substring(0, [Math]::Min(180, $line.Length))) | Out-Null
      }
    }
    return [pscustomobject]@{ FbAll = $fbAll; FbDisk = $fbDisk; FiDisk = $fiDisk; WrAll = $wrAll; RdAll = $rdAll; Samples = $samples.ToArray(); Header = $headerLine }
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

    # ---- setup OUTSIDE the measured window: create files, open handles ----
    $handles = New-Object System.Collections.Generic.List[object]
    $errors  = New-Object System.Collections.Generic.List[string]
    for ($r = 0; $r -lt $Reps; $r++) {
      $path = Join-Path $workDir ("a21_{0}_r{1}.bin" -f $arm.Id, $r)
      try {
        $fh = New-PaddedFile -Path $path -Tag ("e21-{0}-r{1}" -f $arm.Id, $r)
        $handles.Add($fh) | Out-Null
      } catch { $errors.Add($_.Exception.Message) | Out-Null }
    }
    $dirH = $null; $rootH = $null
    if ($arm.UseDir)  { try { $dirH  = Open-Directory -Path $workDir } catch { $errors.Add($_.Exception.Message) | Out-Null } }
    if ($arm.UseRoot) { try { $rootH = Open-Directory -Path $rootDir } catch { $errors.Add($_.Exception.Message) | Out-Null } }

    # ---- measured window: ONLY the flush calls ----
    Write-Host ("[{0}] {1} tracer={2} window=flushes-only reps={3}" -f $arm.Id, $arm.Name, $script:Tracer, $Reps) -ForegroundColor Yellow
    try { Start-FlushTrace -Name $name -Etl $etl }
    catch { $script:ToolErrors.Add("arm $($arm.Id) start: " + $_.Exception.Message) | Out-Null }

    $timings = New-Object System.Collections.Generic.List[object]
    for ($r = 0; $r -lt $Reps; $r++) {
      if ($arm.UseFile -and $r -lt $handles.Count) {
        $timings.Add((Measure-Flush -Handle $handles[$r] -Label 'file')) | Out-Null
      }
      if ($dirH)  { $timings.Add((Measure-Flush -Handle $dirH  -Label 'dir'))  | Out-Null }
      if ($rootH) { $timings.Add((Measure-Flush -Handle $rootH -Label 'root')) | Out-Null }
    }

    try { Stop-FlushTrace -Name $name -Etl $etl }
    catch { $script:ToolErrors.Add("arm $($arm.Id) stop: " + $_.Exception.Message) | Out-Null }

    foreach ($h in $handles) { [void][E21Native]::CloseHandle($h) }
    if ($dirH)  { [void][E21Native]::CloseHandle($dirH) }
    if ($rootH) { [void][E21Native]::CloseHandle($rootH) }

    $csvOut = Convert-Trace -Etl $etl -Csv $csv
    $ev     = Measure-FlushEvents -Csv $csvOut -DiskNumber $diskNumber

    $avgMs = @{}
    foreach ($g in ($timings | Group-Object Label)) {
      $avgMs[$g.Name] = [math]::Round((($g.Group | Measure-Object Ms -Average).Average), 3)
    }

    $row = [pscustomobject]@{
      Arm         = $arm.Id
      Operation   = $arm.Name
      FlushEvents = $ev.FbDisk
      SrbFlush    = $ev.FiDisk
      FbAll       = $ev.FbAll
      WrAll       = $ev.WrAll
      RdAll       = $ev.RdAll
      FileMs      = $(if ($avgMs.ContainsKey('file')) { $avgMs['file'] } else { '' })
      DirMs       = $(if ($avgMs.ContainsKey('dir'))  { $avgMs['dir'] }  else { '' })
      RootMs      = $(if ($avgMs.ContainsKey('root')) { $avgMs['root'] } else { '' })
      Errors      = $errors.Count
      Csv         = (Split-Path -Leaf $csvOut)
    }
    $results.Add($row) | Out-Null

    Write-Host ("     allDisks: FlushBuffers={0} FlushInit={1} Write={2} Read={3} | file={4}ms dir={5}ms root={6}ms | errors={7}" -f `
        $row.FbAll, $row.SrbFlush, $row.WrAll, $row.RdAll, $row.FileMs, $row.DirMs, $row.RootMs, $row.Errors) -ForegroundColor Gray
    foreach ($s2 in $ev.Samples) { Write-Host ("     sample: " + $s2) -ForegroundColor DarkGray }
    if ($ev.Header) { Write-Host ("     csv-header: " + $ev.Header) -ForegroundColor DarkGray }
  }

  # ------------------------------------------------------------------ report
  $byId = @{}
  foreach ($r in $results) { $byId[$r.Arm] = $r }

  $bSrb = $(if ($byId.ContainsKey('B')) { $byId['B'].SrbFlush }    else { 0 })
  $cSrb = $(if ($byId.ContainsKey('C')) { $byId['C'].SrbFlush }    else { 0 })
  $dSrb = $(if ($byId.ContainsKey('D')) { $byId['D'].SrbFlush }    else { 0 })
  $bEv  = $(if ($byId.ContainsKey('B')) { $byId['B'].FbAll } else { 0 })
  $cEv  = $(if ($byId.ContainsKey('C')) { $byId['C'].FbAll } else { 0 })
  $dEv  = $(if ($byId.ContainsKey('D')) { $byId['D'].FbAll } else { 0 })
  $bWr  = $(if ($byId.ContainsKey('B')) { $byId['B'].WrAll } else { 0 })
  $cWr  = $(if ($byId.ContainsKey('C')) { $byId['C'].WrAll } else { 0 })
  $dWr  = $(if ($byId.ContainsKey('D')) { $byId['D'].WrAll } else { 0 })

  # FlushBuffers events from the DiskIo provider ARE device-level: they are
  # flush IRPs that reached the disk class driver.  Counting only our own
  # physical disk keeps background flushes out.
  $metric = 'FlushBuffers@allDisks'
  $cDelta = $cEv - $bEv
  $dDelta = $dEv - $bEv
  $level  = 'DEVICE'

  # timing-based evidence (always available, independent of the tracer)
  $timingNote = ''
  $ratioNum = $null
  if (($fMs -ne '') -and ($dMs -ne '') -and ([double]$fMs -gt 0)) {
    $ratioNum = [double]$dMs / [double]$fMs
    if ($ratioNum -lt 0.25) {
      $timingNote = ("dir flush returned {0}x faster than the file flush - consistent with a no-op (no barrier wait)" -f [math]::Round(1 / $ratioNum, 1))
    } elseif ($ratioNum -gt 2) {
      $timingNote = "dir flush is slower than the file flush - it did real work"
    } else {
      $timingNote = "dir flush timing is comparable to the file flush - ambiguous"
    }
  }

  # timing ratio (works even with no tracer at all)
  $fMs = $byId['B'].FileMs; $dMs = $byId['C'].DirMs; $rMs = $byId['D'].RootMs
  $ratio = ''
  if (($fMs -ne '') -and ($dMs -ne '') -and ([double]$fMs -gt 0)) {
    $ratio = [math]::Round(([double]$dMs / [double]$fMs), 3)
  }

  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("E21 Tier-1 report - $($script:Stamp)")
  $lines.Add(("Host    : {0}" -f $env:COMPUTERNAME))
  $lines.Add(("OS      : {0} build {1}" -f $os.Caption, [Environment]::OSVersion.Version.Build))
  $lines.Add(("PS      : {0}" -f $PSVersionTable.PSVersion))
  $lines.Add(("Volume  : {0} ({1})" -f $Volume, $disk.FileSystem))
  $lines.Add(("Reps    : {0}   Tracer: {1}   Providers: {2}" -f $Reps, $script:Tracer, $script:ProvidersUsed))
  $lines.Add(("Prov avail: " + (($script:ProviderAvail.Keys | ForEach-Object { "$_=" + $script:ProviderAvail[$_] }) -join ' ')))
  $lines.Add("")
  $lines.Add("arm  operation            FlushBuf  FlushInit  Write  Read   fileMs  dirMs  rootMs  errors")
  foreach ($r in $results) {
    $lines.Add(("{0}    {1}  {2,8}  {3,9}  {4,5}  {5,4}  {6,6}  {7,5}  {8,6}  {9,6}" -f `
        $r.Arm, $r.Operation, $r.FbAll, $r.SrbFlush, $r.WrAll, $r.RdAll, $r.FileMs, $r.DirMs, $r.RootMs, $r.Errors))
  }
  $lines.Add("")
  $lines.Add(("TIMING: file flush {0} ms, dir flush {1} ms, root flush {2} ms, dir/file ratio {3}" -f $fMs, $dMs, $rMs, $ratio))
  $lines.Add(("        " + $timingNote))
  $lines.Add("")
  $lines.Add(("DELTA vs B (flush file only), metric = {0}:" -f $metric))
  $lines.Add(("  C - B (parent directory flush) = {0}" -f $cDelta))
  $lines.Add(("  D - B (volume root flush)      = {0}   <- positive control" -f $dDelta))
  $lines.Add(("  writes: B={0} C={1} D={2}   (C-B={3}, D-B={4})" -f $bWr, $cWr, $dWr, ($cWr - $bWr), ($dWr - $bWr)))
  $lines.Add("")
  if ($level -eq 'FILE' -and $bEv -eq 0 -and $cEv -eq 0 -and $dEv -eq 0) {
    $lines.Add("VERDICT: FILE-LEVEL ONLY - no StorPort FLUSH SRB was observed, so the")
    $lines.Add("event counts above are file-level (the call happened), NOT device-level.")
    $lines.Add("Use the TIMING evidence:")
    $lines.Add("  " + $timingNote)
    $lines.Add("For a device-level answer, run with a tracer that exposes StorPort SRBs")
    $lines.Add("(logman -p Microsoft-Windows-StorPort alone) and look for srbFlush > 0.")
  } elseif ($script:Tracer -eq 'none') {
    $lines.Add("VERDICT: NO TRACER - ETW could not be started on this build, so there is")
    $lines.Add("no device-level evidence.  Use the TIMING line above as a weak hint only:")
    $lines.Add("a dir/file ratio well below ~0.2 suggests the directory flush returned")
    $lines.Add("without waiting for a barrier.")
  } elseif ($dDelta -le 0) {
    $lines.Add("VERDICT: INCONCLUSIVE - the volume-root arm (positive control) added no")
    $lines.Add("flush events over the file-only arm, so this trace did not observe")
    $lines.Add("device-level flushes.  Check the CSV files and the provider list above.")
  } elseif ($cDelta -le 0) {
    $lines.Add("VERDICT: REPRODUCES QEMU - a parent-directory FlushFileBuffers adds NO")
    $lines.Add("flush event on this machine, while a volume-root flush does.")
    $lines.Add("=> the parent-dir flush (leveldb 30b34e0 / SQLite winSyncDirectory)")
    $lines.Add("   does not reach the storage stack here either.")
  } else {
    $lines.Add("VERDICT: CONTRADICTS QEMU - the parent-directory flush DID add flush events")
    $lines.Add("on this machine.  Keep the CSV files and investigate before changing any")
    $lines.Add("patch wording.")
  }
  $lines.Add("")
  if ($script:ToolErrors.Count -gt 0) {
    $lines.Add("TOOL ERRORS:")
    foreach ($e in $script:ToolErrors) { $lines.Add("  " + $e) }
    $lines.Add("")
  }
  $lines.Add("Note: a flush reaching the storage stack is necessary but not sufficient")
  $lines.Add("for power-loss durability (the drive's own cache is a separate layer).")
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
