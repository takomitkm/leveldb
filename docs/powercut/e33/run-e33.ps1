<#
  run-e33.ps1 - E33 Tier-1: NTFS $LogFile flush attribution for directory-index changes.

  Question: which handle/API makes the exact $LogFile redo/undo records of a
  test directory's $I30 index reach the storage device behind a flush barrier?

  Arms (per the E33 plan; each arm = one wpr DiskIO ETW session):
    A  no flush                (negative control)
    B  FlushFileBuffers(file)  (data-flush control, E21 shape)
    C  FlushFileBuffers(parent dir)  (E17/E21 baseline)
    D  NtFlushBuffersFile(parent dir) (native)
    E  NtFlushBuffersFileEx(parent dir, flags=0)
    F  FlushFileBuffers on an explicit $I30:$INDEX_ALLOCATION stream handle  <- primary
    G  NtFlushBuffersFile on the $I30 stream handle
    H  NtFlushBuffersFileEx($I30 stream, flags=0)
    I  NtFlushBuffersFileEx($I30 stream, flags=8)  (exploratory, 24H2+)
    J  FlushFileBuffers on a direct $LogFile handle (if openable)
    K  FlushFileBuffers(volume root)  (positive control, admin CI)
    L  FlushFileBuffers(volume device \\.\X:)  (documented volume flush)

  Three namespace operations run inside each measured window, each against its
  own pre-built directory fixture:
    create  : CreateFileW(unique) + WriteFile(2048B) -> candidate flush
    rename  : pre-created durable source -> MoveFileExW(dst) -> candidate flush
    delete  : pre-created durable target -> DeleteFileW -> candidate flush

  After the window the rig extracts raw $MFT and $LogFile images (fsutil
  FSUTIL ROHINT / raw read path via VHD-less direct read where permitted; on
  the runner D:\$LogFile can be read with a plain CreateFileW as admin).

  Requires: admin (ETW kernel providers), NTFS data volume.
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

try { Start-Transcript -Path $LogPath -Force | Out-Null } catch { }

