<#
  bench-sqlite-commit.ps1 - simulate the SQLite rollback-journal commit
  sequence on a real NTFS volume and measure the per-commit cost of:
    vanilla   no dir flush, no root flush          (upstream SQLite)
    dir       + FlushFileBuffers(parent dir)       (+ the E17 patch)
    root      + FlushFileBuffers(volume root)      (+ the E21 addition)

  Each "commit" = create journal, write+fsync, create/modify db, write+fsync,
  delete journal, then the variant-specific flushes.  2048-byte padded writes
  (non-resident) so each fsync actually hits the device.

  Usage (no admin needed for data volumes):
    pwsh -File .\bench-sqlite-commit.ps1 -Volume D: -Transactions 200
#>
param(
  [string]$Volume = 'D:',
  [int]$Transactions = 200,
  [int]$Warmup = 20
)

$ErrorActionPreference = 'Stop'
$script:Root = Split-Path -Parent $PSCommandPath
$workDir = Join-Path $Volume '\e23bench'

if (-not (Test-Path $workDir)) {
  New-Item -ItemType Directory -Force -Path $workDir | Out-Null
}
# clean previous run
Get-ChildItem -Path $workDir -Filter 'bench_*' -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- P/Invoke
if (-not ('E23Native' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class E23Native {
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern IntPtr CreateFileW(string name, uint access, uint share,
      IntPtr sa, uint disp, uint flags, IntPtr tmpl);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool WriteFile(IntPtr h, byte[] buf, int n, out int written, IntPtr ov);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool FlushFileBuffers(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern bool DeleteFileW(string name);
}
'@
}

$GENERIC_WRITE = 0x40000000
$CREATE_ALWAYS = 2
$OPEN_ALWAYS   = 4

function New-File {
  param([string]$Path)
  $h = [E23Native]::CreateFileW($Path, $GENERIC_WRITE, 0, [IntPtr]::Zero, $CREATE_ALWAYS, 0, [IntPtr]::Zero)
  if ($h.ToInt64() -eq -1) { throw "CreateFileW($Path) failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
  return $h
}

function Write-Fsync {
  param([IntPtr]$Handle, [string]$Tag)
  $buf = [Text.Encoding]::ASCII.GetBytes(('P' * 2048))
  $tb = [Text.Encoding]::ASCII.GetBytes($Tag)
  [Array]::Copy($tb, 0, $buf, 0, [Math]::Min($tb.Length, 2048))
  $w = 0
  if (-not [E23Native]::WriteFile($Handle, $buf, 2048, [ref]$w, [IntPtr]::Zero)) {
    throw "WriteFile failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
  }
  if (-not [E23Native]::FlushFileBuffers($Handle)) {
    throw "FlushFileBuffers failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
  }
}

function Open-DirHandle {
  param([string]$Path)
  $h = [E23Native]::CreateFileW($Path, $GENERIC_WRITE, 7, [IntPtr]::Zero, 3, 0x02000000, [IntPtr]::Zero)
  if ($h.ToInt64() -eq -1) { throw "CreateFileW($Path) failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
  return $h
}

# ------------------------------------------------------------ commit sim
# One commit = the rollback-journal sequence SQLite performs per transaction:
#   1. create journal file, write 2048B, fsync(journal)
#   2. create/modify db file, write 2048B, fsync(db)
#   3. delete journal file
#   4. variant-specific: + FlushFileBuffers(parent dir) and/or + FlushFileBuffers(volume root)
function Do-Commit {
  param([string]$Dir, [string]$Root, [string]$Variant, [int]$N)

  $journal = Join-Path $Dir ("jr_{0}_{1}" -f $Variant, $N)
  $db      = Join-Path $Dir ("db_{0}_{1}" -f $Variant, $N)

  # 1. journal: create + write + fsync
  $jh = New-File -Path $journal
  Write-Fsync -Handle $jh -Tag ("jr-{0}-{1}" -f $Variant, $N)
  [void][E23Native]::CloseHandle($jh)

  # 2. db: create + write + fsync
  $dh = New-File -Path $db
  Write-Fsync -Handle $dh -Tag ("db-{0}-{1}" -f $Variant, $N)
  [void][E23Native]::CloseHandle($dh)

  # 3. delete journal (SQLite does this after the db fsync)
  [void][E23Native]::DeleteFileW($journal)

  # 4. variant-specific flush
  if ($Variant -ne 'vanilla') {
    $dh2 = Open-DirHandle -Path $Dir
    [void][E23Native]::FlushFileBuffers($dh2)
    [void][E23Native]::CloseHandle($dh2)
  }
  if ($Variant -eq 'root') {
    $rh = Open-DirHandle -Path $Root
    [void][E23Native]::FlushFileBuffers($rh)
    [void][E23Native]::CloseHandle($rh)
  }

  # clean up the db file for the next iteration
  [void][E23Native]::DeleteFileW($db)
}

# ------------------------------------------------------------------- run
Write-Host ""
Write-Host "=== E23: SQLite commit cost benchmark ===" -ForegroundColor Cyan
Write-Host ("Host        : {0}" -f $env:COMPUTERNAME)
Write-Host ("OS          : {0} build {1}" -f (Get-CimInstance Win32_OperatingSystem).Caption, [Environment]::OSVersion.Version.Build)
Write-Host ("Volume      : {0}" -f $Volume)
Write-Host ("Transactions: {0} per variant (+{1} warmup)" -f $Transactions, $Warmup)
Write-Host ""

$rootDir = $Volume + '\'
$results = @()

foreach ($variant in @('vanilla','dir','root')) {
  # warmup (not measured)
  for ($w = 0; $w -lt $Warmup; $w++) {
    Do-Commit -Dir $workDir -Root $rootDir -Variant $variant -N $w
  }

  # measured
  $sw = [Diagnostics.Stopwatch]::StartNew()
  for ($t = 0; $t -lt $Transactions; $t++) {
    Do-Commit -Dir $workDir -Root $rootDir -Variant $variant -N $t
  }
  $sw.Stop()

  $totalMs = $sw.Elapsed.TotalMilliseconds
  $perCommit = [math]::Round($totalMs / $Transactions, 3)
  $tps = [math]::Round($Transactions / ($totalMs / 1000), 1)
  $results += [pscustomobject]@{
    Variant   = $variant
    Total     = [math]::Round($totalMs, 1)
    PerCommit = $perCommit
    TPS       = $tps
  }
  Write-Host ("  {0,-10} {1,8} ms total   {2,7} ms/commit   {3,8} TPS" -f `
      $variant, [math]::Round($totalMs,1), $perCommit, $tps) -ForegroundColor Gray
}

# ------------------------------------------------------------------ report
$vanilla = $results | Where-Object { $_.Variant -eq 'vanilla' }
$dir     = $results | Where-Object { $_.Variant -eq 'dir' }
$root    = $results | Where-Object { $_.Variant -eq 'root' }

Write-Host ""
Write-Host "=== Deltas ===" -ForegroundColor Cyan
Write-Host ("  dir flush (the patch)      : +{0} ms/commit" -f [math]::Round($dir.PerCommit - $vanilla.PerCommit, 3))
Write-Host ("  root flush (the addition)  : +{0} ms/commit" -f [math]::Round($root.PerCommit - $vanilla.PerCommit, 3))
Write-Host ("  root vs dir (root's cost)  : +{0} ms/commit" -f [math]::Round($root.PerCommit - $dir.PerCommit, 3))
Write-Host ""
Write-Host "Note: these measure the flush costs on this volume.  Real SQLite adds"
Write-Host "parsing/B-tree/locking overhead on top, so absolute numbers differ but"
Write-Host "the DELTA from the dir flush and the root flush is what matters."

# clean up
Get-ChildItem -Path $workDir -Filter 'bench_*' -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
