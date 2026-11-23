<#
  run-e33.ps1 - E33 Tier-1: NTFS $LogFile flush attribution for directory-index changes.

  Question: which handle/API makes the exact $LogFile redo/undo records of a
  test directory's $I30 index reach the storage device behind a flush barrier?

  Phases:
    0. environment + open-probe matrix (all documented stream paths x access
       masks x backup-semantics, CreateFileW and NtCreateFile).  A failed
       open is recorded as a result.
    1. fixtures: a small directory (resident $INDEX_ROOT) and a large one
       (2000 entries -> non-resident $I30:$INDEX_ALLOCATION, verified via
       fsutil queryExtents and later from the extracted MFT records).
    2. arms A-L, each in its own ETW session (logman Kernel-Disk; wpr DiskIO
       as a fallback).  The measured window contains ONLY: create / rename /
       delete + the candidate flush, repeated -Reps times.  Rename and delete
       pre-states are made durable outside the window (file flush + root
       flush, the E17 corrected protocol).
    3. after each arm: raw-volume extraction of the MFT records touched by
       the arm plus the whole $LogFile (python e33_extract.py), because
       D:\$MFT and D:\$LogFile are not openable by name even as admin.

  Requires administrator (ETW kernel provider + raw volume read).
  All artifacts land in docs/powercut/e33/e33-artifacts-<stamp>/.