try {
  # ---------------------------------------------------------------- elevation
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if (-not $isAdmin) {
    throw "E33 requires an elevated shell (ETW kernel providers + metadata read)."
  }

  # ------------------------------------------------------------ volume checks
  $Volume = $Volume.TrimEnd('\')
  if ($Volume -notmatch '^[A-Za-z]:$') { throw "Volume must look like D: (got '$Volume')" }
  if ($Volume -ieq $env:SystemDrive) { throw "Refusing to run on the system volume." }
  $disk = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='{0}'" -f $Volume)
  if (-not $disk) { throw "Volume $Volume not found." }
  if ($disk.FileSystem -ne 'NTFS') { throw "Volume $Volume is $($disk.FileSystem); NTFS required." }
  if ($disk.FreeSpace -lt 1GB) { throw "Volume $Volume needs >= 1 GB free." }
  $wpr = Join-Path $env:SystemRoot 'System32\wpr.exe'
  if (-not (Test-Path $wpr)) { throw "wpr.exe not found." }
  & $wpr -cancel 2>&1 | Out-Null

  New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
  $etwDir = Join-Path $ArtifactDir 'etw'
  $metaDir = Join-Path $ArtifactDir 'logfile'
  New-Item -ItemType Directory -Force -Path $etwDir | Out-Null
  New-Item -ItemType Directory -Force -Path $metaDir | Out-Null

  $os = Get-CimInstance Win32_OperatingSystem
  Write-Host ""
  Write-Host "=== E33: NTFS `$LogFile flush attribution ===" -ForegroundColor Cyan
  Write-Host ("OS      : {0} build {1}" -f $os.Caption, [Environment]::OSVersion.Version.Build)
  Write-Host ("Volume  : {0} ({1}, {2} GB free)" -f $Volume, $disk.FileSystem,
      [math]::Round($disk.FreeSpace / 1GB, 1))
  Write-Host ("Reps    : {0} per operation per arm; large-dir entries: {1}" -f $Reps, $LargeDirEntries)
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
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool DeleteFileW(string name);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern bool GetFileInformationByHandle(IntPtr h,
      out BY_HANDLE_FILE_INFORMATION info);
  [DllImport("ntdll.dll")]
  public static extern int NtFlushBuffersFile(IntPtr handle, out IoStatusBlock ios);
  [DllImport("ntdll.dll")]
  public static extern int NtFlushBuffersFileEx(IntPtr handle, uint flags,
      IntPtr parms, uint parmsSize, out IoStatusBlock ios);
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

  $GENERIC_READ     = [uint32]2147483648   # 0x80000000
  $GENERIC_WRITE    = [uint32]1073741824   # 0x40000000
  $FILE_APPEND_DATA = [uint32]0x00000004
  $ACCESS_DIR_RW    = $GENERIC_READ -bor $FILE_APPEND_DATA   # non-admin dir shape
  $ACCESS_FILE_RW   = $GENERIC_READ -bor $GENERIC_WRITE
  $SHARE_ALL        = [uint32]7
  $OPEN_EXISTING    = [uint32]3
  $CREATE_ALWAYS    = [uint32]2
  $BACKUP_SEM       = [uint32]0x02000000
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
    return @{
      Ok = $true; Handle = $h; Err = 0
      Index = if ($got) { [uint64]$info.FileIndexHigh * 0x100000000 + $info.FileIndexLow } else { 0 }
      Attrs = if ($got) { $info.FileAttributes } else { 0 }
    }
  }

  function New-DirtyFile {
    param([string]$Path, [string]$Tag)
    $h = [E33Native]::CreateFileW($Path, $GENERIC_WRITE, $SHARE_ALL, [IntPtr]::Zero,
        $CREATE_ALWAYS, [uint32]0, [IntPtr]::Zero)
    if ($h.ToInt64() -eq -1) {
      throw "CreateFileW($Path) failed err=$( [Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    $buf = New-Object byte[] 2048
    $tagB = [Text.Encoding]::ASCII.GetBytes($Tag)
    [Array]::Copy($tagB, 0, $buf, 0, [Math]::Min($tagB.Length, 2048))
    $w = 0
    [void][E33Native]::WriteFile($h, $buf, 2048, [ref]$w, [IntPtr]::Zero)
    return $h
  }

  function Make-Durable {
    param([IntPtr]$FileHandle, [IntPtr]$DirHandle)
    [void][E33Native]::FlushFileBuffers($FileHandle)
    if ($DirHandle -ne [IntPtr]::Zero) {
      [void][E33Native]::FlushFileBuffers($DirHandle)
      $rootH = (Open-Path -Path ($Volume + '\') -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM).Handle
      if ($rootH -ne [IntPtr]::Zero) { [void][E33Native]::FlushFileBuffers($rootH); [void][E33Native]::CloseHandle($rootH) }  # the E17 root flush
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
    # Durabilize the fixture itself (E17 corrected protocol).
    $dh = (Open-Path -Path $dir -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM).Handle
    if ($dh -eq [IntPtr]::Zero) { throw "cannot open fixture dir $dir" }
    $marker = Join-Path $dir 'arm_marker.txt'
    $mh = New-DirtyFile -Path $marker -Tag "fixture-$Name"
    Make-Durable -FileHandle $mh -DirHandle $dh
    [void][E33Native]::CloseHandle($mh)
    [void][E33Native]::CloseHandle($dh)
    return $dir
  }

  Write-Host "Building fixtures..." -ForegroundColor Yellow
  $fixtureSmall = New-Fixture -Name 'small' -Large $false
  $fixtureLarge = New-Fixture -Name 'large' -Large $true
  Write-Host ("  small: {0}" -f $fixtureSmall)
  Write-Host ("  large: {0}  (entries: {1})" -f $fixtureLarge, $LargeDirEntries)

  # Verify the large fixture's $I30 is actually non-resident.
  $nonResident = $false
  try {
    $out = & fsutil file queryfileid "$fixtureLarge" 2>&1
  } catch { }
  # fsutil layout check via dir /r is unreliable; use the definitive route:
  # read the MFT record of the directory from the extracted $MFT later. For
  # the in-run check use the file attribute query on the index allocation via
  # fsutil usn read-records? Keep it simple: size of index allocation can be
  # probed with fsutil file queryExtents on the stream path (Server 2019+).
  $extOut = & fsutil file queryExtents "${fixtureLarge}:`$I30:`$INDEX_ALLOCATION" 2>&1
  if ("$extOut" -match 'VCN:.*LCN:') { $nonResident = $true }
  if ($nonResident) {
    Write-Host "  large `$I30 non-resident probe: extents reported" -ForegroundColor Gray
  } else {
    Write-Host "  large `$I30 non-resident probe: no extents (fsutil said: $extOut)" -ForegroundColor Gray
  }

  # ------------------------------------------------------------ arm runner
  $results = New-Object System.Collections.Generic.List[object]

  function Get-CandidateHandles {
    param([string]$Dir, [string]$Arm)
    $out = @{ StreamPath1 = "$Dir`:`$I30:`$INDEX_ALLOCATION"
              StreamPath2 = "$Dir`:```$INDEX_ALLOCATION"
              Stream = [IntPtr]::Zero; Stream2 = [IntPtr]::Zero
              Dir = [IntPtr]::Zero; LogFile = [IntPtr]::Zero
              StreamOpen1 = ''; StreamOpen2 = ''; LogFileOpen = '' }
    $r1 = Open-Path -Path $out.StreamPath1 -Access $ACCESS_FILE_RW -Flags 0
    if ($r1.Ok) { $out.Stream = $r1.Handle; $out.StreamOpen1 = "ok idx=$($r1.Index)" }
    else { $out.StreamOpen1 = "err=$($r1.Err)" }
    $r2 = Open-Path -Path $out.StreamPath2 -Access $ACCESS_FILE_RW -Flags 0
    if ($r2.Ok) { $out.Stream2 = $r2.Handle; $out.StreamOpen2 = "ok idx=$($r2.Index)" }
    else { $out.StreamOpen2 = "err=$($r2.Err)" }
    $rd = Open-Path -Path $Dir -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM
    if ($rd.Ok) { $out.Dir = $rd.Handle }
    $lg = Open-Path -Path "$Volume\`$LogFile" -Access $ACCESS_FILE_RW -Flags 0
    if ($lg.Ok) { $out.LogFile = $lg.Handle; $out.LogFileOpen = "ok" }
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
      'K' { $kh = (Open-Path -Path ($Volume + '\') -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM).Handle; $ok = [E33Native]::FlushFileBuffers($kh); $kerr = $(if (-not $ok) { [Runtime.InteropServices.Marshal]::GetLastWin32Error() } else { 0 }); [void][E33Native]::CloseHandle($kh); $status = "win32-root ok=$ok err=$kerr" }
      'L' { $dev = "\\.\$Volume"; $ok = [E33Native]::FlushFileBuffers((Open-Path -Path $dev -Access $GENERIC_READ -Flags 0).Handle); $status = "win32-volume ok=$ok err=$(ErrOf $ok)" }
      default { $status = 'unknown-arm' }
    }
    $sw.Stop()
    return @{ Status = $status; Ms = [math]::Round($sw.Elapsed.TotalMilliseconds, 3) }
  }

  # Extract raw metadata copies (best effort; admin on the runner can read them).
  function Extract-Metadata {
    param([string]$Tag)
    try {
      $mfth = [E33Native]::CreateFileW("$Volume\`$MFT", $GENERIC_READ, 3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)
      if ($mfth.ToInt64() -ne -1) {
        $fs = [E33Native]::GetFileInformationByHandle
        $dst = Join-Path $metaDir ("{0}-MFT.bin" -f $Tag)
        # Stream-copy via FileStream on the raw handle.
        $safe = New-Object Microsoft.Win32.SafeHandles.SafeFileHandle($mfth, $true)
        $src = New-Object IO.FileStream($safe, [IO.FileAccess]::Read, 1MB)
        $out = [IO.File]::Create($dst)
        $buf = New-Object byte[] (4MB)
        while (($n = $src.Read($buf, 0, $buf.Length)) -gt 0) { $out.Write($buf, 0, $n) }
        $out.Close(); $src.Close()
        Write-Host ("  extracted {0} ({1:N0} bytes)" -f (Split-Path -Leaf $dst), (Get-Item $dst).Length) -ForegroundColor Gray
      } else {
        Write-Host ("  `$MFT open failed err={0}" -f [Runtime.InteropServices.Marshal]::GetLastWin32Error()) -ForegroundColor DarkYellow
        $script:ToolErrors.Add("MFT open failed for $Tag") | Out-Null
      }
    } catch {
      $script:ToolErrors.Add("MFT extract: " + $_.Exception.Message) | Out-Null
    }
    try {
      $lfh = [E33Native]::CreateFileW("$Volume\`$LogFile", $GENERIC_READ, 3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)
      if ($lfh.ToInt64() -ne -1) {
        $dst = Join-Path $metaDir ("{0}-LogFile.bin" -f $Tag)
        $safe = New-Object Microsoft.Win32.SafeHandles.SafeFileHandle($lfh, $true)
        $src = New-Object IO.FileStream($safe, [IO.FileAccess]::Read, 1MB)
        $out = [IO.File]::Create($dst)
        $buf = New-Object byte[] (4MB)
        while (($n = $src.Read($buf, 0, $buf.Length)) -gt 0) { $out.Write($buf, 0, $n) }
        $out.Close(); $src.Close()
        Write-Host ("  extracted {0} ({1:N0} bytes)" -f (Split-Path -Leaf $dst), (Get-Item $dst).Length) -ForegroundColor Gray
      } else {
        Write-Host ("  `$LogFile open failed err={0}" -f [Runtime.InteropServices.Marshal]::GetLastWin32Error()) -ForegroundColor DarkYellow
        $script:ToolErrors.Add("LogFile open failed for $Tag") | Out-Null
      }
    } catch {
      $script:ToolErrors.Add("LogFile extract: " + $_.Exception.Message) | Out-Null
    }
  }

  foreach ($arm in $Arms) {
    # Fixtures: small dir hosts create/rename/delete trials in this run.
    $dir = $fixtureSmall
    if ($arm -in @('F','G','H','I')) { $dir = $fixtureLarge }

    # ---- setup OUTSIDE the measured window ----
    # Pre-create durable sources for rename and delete.
    $renameSrc = Join-Path $dir ("e33_{0}_ren_src.bin" -f $arm)
    $deleteTgt = Join-Path $dir ("e33_{0}_del_tgt.bin" -f $arm)
    $dirH = (Open-Path -Path $dir -Access $ACCESS_DIR_RW -Flags $BACKUP_SEM).Handle
    $h1 = New-DirtyFile -Path $renameSrc -Tag "ren-$arm"
    Make-Durable -FileHandle $h1 -DirHandle $dirH
    [void][E33Native]::CloseHandle($h1)
    $h2 = New-DirtyFile -Path $deleteTgt -Tag "del-$arm"
    Make-Durable -FileHandle $h2 -DirHandle $dirH
    [void][E33Native]::CloseHandle($h2)
    [void][E33Native]::CloseHandle($dirH)

    $handles = Get-CandidateHandles -Dir $dir -Arm $arm
    $etl = Join-Path $etwDir ("arm-{0}.etl" -f $arm)
    & $wpr -start DiskIO -filemode 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "wpr start failed for arm $arm" }

    # ---- measured window: namespace ops + candidate flush only ----
    $opRows = New-Object System.Collections.Generic.List[object]
    for ($r = 0; $r -lt $Reps; $r++) {
      # create
      $createPath = Join-Path $dir ("e33_{0}_create_{1:D4}.bin" -f $arm, $r)
      $swOp = [Diagnostics.Stopwatch]::StartNew()
      $fh = New-DirtyFile -Path $createPath -Tag ("create-$arm-$r")
      $swOp.Stop()
      $createMs = [math]::Round($swOp.Elapsed.TotalMilliseconds, 3)
      $cand = Invoke-Candidate -Arm $arm -H $handles -FileHandle $fh
      $opRows.Add([pscustomobject]@{
        Rep = $r; Op = 'create'; Target = $createPath
        OpMs = $createMs; FlushMs = $cand.Ms; FlushStatus = $cand.Status })
      [void][E33Native]::CloseHandle($fh)

      # rename (same-directory)
      $renameDst = Join-Path $dir ("e33_{0}_ren_dst_{1:D4}.bin" -f $arm, $r)
      $swOp = [Diagnostics.Stopwatch]::StartNew()
      $ok = [E33Native]::MoveFileExW($renameSrc, $renameDst, $MOVE_WRITE_THROUGH)
      $renameErr = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
      $swOp.Stop()
      $renameMs = [math]::Round($swOp.Elapsed.TotalMilliseconds, 3)
      $cand = Invoke-Candidate -Arm $arm -H $handles -FileHandle ([IntPtr]::Zero)
      $opRows.Add([pscustomobject]@{
        Rep = $r; Op = 'rename'; Target = $renameDst
        OpMs = $renameMs; FlushMs = $cand.Ms; FlushStatus = $cand.Status
        OpOk = $ok; OpErr = $renameErr })
      # move back for the next rep
      [void][E33Native]::MoveFileExW($renameDst, $renameSrc, 0)

      # delete
      $swOp = [Diagnostics.Stopwatch]::StartNew()
      $ok = [E33Native]::DeleteFileW($deleteTgt)
      $delErr = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
      $swOp.Stop()
      $deleteMs = [math]::Round($swOp.Elapsed.TotalMilliseconds, 3)
      $cand = Invoke-Candidate -Arm $arm -H $handles -FileHandle ([IntPtr]::Zero)
      $opRows.Add([pscustomobject]@{
        Rep = $r; Op = 'delete'; Target = $deleteTgt
        OpMs = $deleteMs; FlushMs = $cand.Ms; FlushStatus = $cand.Status
        OpOk = $ok; OpErr = $delErr })
      # recreate the deleted target for the next rep
      $h3 = New-DirtyFile -Path $deleteTgt -Tag "del-re-$arm-$r"
      [void][E33Native]::CloseHandle($h3)
    }

    Start-Sleep -Milliseconds 300
    $etlDone = Join-Path $etwDir ("arm-{0}-done.etl" -f $arm)
    & $wpr -stop $etlDone 2>&1 | Out-Null
    if (-not (Test-Path $etlDone)) {
      $script:ToolErrors.Add("wpr stop produced no etl for arm $arm") | Out-Null
    } else {
      Move-Item $etlDone $etl -Force
    }

    # Close candidate handles.
    foreach ($hh in @($handles.Stream, $handles.Stream2, $handles.Dir, $handles.LogFile)) {
      if ($hh -ne [IntPtr]::Zero) { [void][E33Native]::CloseHandle($hh) }
    }

    # Metadata extraction AFTER the window (records exist / not is the question;
    # timing attribution comes from ETW, per the plan).
    if (-not $SkipExtract) { Extract-Metadata -Tag ("arm-{0}" -f $arm) }

    $csv = Join-Path $ArtifactDir ("arm-{0}-ops.csv" -f $arm)
    $opRows | Export-Csv -NoTypeInformation -Path $csv -Encoding UTF8

    $flushOk = @($opRows | Where-Object { $_.FlushStatus -match 'ok=True|SUCCESS' }).Count
    $results.Add([pscustomobject]@{
      Arm = $arm; Dir = $dir
      StreamOpen1 = $handles.StreamOpen1; StreamOpen2 = $handles.StreamOpen2
      LogFileOpen = $handles.LogFileOpen
      OpsTotal = $opRows.Count
      FlushOk = $flushOk
      AvgFlushMs = [math]::Round((($opRows | Measure-Object FlushMs -Average).Average), 3)
      Etl = (Split-Path -Leaf $etl)
    })
    Write-Host ("[arm {0}] dir={1} flush_ok={2}/{3} avg_flush={4}ms stream1={5} stream2={6} logfile={7}" -f `
        $arm, (Split-Path -Leaf $dir), $flushOk, $opRows.Count, $results[-1].AvgFlushMs,
        $handles.StreamOpen1, $handles.StreamOpen2, $handles.LogFileOpen) -ForegroundColor Yellow
    # Clean trial files so the next arm starts from the same fixture shape.
    Get-ChildItem $dir -Filter ("e33_{0}_*" -f $arm) -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem $dir -Filter ("e33_{0}_ren_src.bin" -f $arm) -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
  }

  # ---------------------------------------------------------------- report
  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("E33 Tier-1 report - $Stamp")
  $lines.Add(("OS      : {0} build {1}" -f $os.Caption, [Environment]::OSVersion.Version.Build))
  $lines.Add(("Volume  : {0} ({1})" -f $Volume, $disk.FileSystem))
  $lines.Add(("Reps    : {0} per op per arm; large-dir entries: {1}" -f $Reps, $LargeDirEntries))
  $lines.Add(("Large-dir I30 non-resident probe: {0}" -f $nonResident))
  $lines.Add("")
  $lines.Add("arm  dir   stream1  stream2  logfile  ops  flush_ok  avg_flush_ms")
  foreach ($r in $results) {
    $lines.Add(("{0,-4} {1,-6} {2,-8} {3,-8} {4,-8} {5,4} {6,9} {7,12}" -f `
        $r.Arm, (Split-Path -Leaf $r.Dir), $r.StreamOpen1, $r.StreamOpen2,
        $r.LogFileOpen, $r.OpsTotal, $r.FlushOk, $r.AvgFlushMs))
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
  Write-Host ""
  Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
  if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed }
  Write-Host ("Log: " + $LogPath) -ForegroundColor Yellow
  exit 1
}
finally {
  try { Stop-Transcript | Out-Null } catch { }
  try { & (Join-Path $env:SystemRoot 'System32\wpr.exe') -cancel 2>&1 | Out-Null } catch { }
  if (-not $NoPause) { [void](Read-Host "Press Enter to close") }
}
