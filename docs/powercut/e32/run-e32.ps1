#>
  run-e32.ps1 - E32 Tier-1: NtFlushBuffersFileEx(FLUSH_AND_PURGE=8) vs flag 0 / Win32 flush.

  Question: on build 26100+ (Server 2025 / Windows 11 24H2+), does
  NtFlushBuffersFileEx(..., FLUSH_AND_PURGE, ...) actually push metadata/
  volume dirty state to the device — i.e. is it a *stronger* flush than the
  plain file/dir flush the whole Windows-dir-flush family converged on?

  Method: wpr DiskIO (same tracer as E21/E27), cumulative arms, N reps each.
  Per-call deltas over the no-flush baseline:
    A  baseline: create+write file, no flush
    B  Win32 FlushFileBuffers(file)
    C  Ex(file, FLAGS=0)
    D  Ex(file, FLAGS=8)          <- FLUSH_AND_PURGE on a file handle
    E  Ex(dir,  FLAGS=0)
    F  Ex(dir,  FLAGS=8)          <- FLUSH_AND_PURGE on a directory handle
    G  Ex(root, FLAGS=8)          <- on the volume ROOT dir (append-data open)
    H  Ex(root, FLAGS=0)
  Deltas vs A tell whether flag 8 reaches further than flag 0 (volume-wide
  flush) or behaves like the plain flush.

  Dir/root handles are opened READ|FILE_APPEND_DATA (the non-admin shape);
  GH runners are administrators so this also covers the ordinary case.

  Safety: volume is D: (non-system); the probe only creates scratch files.

  Usage: pwsh -NoProfile -ExecutionPolicy Bypass -File .\run-e32.ps1 -Reps 100 -NoPause