#>
param(
  [string]$Volume = 'D:',
  [int]$Reps = 20,
  [int]$LargeDirEntries = 2000,
  [switch]$NoPause,
  [string[]]$Arms = @('A','B','C','D','E','F','G','H','I','J','K','L'),
  [switch]$SkipExtract
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$LogPath = Join-Path $Root ("e33-log-{0}.txt" -f $Stamp)
$ReportPath = Join-Path $Root ("e33-report-{0}.txt" -f $Stamp)
$ArtifactDir = Join-Path $Root ("e33-artifacts-{0}" -f $Stamp)
$script:ToolErrors = New-Object System.Collections.Generic.List[string]
$script:Tracer = 'none'
$script:Failed = $false

try { Start-Transcript -Path $LogPath -Force | Out-Null } catch { }

try {
  # ---------------------------------------------------------------- elevation
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if (-not $isAdmin) {
    throw "E33 requires an elevated shell (ETW kernel providers + raw volume read)."
  }

  # ------------------------------------------------------------ volume checks
  $Volume = $Volume.TrimEnd('\')
  if ($Volume -notmatch '^[A-Za-z]:$') { throw "Volume must look like D: (got '$Volume')" }
  if ($Volume -ieq $env:SystemDrive) { throw "Refusing to run on the system volume." }
  $disk = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='{0}'" -f $Volume)
  if (-not $disk) { throw "Volume $Volume not found." }
  if ($disk.FileSystem -ne 'NTFS') { throw "Volume $Volume is $($disk.FileSystem); NTFS required." }
  $wpr = Join-Path $env:SystemRoot 'System32\wpr.exe'
  & $wpr -cancel 2>&1 | Out-Null

  New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
  $etwDir = Join-Path $ArtifactDir 'etw'
  $metaDir = Join-Path $ArtifactDir 'logfile'
  New-Item -ItemType Directory -Force -Path $etwDir | Out-Null
  New-Item -ItemType Directory -Force -Path $metaDir | Out-Null

  $os = Get-CimInstance Win32_OperatingSystem
  $py = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $py) { throw "python not found on PATH" }

  Write-Host ""
  Write-Host "=== E33: NTFS `$LogFile flush attribution ===" -ForegroundColor Cyan
  Write-Host ("OS      : {0} build {1}" -f $os.Caption, [Environment]::OSVersion.Version.Build)
  Write-Host ("Volume  : {0} ({1}, {2} GB free)" -f $Volume, $disk.FileSystem,
      [math]::Round($disk.FreeSpace / 1GB, 1))
  Write-Host ("Reps    : {0} per operation per arm; large-dir entries: {1}" -f $Reps, $LargeDirEntries)
  Write-Host ("Python  : {0}" -f $py)
  Write-Host ("Artifacts: {0}" -f $ArtifactDir)
  Write-Host ""

  # ------------------------------------------------------------ P/Invoke API
  if (-not ('E33Native' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class E33Native {
  [StructLayout(LayoutKind.Sequential)]
  public struct IoStatusBlock { public IntPtr Status; public IntPtr Information; }
  [StructLayout(LayoutKind.Sequential)]
  public struct UNICODE_STRING { public ushort Length; public ushort MaximumLength;
                                 public IntPtr Buffer; }
  [StructLayout(LayoutKind.Sequential)]
  public struct OBJECT_ATTRIBUTES { public uint Length; public IntPtr RootDirectory;
                                   public IntPtr ObjectName; public uint Attributes;
                                   public IntPtr SecurityDescriptor;
                                   public IntPtr SecurityQualityOfService; }
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
  public static extern bool MoveFileExW(string src, string dst, uint flags);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern bool DeleteFileW(string name);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern uint GetFileType(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool GetFileInformationByHandle(IntPtr h,
      out BY_HANDLE_FILE_INFORMATION info);
  [DllImport("ntdll.dll")]
  public static extern int NtFlushBuffersFile(IntPtr handle, out IoStatusBlock ios);
  [DllImport("ntdll.dll")]
  public static extern int NtFlushBuffersFileEx(IntPtr handle, uint flags,
      IntPtr parms, uint parmsSize, out IoStatusBlock ios);
  [DllImport("ntdll.dll")]
  public static extern int NtCreateFile(out IntPtr handle, uint access,
      ref OBJECT_ATTRIBUTES oa, out IoStatusBlock ios, IntPtr allocSize,
      uint fileAttributes, uint share, uint disposition, uint options,
      IntPtr eaBuffer, uint eaLength);
  [DllImport("ntdll.dll")]
  public static extern uint RtlNtStatusToDosError(int status);
  [StructLayout(LayoutKind.Sequential)]
  public struct BY_HANDLE_FILE_INFORMATION {
    public uint FileAttributes; public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
    public uint VolumeSerialNumber; public uint FileSizeHigh; public uint FileSizeLow;
    public uint NumberOfLinks; public uint FileIndexHigh; public uint FileIndexLow;
  }
}
'@
  }

  $GENERIC_READ      = [uint32]2147483648   # 0x80000000
  $GENERIC_WRITE     = [uint32]1073741824   # 0x40000000
  $FILE_READ_DATA    = [uint32]1
  $FILE_WRITE_DATA   = [uint32]2
  $FILE_APPEND_DATA  = [uint32]4
  $ACCESS_DIR_RW     = $GENERIC_READ -bor $FILE_APPEND_DATA
  $ACCESS_FILE_RW    = $GENERIC_READ -bor $GENERIC_WRITE
  $ACCESS_DATA_RW    = $FILE_READ_DATA -bor $FILE_WRITE_DATA
  $SHARE_ALL         = [uint32]7
  $OPEN_EXISTING     = [uint32]3
  $CREATE_ALWAYS     = [uint32]2
  $BACKUP_SEM        = [uint32]0x02000000
  $MOVE_WRITE_THROUGH = [uint32]0x1

  function ErrOf([bool]$ok) {
    if (-not $ok) { return [Runtime.InteropServices.Marshal]::GetLastWin32Error() }
    return 0
  }

  function NT([int]$st) {
    if ($st -ge 0) { return "SUCCESS" }
    $dos = [E33Native]::RtlNtStatusToDosError($st)
    return "0x{0:X8} (dos={1})" -f ($st -band 0xFFFFFFFF), $dos
  }

  function Open-Path {
    param([string]$Path, [uint32]$Access, [uint32]$Flags, [uint32]$Share = $SHARE_ALL)
    $h = [E33Native]::CreateFileW($Path, $Access, $Share, [IntPtr]::Zero,
        $OPEN_EXISTING, $Flags, [IntPtr]::Zero)
    if ($h.ToInt64() -eq -1) {
      return @{ Ok = $false; Err = [Runtime.InteropServices.Marshal]::GetLastWin32Error(); Handle = [IntPtr]::Zero }
    }
    $info = New-Object E33Native+BY_HANDLE_FILE_INFORMATION
    $got = [E33Native]::GetFileInformationByHandle($h, [ref]$info)
    $idx = 0
    if ($got) { $idx = [uint64]$info.FileIndexHigh * 0x100000000 + $info.FileIndexLow }
    return @{ Ok = $true; Handle = $h; Err = 0; Index = $idx }
  }

  function Index-OfHandle([IntPtr]$H) {
    if ($H -eq [IntPtr]::Zero) { return 0 }
    $info = New-Object E33Native+BY_HANDLE_FILE_INFORMATION
    $got = [E33Native]::GetFileInformationByHandle($H, [ref]$info)
    if (-not $got) { return 0 }
    return [uint64]$info.FileIndexHigh * 0x100000000 + $info.FileIndexLow
  }

  function New-DirtyFile {
    param([string]$Path, [string]$Tag)
    $h = [E33Native]::CreateFileW($Path, $GENERIC_WRITE, $SHARE_ALL, [IntPtr]::Zero,
        $CREATE_ALWAYS, [uint32]0, [IntPtr]::Zero)
    if ($h.ToInt64() -eq -1) {
      throw "CreateFileW($Path) failed err=$([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    $buf = New-Object byte[] 2048
    $tagB = [Text.Encoding]::ASCII.GetBytes($Tag)
    [Array]::Copy($tagB, 0, $buf, 0, [Math]::Min($tagB.Length, 2048))
    $w = 0
    [void][E33Native]::WriteFile($h, $buf, 2048, [ref]$w, [IntPtr]::Zero)
    return $h
  }

  function Flush-Root {
    $rh = (Open-Path -Path ($Volume + '\') -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM).Handle
    if ($rh -ne [IntPtr]::Zero) {
      [void][E33Native]::FlushFileBuffers($rh)
      [void][E33Native]::CloseHandle($rh)
    }
  }

  function Make-Durable {
    param([IntPtr]$FileHandle)
    [void][E33Native]::FlushFileBuffers($FileHandle)
    Flush-Root
  }

  # ------------------------------------------------------ ETW tracer choice
  # logman with a single explicit provider (Kernel-Disk) keeps the ETL small:
  # the wpr DiskIO profile also pulls CLR rundown and balloons to ~60 MB per
  # 2-second window.  Fall back to wpr DiskIO if logman is unusable.
  $script:TracerProvider = 'Microsoft-Windows-Kernel-Disk'
  try {
    $testEtl = Join-Path $etwDir 'tracer-selftest.etl'
    & logman create trace 'e33selftest' -o $testEtl -ets -f bin `
        -p $script:TracerProvider 0xFFFFFFFF 5 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
      Start-Sleep -Milliseconds 300
      & logman stop 'e33selftest' -ets 2>&1 | Out-Null
      & logman delete 'e33selftest' 2>&1 | Out-Null
      if (Test-Path $testEtl) {
        $script:Tracer = 'logman'
        Remove-Item $testEtl -Force -ErrorAction SilentlyContinue
      }
    } else {
      & logman delete 'e33selftest' 2>&1 | Out-Null
      $script:ToolErrors.Add("logman Kernel-Disk create failed rc=$LASTEXITCODE") | Out-Null
    }
  } catch {
    $script:ToolErrors.Add("logman selftest: " + $_.Exception.Message) | Out-Null
  }
  if ($script:Tracer -eq 'none') {
    $testEtl = Join-Path $etwDir 'tracer-selftest-wpr.etl'
    & $wpr -start DiskIO -filemode 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
      Start-Sleep -Milliseconds 300
      & $wpr -stop $testEtl 2>&1 | Out-Null
      if (Test-Path $testEtl) { $script:Tracer = 'wpr'; Remove-Item $testEtl -Force -ErrorAction SilentlyContinue }
    }
  }
  Write-Host ("Tracer  : {0} ({1})" -f $script:Tracer, $script:TracerProvider)
  if ($script:Tracer -eq 'none') { throw "no usable ETW tracer" }

  function Start-ArmTrace {
    param([string]$Name, [string]$Etl)
    if ($script:Tracer -eq 'logman') {
      & logman create trace $Name -o $Etl -ets -f bin `
          -p $script:TracerProvider 0xFFFFFFFF 5 2>&1 | Out-Null
      if ($LASTEXITCODE -ne 0) { throw "logman create trace failed rc=$LASTEXITCODE" }
    } else {
      & $wpr -start DiskIO -filemode 2>&1 | Out-Null
      if ($LASTEXITCODE -ne 0) { throw "wpr start failed" }
    }
  }

  function Stop-ArmTrace {
    param([string]$Name, [string]$Etl)
    Start-Sleep -Milliseconds 250
    if ($script:Tracer -eq 'logman') {
      & logman stop $Name -ets 2>&1 | Out-Null
      Start-Sleep -Milliseconds 300
      & logman delete $Name 2>&1 | Out-Null
    } else {
      & $wpr -stop $Etl 2>&1 | Out-Null
    }
  }

  # ------------------------------------------------------------ fixtures
  $baseDir = Join-Path ($Volume + '\') 'e33'
  if (Test-Path $baseDir) {
    Get-ChildItem $baseDir -Recurse -Force -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $baseDir -Force -ErrorAction SilentlyContinue
  }
  New-Item -ItemType Directory -Force -Path $baseDir | Out-Null

  function New-Fixture {
    param([string]$Name, [bool]$Large)
    $dir = Join-Path $baseDir $Name
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    if ($Large) {
      for ($i = 0; $i -lt $LargeDirEntries; $i++) {
        $p = Join-Path $dir ("pre_{0:D5}.bin" -f $i)
        $h = New-DirtyFile -Path $p -Tag ("pre-$Name-$i")
        [void][E33Native]::CloseHandle($h)
      }
    }
    $dh = (Open-Path -Path $dir -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM).Handle
    if ($dh -eq [IntPtr]::Zero) { throw "cannot open fixture dir $dir" }
    $marker = Join-Path $dir 'arm_marker.txt'
    $mh = New-DirtyFile -Path $marker -Tag "fixture-$Name"
    Make-Durable -FileHandle $mh
    [void][E33Native]::CloseHandle($mh)
    $index = Index-OfHandle $dh
    [void][E33Native]::CloseHandle($dh)
    return @{ Path = $dir; Record = $index }
  }

  Write-Host "Building fixtures..." -ForegroundColor Yellow
  $fxSmall = New-Fixture -Name 'small' -Large $false
  $fxLarge = New-Fixture -Name 'large' -Large $true
  Write-Host ("  small: {0}  (MFT record {1})" -f $fxSmall.Path, $fxSmall.Record)
  Write-Host ("  large: {0}  (MFT record {1}, entries {2})" -f $fxLarge.Path,
      $fxLarge.Record, $LargeDirEntries)

  $extOut = & fsutil file queryExtents "${($fxLarge.Path)}:`$I30:`$INDEX_ALLOCATION" 2>&1
  $nonResident = ("$extOut" -match 'VCN:.*LCN:')
  Write-Host ("  large `$I30 non-resident probe: {0}" -f
      $(if ($nonResident) { 'extents reported' } else { 'no extents (resident?)' }))

  # ------------------------------------------------------- open-handle probe
  function Invoke-NtOpen {
    param([string]$Path, [uint32]$Access, [uint32]$Options)
    $nameBuf = [Runtime.InteropServices.Marshal]::StringToHGlobalUni($Path)
    try {
      $us = New-Object E33Native+UNICODE_STRING
      $us.Length = [uint16]($Path.Length * 2)
      $us.MaximumLength = [uint16]($Path.Length * 2 + 2)
      $us.Buffer = $nameBuf
      $usPtr = [Runtime.InteropServices.Marshal]::AllocHGlobal([Runtime.InteropServices.Marshal]::SizeOf([type]'E33Native+UNICODE_STRING'))
      [Runtime.InteropServices.Marshal]::StructureToPtr($us, $usPtr, $false)
      $oa = New-Object E33Native+OBJECT_ATTRIBUTES
      $oa.Length = [uint32][Runtime.InteropServices.Marshal]::SizeOf([type]'E33Native+OBJECT_ATTRIBUTES')
      $oa.RootDirectory = [IntPtr]::Zero
      $oa.ObjectName = $usPtr
      $oa.Attributes = 0
      $h = [IntPtr]::Zero
      $ios = New-Object E33Native+IoStatusBlock
      $st = [E33Native]::NtCreateFile([ref]$h, $Access, [ref]$oa, [ref]$ios,
          [IntPtr]::Zero, 0, $SHARE_ALL, 1, $Options, [IntPtr]::Zero, 0)
      $desc = NT $st
      if ($st -ge 0) { [void][E33Native]::CloseHandle($h) }
      [Runtime.InteropServices.Marshal]::FreeHGlobal($usPtr)
      return @{ Status = $desc; Ok = ($st -ge 0) }
    } finally {
      [Runtime.InteropServices.Marshal]::FreeHGlobal($nameBuf)
    }
  }

  $probeRows = New-Object System.Collections.Generic.List[object]
  function Probe-Open {
    param([string]$Label, [string]$Path, [uint32]$Access, [uint32]$Flags,
          [switch]$UseNt)
    if ($UseNt) {
      $r = Invoke-NtOpen -Path $Path -Access $Access -Options $Flags
      $probeRows.Add([pscustomobject]@{
        Label = $Label; Path = $Path; Api = 'NtCreateFile'
        Access = ('0x{0:X8}' -f $Access); Flags = ('0x{0:X8}' -f $Flags)
        Ok = $r.Ok; Result = $r.Status; Type = '' }) | Out-Null
      return
    }
    $r = Open-Path -Path $Path -Access $Access -Flags $Flags
    $type = ''
    if ($r.Ok) {
      $type = [E33Native]::GetFileType($r.Handle)
      [void][E33Native]::CloseHandle($r.Handle)
    }
    $probeRows.Add([pscustomobject]@{
      Label = $Label; Path = $Path; Api = 'CreateFileW'
      Access = ('0x{0:X8}' -f $Access); Flags = ('0x{0:X8}' -f $Flags)
      Ok = $r.Ok; Result = $(if ($r.Ok) { 'ok' } else { 'err=' + $r.Err }); Type = $type }) | Out-Null
  }

  foreach ($fx in @($fxSmall, $fxLarge)) {
    $d = $fx.Path
    $i30 = $d + ':$I30:$INDEX_ALLOCATION'
    $i30alt = $d + '::$INDEX_ALLOCATION'
    $i30root = $d + ':$I30'
    foreach ($mask in @(@{ N = 'read+write'; A = $ACCESS_FILE_RW },
                        @{ N = 'file_read|file_write'; A = $ACCESS_DATA_RW })) {
      foreach ($sem in @(@{ N = 'no-backup'; F = [uint32]0 },
                         @{ N = 'backup-sem'; F = $BACKUP_SEM })) {
        Probe-Open -Label ("dir/" + $mask.N + "/" + $sem.N) -Path $d `
            -Access $mask.A -Flags $sem.F
        Probe-Open -Label ("i30-allocation/" + $mask.N + "/" + $sem.N) -Path $i30 `
            -Access $mask.A -Flags $sem.F
        Probe-Open -Label ("i30-alt/" + $mask.N + "/" + $sem.N) -Path $i30alt `
            -Access $mask.A -Flags $sem.F
        Probe-Open -Label ("i30-root/" + $mask.N + "/" + $sem.N) -Path $i30root `
            -Access $mask.A -Flags $sem.F
      }
    }
    Probe-Open -Label 'i30-allocation/NtCreateFile/data-rw' -Path $i30 `
        -Access $ACCESS_DATA_RW -Flags 0 -UseNt
    Probe-Open -Label 'i30-alt/NtCreateFile/data-rw' -Path $i30alt `
        -Access $ACCESS_DATA_RW -Flags 0 -UseNt
    Probe-Open -Label 'i30-allocation/NtCreateFile/backup' -Path $i30 `
        -Access $ACCESS_DATA_RW -Flags 0x00004000 -UseNt
  }
  Probe-Open -Label 'logfile' -Path ($Volume + '\$LogFile') -Access $ACCESS_FILE_RW -Flags 0
  Probe-Open -Label 'mft' -Path ($Volume + '\$MFT') -Access $ACCESS_FILE_RW -Flags 0
  Probe-Open -Label 'volume-root' -Path ($Volume + '\') -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM
  Probe-Open -Label 'volume-device' -Path ('\\.\' + $Volume) -Access $GENERIC_READ -Flags 0
  $probeRows | Export-Csv -NoTypeInformation -Path (Join-Path $ArtifactDir 'open-probes.csv') -Encoding UTF8
  $probes = [pscustomobject]@{
    small = $fxSmall; large = $fxLarge; rows = $probeRows
  }
  $probes | ConvertTo-Json -Depth 5 |
      Set-Content -Path (Join-Path $ArtifactDir 'open-probes.json') -Encoding UTF8
  Write-Host ("Open probes: {0} rows" -f $probeRows.Count) -ForegroundColor Gray

  # ------------------------------------------------------------ arm runner
  $results = New-Object System.Collections.Generic.List[object]

  function Get-CandidateHandles {
    param([string]$Dir)
    $i30 = $Dir + ':$I30:$INDEX_ALLOCATION'
    $out = @{ Stream = [IntPtr]::Zero; Dir = [IntPtr]::Zero
              LogFile = [IntPtr]::Zero; StreamOpen = ''; LogFileOpen = '' }
    $r1 = Open-Path -Path $i30 -Access $ACCESS_FILE_RW -Flags 0
    if ($r1.Ok) { $out.Stream = $r1.Handle; $out.StreamOpen = "ok idx=$($r1.Index)" }
    else { $out.StreamOpen = "err=$($r1.Err)" }
    $rd = Open-Path -Path $Dir -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM
    if ($rd.Ok) { $out.Dir = $rd.Handle }
    $lg = Open-Path -Path ($Volume + '\$LogFile') -Access $ACCESS_FILE_RW -Flags 0
    if ($lg.Ok) { $out.LogFile = $lg.Handle; $out.LogFileOpen = 'ok' }
    else { $out.LogFileOpen = "err=$($lg.Err)" }
    return $out
  }

  function Invoke-Candidate {
    param([string]$Arm, [hashtable]$H, [IntPtr]$FileHandle)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $status = ''
    switch ($Arm) {
      'A' { $status = 'no-flush' }
      'B' { $ok = [E33Native]::FlushFileBuffers($FileHandle); $status = "win32-file ok=$ok err=$(ErrOf $ok)" }
      'C' { $ok = [E33Native]::FlushFileBuffers($H.Dir); $status = "win32-dir ok=$ok err=$(ErrOf $ok)" }
      'D' { $ios = New-Object E33Native+IoStatusBlock; $st = [E33Native]::NtFlushBuffersFile($H.Dir, [ref]$ios); $status = "nt-dir " + (NT $st) }
      'E' { $ios = New-Object E33Native+IoStatusBlock; $st = [E33Native]::NtFlushBuffersFileEx($H.Dir, 0, [IntPtr]::Zero, 0, [ref]$ios); $status = "ntex-dir0 " + (NT $st) }
      'F' { $ok = [E33Native]::FlushFileBuffers($H.Stream); $status = "win32-i30 ok=$ok err=$(ErrOf $ok)" }
      'G' { $ios = New-Object E33Native+IoStatusBlock; $st = [E33Native]::NtFlushBuffersFile($H.Stream, [ref]$ios); $status = "nt-i30 " + (NT $st) }
      'H' { $ios = New-Object E33Native+IoStatusBlock; $st = [E33Native]::NtFlushBuffersFileEx($H.Stream, 0, [IntPtr]::Zero, 0, [ref]$ios); $status = "ntex-i30-0 " + (NT $st) }
      'I' { $ios = New-Object E33Native+IoStatusBlock; $st = [E33Native]::NtFlushBuffersFileEx($H.Stream, 8, [IntPtr]::Zero, 0, [ref]$ios); $status = "ntex-i30-8 " + (NT $st) }
      'J' { $ok = [E33Native]::FlushFileBuffers($H.LogFile); $status = "win32-logfile ok=$ok err=$(ErrOf $ok)" }
      'K' {
        $kh = (Open-Path -Path ($Volume + '\') -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM).Handle
        $ok = [E33Native]::FlushFileBuffers($kh)
        $kerr = ErrOf $ok
        [void][E33Native]::CloseHandle($kh)
        $status = "win32-root ok=$ok err=$kerr"
      }
      'L' {
        $vh = (Open-Path -Path ('\\.\' + $Volume) -Access $GENERIC_READ -Flags 0).Handle
        $ok = [E33Native]::FlushFileBuffers($vh)
        $lerr = ErrOf $ok
        if ($vh -ne [IntPtr]::Zero) { [void][E33Native]::CloseHandle($vh) }
        $status = "win32-volume ok=$ok err=$lerr"
      }
      default { $status = 'unknown-arm' }
    }
    $sw.Stop()
    return @{ Status = $status; Ms = [math]::Round($sw.Elapsed.TotalMilliseconds, 3) }
  }

  function Extract-ArmMetadata {
    param([string]$Tag, [string[]]$Records)
    if ($SkipExtract) { return }
    $recList = ($Records | Where-Object { $_ -gt 0 } | Sort-Object -Unique) -join ','
    $outJson = Join-Path $metaDir ("{0}-extract.json" -f $Tag)
    $pyArgs = @((Join-Path $Root 'e33_extract.py'),
              '--volume', $Volume, '--tag', $Tag, '--out-dir', $metaDir,
              '--records', $recList)
    $out = & $py @pyArgs 2>&1
    $code = $LASTEXITCODE
    $txt = ($out | ForEach-Object { "$_" }) -join ' | '
    if ($code -ne 0) {
      $script:ToolErrors.Add("extract $Tag rc=$code : $txt") | Out-Null
      Write-Host ("  extract {0} FAILED rc={1}: {2}" -f $Tag, $code, $txt) -ForegroundColor DarkYellow
    } else {
      Write-Host ("  extract {0}: {1}" -f $Tag, $txt) -ForegroundColor Gray
    }
    return $outJson
  }

  foreach ($arm in $Arms) {
    $dir = $fxSmall.Path
    $dirRecord = $fxSmall.Record
    if ($arm -in @('F','G','H','I')) { $dir = $fxLarge.Path; $dirRecord = $fxLarge.Record }

    # ---- setup OUTSIDE the measured window ----
    $renameSrc = Join-Path $dir ("e33_{0}_ren_src.bin" -f $arm)
    $deleteTgt = Join-Path $dir ("e33_{0}_del_tgt.bin" -f $arm)
    $h1 = New-DirtyFile -Path $renameSrc -Tag "ren-$arm"
    Make-Durable -FileHandle $h1
    $renSrcRec = Index-OfHandle $h1
    $h2 = New-DirtyFile -Path $deleteTgt -Tag "del-$arm"
    Make-Durable -FileHandle $h2
    $delTgtRec = Index-OfHandle $h2
    # Keep write-capable handles for arm B on the rename/delete operations.
    $renH = (Open-Path -Path $renameSrc -Access $ACCESS_FILE_RW -Flags 0).Handle
    $delH = (Open-Path -Path $deleteTgt -Access $ACCESS_FILE_RW -Flags 0).Handle
    [void][E33Native]::CloseHandle($h1)
    [void][E33Native]::CloseHandle($h2)

    $handles = Get-CandidateHandles -Dir $dir
    $etl = Join-Path $etwDir ("arm-{0}.etl" -f $arm)
    $recNumbers = New-Object System.Collections.Generic.List[long]
    $recNumbers.Add([long]$dirRecord) | Out-Null
    $recNumbers.Add([long]$renSrcRec) | Out-Null
    $recNumbers.Add([long]$delTgtRec) | Out-Null

    Start-ArmTrace -Name ("e33" + $arm) -Etl $etl

    # ---- measured window: namespace ops + candidate flush only ----
    $opRows = New-Object System.Collections.Generic.List[object]
    for ($r = 0; $r -lt $Reps; $r++) {
      # create
      $createPath = Join-Path $dir ("e33_{0}_create_{1:D4}.bin" -f $arm, $r)
      $swOp = [Diagnostics.Stopwatch]::StartNew()
      $fh = New-DirtyFile -Path $createPath -Tag ("create-$arm-$r")
      $swOp.Stop()
      $createMs = [math]::Round($swOp.Elapsed.TotalMilliseconds, 3)
      $creRec = Index-OfHandle $fh
      $cand = Invoke-Candidate -Arm $arm -H $handles -FileHandle $fh
      $opRows.Add([pscustomobject]@{
        Rep = $r; Op = 'create'; Target = $createPath; Record = $creRec
        OpMs = $createMs; FlushMs = $cand.Ms; FlushStatus = $cand.Status })
      if ($creRec -gt 0) { $recNumbers.Add([long]$creRec) | Out-Null }
      [void][E33Native]::CloseHandle($fh)

      # rename (same-directory, write-through flag off: the candidate is what we test)
      $renameDst = Join-Path $dir ("e33_{0}_ren_dst_{1:D4}.bin" -f $arm, $r)
      $swOp = [Diagnostics.Stopwatch]::StartNew()
      $ok = [E33Native]::MoveFileExW($renameSrc, $renameDst, [uint32]0)
      $renameErr = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
      $swOp.Stop()
      $renameMs = [math]::Round($swOp.Elapsed.TotalMilliseconds, 3)
      $cand = Invoke-Candidate -Arm $arm -H $handles -FileHandle $renH
      $opRows.Add([pscustomobject]@{
        Rep = $r; Op = 'rename'; Target = $renameDst; Record = $renSrcRec
        OpMs = $renameMs; FlushMs = $cand.Ms; FlushStatus = $cand.Status
        OpOk = $ok; OpErr = $renameErr })
      [void][E33Native]::MoveFileExW($renameDst, $renameSrc, [uint32]0)

      # delete
      $swOp = [Diagnostics.Stopwatch]::StartNew()
      $ok = [E33Native]::DeleteFileW($deleteTgt)
      $delErr = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
      $swOp.Stop()
      $deleteMs = [math]::Round($swOp.Elapsed.TotalMilliseconds, 3)
      $cand = Invoke-Candidate -Arm $arm -H $handles -FileHandle $delH
      $opRows.Add([pscustomobject]@{
        Rep = $r; Op = 'delete'; Target = $deleteTgt; Record = $delTgtRec
        OpMs = $deleteMs; FlushMs = $cand.Ms; FlushStatus = $cand.Status
        OpOk = $ok; OpErr = $delErr })
      # recreate the deleted target for the next rep
      $h3 = New-DirtyFile -Path $deleteTgt -Tag "del-re-$arm-$r"
      [void][E33Native]::CloseHandle($h3)
    }

    Stop-ArmTrace -Name ("e33" + $arm) -Etl $etl

    foreach ($hh in @($handles.Stream, $handles.Dir, $handles.LogFile, $renH, $delH)) {
      if ($hh -ne [IntPtr]::Zero) { [void][E33Native]::CloseHandle($hh) }
    }

    $csv = Join-Path $ArtifactDir ("arm-{0}-ops.csv" -f $arm)
    $opRows | Export-Csv -NoTypeInformation -Path $csv -Encoding UTF8

    if (-not $SkipExtract) {
      Extract-ArmMetadata -Tag ("arm-{0}" -f $arm) -Records $recNumbers
    }

    $flushOk = @($opRows | Where-Object { $_.FlushStatus -match 'ok=True|SUCCESS' }).Count
    $avgMs = 0
    if ($opRows.Count -gt 0) {
      $avgMs = [math]::Round((($opRows | Measure-Object FlushMs -Average).Average), 3)
    }
    $results.Add([pscustomobject]@{
      Arm = $arm; Dir = (Split-Path -Leaf $dir)
      StreamOpen = $handles.StreamOpen; LogFileOpen = $handles.LogFileOpen
      OpsTotal = $opRows.Count; FlushOk = $flushOk; AvgFlushMs = $avgMs
      Etl = (Split-Path -Leaf $etl)
    })
    Write-Host ("[arm {0}] dir={1} flush_ok={2}/{3} avg_flush={4}ms stream={5} logfile={6}" -f `
        $arm, (Split-Path -Leaf $dir), $flushOk, $opRows.Count, $avgMs,
        $handles.StreamOpen, $handles.LogFileOpen) -ForegroundColor Yellow
    Get-ChildItem $dir -Filter ("e33_{0}_*" -f $arm) -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
  }

  # ---------------------------------------------------------------- report
  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("E33 Tier-1 report - $Stamp")
  $lines.Add(("OS      : {0} build {1}" -f $os.Caption, [Environment]::OSVersion.Version.Build))
  $lines.Add(("Volume  : {0} ({1})" -f $Volume, $disk.FileSystem))
  $lines.Add(("Reps    : {0} per op per arm; large-dir entries: {1}" -f $Reps, $LargeDirEntries))
  $lines.Add(("Tracer  : {0} ({1})" -f $script:Tracer, $script:TracerProvider))
  $lines.Add(("Large-dir I30 non-resident probe: {0}" -f $nonResident))
  $lines.Add("")
  $lines.Add("arm  dir   stream  logfile  ops  flush_ok  avg_flush_ms")
  foreach ($r in $results) {
    $lines.Add(("{0,-4} {1,-6} {2,-7} {3,-8} {4,4} {5,9} {6,12}" -f `
        $r.Arm, $r.Dir, $r.StreamOpen, $r.LogFileOpen, $r.OpsTotal,
        $r.FlushOk, $r.AvgFlushMs))
  }
  if ($script:ToolErrors.Count -gt 0) {
    $lines.Add("")
    $lines.Add("TOOL ERRORS:")
    foreach ($e in $script:ToolErrors) { $lines.Add("  " + $e) }
  }
  $lines | Set-Content -Path $ReportPath -Encoding UTF8
  Write-Host ""
  Write-Host ("Report   : {0}" -f $ReportPath) -ForegroundColor Cyan
  Write-Host ("Artifacts: {0}" -f $ArtifactDir) -ForegroundColor Cyan
}
catch {
  $script:Failed = $true
  Write-Host ""
  Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
  if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed }
  Write-Host ("Log: " + $LogPath) -ForegroundColor Yellow
}
finally {
  try { Stop-Transcript | Out-Null } catch { }
  try { & (Join-Path $env:SystemRoot 'System32\wpr.exe') -cancel 2>&1 | Out-Null } catch { }
  try { & logman stop 'e33A' -ets 2>&1 | Out-Null; & logman delete 'e33A' 2>&1 | Out-Null } catch { }
  if (-not $NoPause) { [void](Read-Host "Press Enter to close") }
}
if ($script:Failed) { exit 1 }
exit 0