#>
param(
  [string]$Volume = 'D:',
  [int]$Reps = 100,
  [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$LogPath = Join-Path $Root ("e32-log-{0}.txt" -f $Stamp)
$ReportPath = Join-Path $Root ("e32-report-{0}.txt" -f $Stamp)
$script:Tracer = 'none'
$script:ToolErrors = New-Object System.Collections.Generic.List[string]

try { Start-Transcript -Path $LogPath -Force | Out-Null } catch { }

if (-not ('E32Native' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class E32Native {
  [StructLayout(LayoutKind.Sequential)]
  public struct IoStatusBlock { public IntPtr Status; public IntPtr Information; }
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr tmpl);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool WriteFile(IntPtr h, byte[] buf, int n, out int written, IntPtr ov);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool FlushFileBuffers(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool CloseHandle(IntPtr h);
  [DllImport("ntdll.dll")]
  public static extern int NtFlushBuffersFileEx(IntPtr handle, uint flags, IntPtr parms, uint parmsSize, out IoStatusBlock ios);
  [DllImport("ntdll.dll")]
  public static extern int NtFlushBuffersFile(IntPtr handle, out IoStatusBlock ios);
  [DllImport("ntdll.dll")]
  public static extern uint RtlNtStatusToDosError(int status);
}
'@
}

$SHARE_ALL = [uint32]7
$OPEN_EXISTING = [uint32]3
$CREATE_ALWAYS = [uint32]2
$BACKUP_SEM = [uint32]0x02000000
$ACCESS_DIR_APPEND = [uint32]2147483652   # GENERIC_READ | FILE_APPEND_DATA
$ACCESS_FILE_RW = [uint32]2147483649      # GENERIC_READ | GENERIC_WRITE

function NT-Str([int]$st) {
  $dos = [E32Native]::RtlNtStatusToDosError($st)
  return ('0x{0:X8} dos={1}' -f ($st -band 0xFFFFFFFF), $dos)
}

function Write-Dirty([string]$path) {
  $h = [E32Native]::CreateFileW($path, $ACCESS_FILE_RW, $SHARE_ALL, [IntPtr]::Zero, $CREATE_ALWAYS, [uint32]0, [IntPtr]::Zero)
  if ($h.ToInt64() -eq -1) { throw "create failed err=$([Runtime.InteropServices.Marshal]::GetLastWin32Error()) $path" }
  $buf = New-Object byte[] 2048
  $w = 0
  [void][E32Native]::WriteFile($h, $buf, 2048, [ref]$w, [IntPtr]::Zero)
  return $h
}

function Ex-Flush([IntPtr]$h, [uint32]$flags, [IntPtr]$buf) {
  $ios = New-Object E32Native+IoStatusBlock
  return [E32Native]::NtFlushBuffersFileEx($h, $flags, $buf, [uint32]0, [ref]$ios)
}

$Volume = $Volume.TrimEnd('\')
if ($Volume -notmatch '^[A-Za-z]:$') { throw "Volume must look like D: (got '$Volume')" }
if ($Volume -ieq $env:SystemDrive) { throw "Refusing to run on the system volume." }
$disk = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='{0}'" -f $Volume)
if (-not $disk) { throw "Volume $Volume not found." }
if ($disk.FileSystem -ne 'NTFS') { throw "Volume $Volume is $($disk.FileSystem); NTFS required." }

$WorkDir = Join-Path ($Volume + '\') 'e32'
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
Get-ChildItem $WorkDir -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# tracer: wpr DiskIO (same as E21/E27)
$wpr = Join-Path $env:SystemRoot 'System32\wpr.exe'
if (Test-Path $wpr) {
  $test = Join-Path $Root ("e32-selftest-{0}.etl" -f $Stamp)
  & $wpr -start DiskIO -filemode 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) {
    & $wpr -stop $test 2>&1 | Out-Null
    if (Test-Path $test) { $script:Tracer = 'wpr'; Remove-Item $test -Force -ErrorAction SilentlyContinue }
  }
}

function Count-Csv([string]$csv) {
  $w = 0; $f = 0
  if (-not $csv -or -not (Test-Path $csv)) { return @{ W = 0; F = 0 } }
  foreach ($line in [IO.File]::ReadLines($csv)) {
    if ($line -match '(?i)^[^,]*,\s*Write\s*,') { $w++ }
    if ($line -match '(?i)FlushBuffers|FlushInit') { $f++ }
  }
  return @{ W = $w; F = $f }
}

function Run-Arm([string]$arm) {
  $dir = Join-Path $WorkDir $arm
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $etl = Join-Path $Root ("e32-{0}-{1}.etl" -f $arm, $Stamp)
  $csv = Join-Path $Root ("e32-{0}-{1}.csv" -f $arm, $Stamp)
  $handles = New-Object System.Collections.Generic.List[object]
  $status = @{}
  $errors = New-Object System.Collections.Generic.List[string]

  try {
    $rootH = [E32Native]::CreateFileW(($Volume + '\'), $ACCESS_DIR_APPEND, $SHARE_ALL, [IntPtr]::Zero, $OPEN_EXISTING, $BACKUP_SEM, [IntPtr]::Zero)
    $dirH = [E32Native]::CreateFileW($dir, $ACCESS_DIR_APPEND, $SHARE_ALL, [IntPtr]::Zero, $OPEN_EXISTING, $BACKUP_SEM, [IntPtr]::Zero)
  } catch { $errors.Add($_.Exception.Message) | Out-Null }

  # setup OUTSIDE the trace window: create + write the files (dirty the cache)
  for ($r = 0; $r -lt $Reps; $r++) {
    try { $handles.Add((Write-Dirty (Join-Path $dir ("t_{0}_{1}.bin" -f $arm, $r)))) | Out-Null }
    catch { $errors.Add($_.Exception.Message) | Out-Null }
  }

  if ($script:Tracer -eq 'wpr') { & $wpr -start DiskIO -filemode 2>&1 | Out-Null }

  # measured window: flush calls only (E21 discipline)
  for ($r = 0; $r -lt $Reps; $r++) {
    try {
      switch ($arm) {
        'A' { }
        'B' { [void][E32Native]::FlushFileBuffers($handles[$r]) }
        'C' { $status["rep$r"] = NT-Str (Ex-Flush $handles[$r] ([uint32]0) [IntPtr]::Zero) }
        'D' { $status["rep$r"] = NT-Str (Ex-Flush $handles[$r] ([uint32]8) [IntPtr]::Zero) }
        'E' { $status["rep$r"] = NT-Str (Ex-Flush $dirH ([uint32]0) [IntPtr]::Zero) }
        'F' { $status["rep$r"] = NT-Str (Ex-Flush $dirH ([uint32]8) [IntPtr]::Zero) }
        'G' { $status["rep$r"] = NT-Str (Ex-Flush $rootH ([uint32]8) [IntPtr]::Zero) }
        'H' { $status["rep$r"] = NT-Str (Ex-Flush $rootH ([uint32]0) [IntPtr]::Zero) }
      }
    } catch { $errors.Add($_.Exception.Message) | Out-Null }
  }

  if ($script:Tracer -eq 'wpr') {
    Start-Sleep -Milliseconds 300
    & $wpr -stop $etl 2>&1 | Out-Null
    if (Test-Path $etl) { & tracerpt $etl -o $csv -of CSV -y 2>&1 | Out-Null }
  }

  foreach ($h in $handles) { [void][E32Native]::CloseHandle($h) }
  if ($dirH -and $dirH.ToInt64() -ne -1) { [void][E32Native]::CloseHandle($dirH) }
  if ($rootH -and $rootH.ToInt64() -ne -1) { [void][E32Native]::CloseHandle($rootH) }

  $cnt = Count-Csv $csv
  $firstStatus = ''
  if ($status.Count -gt 0) { $firstStatus = $status['rep0'] }
  return [pscustomobject]@{
    Arm = $arm; Writes = $cnt.W; Flushes = $cnt.F
    Status0 = $firstStatus; Errors = $errors.Count
    ErrSample = ($errors | Select-Object -First 2) -join ' | '
  }
}

$results = @()
foreach ($arm in 'A','B','C','D','E','F','G','H') {
  Write-Host ("[E32] arm {0} ..." -f $arm)
  $results += Run-Arm $arm
}

$base = $results | Where-Object Arm -eq 'A'
$lines = @()
$lines += "E32 - NtFlushBuffersFileEx(FLUSH_AND_PURGE) Tier-1 (build $([Environment]::OSVersion.Version))"
$lines += "volume=$Volume fs=NTFS reps=$Reps tracer=$($script:Tracer)"
$lines += ""
$lines += ("{0,-4} {1,8} {2,8} {3,9} {4,9}  {5}" -f 'arm','writes','flushes','dWrites','dFlush','status(rep0)')
foreach ($r in $results) {
  $dw = $r.Writes - $base.Writes
  $df = $r.Flushes - $base.Flushes
  $lines += ("{0,-4} {1,8} {2,8} {3,9} {4,9}  {5}" -f $r.Arm, $r.Writes, $r.Flushes, $dw, $df, $r.Status0)
}
$lines += ""
$lines += "errors: " + (($results | ForEach-Object { "$($_.Arm)=$($_.Errors) $($_.ErrSample)" }) -join '; ')
$lines | ForEach-Object { Write-Host $_ }
$lines | Set-Content -Path $ReportPath -Encoding UTF8
Get-ChildItem $WorkDir -Recurse -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ("report: {0}" -f $ReportPath)
try { Stop-Transcript | Out-Null } catch { }
if (-not $NoPause) { Read-Host 'Press Enter to close' | Out-Null }