<#
  run-e28.ps1 - E28 Tier-1: single-operation rightkit FILE_WRITE_THROUGH paths.

  This is an ETW-only test. It does not claim power-loss durability.
  It measures whether the operation itself causes device-level metadata
  Write events, compared with parent-directory and volume-root flushes.

  Arms:
    A baseline      create file, no flush
    B flush(file)   create file + FlushFileBuffers(file)
    C flush(dir)    B + FlushFileBuffers(parent dir)
    D flush(root)   B + FlushFileBuffers(volume root)  positive control
    E nt-create-fwt NtCreateFile(FILE_CREATE, FILE_WRITE_THROUGH) + write + close
    F nt-rename-fwt open source with FILE_WRITE_THROUGH, NtSetInformationFile(FileRenameInformation)
    G nt-delete-fwt open target with FILE_WRITE_THROUGH, NtSetInformationFile(FileDispositionInformation)
    H rightkit      unmodified rightkit-fs crate via public StateDir API

  Usage:
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\run-e27.ps1 -Volume D: -Reps 100 -NoPause -RightKitExe path\to\e28-rightkit.exe
#>
param(
  [string]$Volume = 'D:',
  [int]$Reps = 100,
  [int]$TraceLevel = 5,
  [switch]$KeepEtl,
  [string]$RightKitExe = '',
  [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$script:Root       = Split-Path -Parent $PSCommandPath
$script:Stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
$script:LogPath    = Join-Path $script:Root ("e28-log-{0}.txt" -f $script:Stamp)
$script:ReportPath = Join-Path $script:Root ("e28-report-{0}.txt" -f $script:Stamp)
$script:Failed     = $false
$script:Tracer     = 'none'
$script:ProvidersUsed = ''
$script:ToolErrors = New-Object System.Collections.Generic.List[string]

try { Start-Transcript -Path $script:LogPath -Force | Out-Null } catch { }

try {
  $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  $isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if (-not $isAdmin) { throw "Administrator rights are required for ETW kernel providers." }

  $Volume = $Volume.TrimEnd('\')
  if ($Volume -notmatch '^[A-Za-z]:$') { throw "Volume must look like D: (got '$Volume')" }
  if ($Volume -ieq $env:SystemDrive) { throw "Refusing to run on the system volume ($env:SystemDrive)." }
  $disk = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='{0}'" -f $Volume)
  if (-not $disk) { throw "Volume $Volume not found." }
  if ($disk.FileSystem -ne 'NTFS') { throw "Volume $Volume is $($disk.FileSystem); NTFS required." }

  $script:WorkDir = Join-Path $Volume '\e27'
  New-Item -ItemType Directory -Force -Path $script:WorkDir | Out-Null
  Get-ChildItem -Path $script:WorkDir -Force -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

  $diskNumber = -1
  try {
    $part = Get-Partition -DriveLetter $Volume.Substring(0,1) -ErrorAction Stop
    $diskNumber = [int]$part.DiskNumber
  } catch { }

  if (-not ('E28Native' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class E28Native {
  [StructLayout(LayoutKind.Sequential)]
  public struct UnicodeString { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
  [StructLayout(LayoutKind.Sequential)]
  public struct ObjectAttributes {
    public uint Length; public IntPtr RootDirectory; public IntPtr ObjectName;
    public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService;
  }
  [StructLayout(LayoutKind.Sequential)]
  public struct IoStatusBlock { public IntPtr Status; public IntPtr Information; }
  [StructLayout(LayoutKind.Sequential)]
  public struct RenameInfo { public byte ReplaceIfExists; public IntPtr RootDirectory; public uint FileNameLength; public char FileName; }

  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr tmpl);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool WriteFile(IntPtr h, byte[] buf, int n, out int written, IntPtr ov);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool FlushFileBuffers(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool SetFileInformationByHandle(IntPtr h, int cls, IntPtr info, int size);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool CloseHandle(IntPtr h);
  [DllImport("ntdll.dll")]
  public static extern int NtCreateFile(out IntPtr handle, uint access, ref ObjectAttributes attr, out IoStatusBlock ios,
      IntPtr alloc, uint fileAttrs, uint share, uint disposition, uint options, IntPtr ea, uint eaLength);
  [DllImport("ntdll.dll")]
  public static extern int NtSetInformationFile(IntPtr handle, out IoStatusBlock ios, IntPtr info, uint size, int cls);
  [DllImport("ntdll.dll")]
  public static extern uint RtlNtStatusToDosError(int status);
}
'@
  }

  $GENERIC_WRITE = 0x40000000
  $DELETE_ACCESS = 0x00010000
  $SYNCHRONIZE   = 0x00100000
  $READ_ATTRIBUTES = 0x80
  $SHARE_ALL     = 0x00000007
  $CREATE_ALWAYS = 2
  $OPEN_EXISTING = 3
  $FILE_CREATE   = 2
  $FILE_OPEN     = 1
  $FLAG_BACKUP_SEM = 0x02000000
  $FILE_DIRECTORY_FILE = 0x00000001
  $FILE_NON_DIRECTORY_FILE = 0x00000040
  $FILE_WRITE_THROUGH = 0x00000002
  $FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
  $OBJ_CASE_INSENSITIVE = 0x00000040
  $FILE_RENAME_INFORMATION = 10
  $FILE_DISPOSITION_INFO = 4
  $STATUS_SUCCESS = 0

  function Get-Win32Err { [Runtime.InteropServices.Marshal]::GetLastWin32Error() }
  function NT-Success { param([int]$Status) return ($Status -ge 0) }
  function NT-Err { param([int]$Status) return [E28Native]::RtlNtStatusToDosError($Status) }

  function New-PaddedFile {
    param([string]$Path, [string]$Tag)
    $h = [E28Native]::CreateFileW($Path, $GENERIC_WRITE, 0, [IntPtr]::Zero, $CREATE_ALWAYS, 0, [IntPtr]::Zero)
    if ($h.ToInt64() -eq -1) { throw "CreateFileW failed err=$(Get-Win32Err) path=$Path" }
    $buf = [Text.Encoding]::ASCII.GetBytes(('P' * 2048))
    $tagb = [Text.Encoding]::ASCII.GetBytes($Tag)
    [Array]::Copy($tagb, 0, $buf, 0, [Math]::Min($tagb.Length, 2048))
    $written = 0
    if (-not [E28Native]::WriteFile($h, $buf, $buf.Length, [ref]$written, [IntPtr]::Zero)) {
      $e = Get-Win32Err; [void][E28Native]::CloseHandle($h); throw "WriteFile failed err=$e"
    }
    return $h
  }

  function Open-Directory {
    param([string]$Path)
    $h = [E28Native]::CreateFileW($Path, $GENERIC_WRITE, $SHARE_ALL, [IntPtr]::Zero, $OPEN_EXISTING, $FLAG_BACKUP_SEM, [IntPtr]::Zero)
    if ($h.ToInt64() -eq -1) { throw "Open directory failed err=$(Get-Win32Err) path=$Path" }
    return $h
  }

  function New-UnicodeBuffer {
    param([string]$Text)
    $wide = $Text.ToCharArray()
    $ptr = [Runtime.InteropServices.Marshal]::AllocHGlobal(($wide.Length + 1) * 2)
    [Runtime.InteropServices.Marshal]::Copy($wide, 0, $ptr, $wide.Length)
    [Runtime.InteropServices.Marshal]::WriteInt16($ptr, $wide.Length * 2, 0)
    return $ptr
  }

  function Nt-Create-Relative {
    param([IntPtr]$Root, [string]$Name, [uint32]$Access, [uint32]$Disposition, [uint32]$Options, [uint32]$Share = 0)
    $buf = New-UnicodeBuffer $Name
    try {
      $us = New-Object E28Native+UnicodeString
      $us.Length = [uint16]($Name.Length * 2)
      $us.MaximumLength = [uint16](($Name.Length + 1) * 2)
      $us.Buffer = $buf
      $usPtr = [Runtime.InteropServices.Marshal]::AllocHGlobal([Runtime.InteropServices.Marshal]::SizeOf([type][E28Native+UnicodeString]))
      [Runtime.InteropServices.Marshal]::StructureToPtr($us, $usPtr, $false)
      $oa = New-Object E28Native+ObjectAttributes
      $oa.Length = [uint32][Runtime.InteropServices.Marshal]::SizeOf([type][E28Native+ObjectAttributes])
      $oa.RootDirectory = $Root
      $oa.ObjectName = $usPtr
      $oa.Attributes = $OBJ_CASE_INSENSITIVE
      $oa.SecurityDescriptor = [IntPtr]::Zero
      $oa.SecurityQualityOfService = [IntPtr]::Zero
      $handle = [IntPtr]::Zero
      $ios = New-Object E28Native+IoStatusBlock
      $st = [E28Native]::NtCreateFile([ref]$handle, $Access, [ref]$oa, [ref]$ios, [IntPtr]::Zero, 0, $Share, $Disposition, $Options, [IntPtr]::Zero, 0)
      if (-not (NT-Success $st)) { throw "NtCreateFile($Name) status=$('0x{0:X8}' -f $st) dos=$(NT-Err $st)" }
      return $handle
    } finally {
      if ($usPtr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::FreeHGlobal($usPtr) }
      [Runtime.InteropServices.Marshal]::FreeHGlobal($buf)
    }
  }

  function Nt-Rename-Relative {
    param([IntPtr]$Root, [IntPtr]$Source, [string]$TargetName)
    $nameBytes = [Text.Encoding]::Unicode.GetBytes($TargetName + "`0")
    $size = 8 + 8 + 4 + 2 + $nameBytes.Length
    $info = [Runtime.InteropServices.Marshal]::AllocHGlobal($size)
    try {
      [Runtime.InteropServices.Marshal]::WriteByte($info, 0, 1)
      [Runtime.InteropServices.Marshal]::WriteIntPtr($info, 8, $Root)
      [Runtime.InteropServices.Marshal]::WriteInt32($info, 16, $nameBytes.Length - 2)
      [Runtime.InteropServices.Marshal]::Copy($nameBytes, 0, [IntPtr]($info.ToInt64() + 20), $nameBytes.Length)
      $ios = New-Object E28Native+IoStatusBlock
      $st = [E28Native]::NtSetInformationFile($Source, [ref]$ios, $info, [uint32]$size, $FILE_RENAME_INFORMATION)
      if (-not (NT-Success $st)) { throw "NtSetInformationFile(rename) status=$('0x{0:X8}' -f $st) dos=$(NT-Err $st)" }
    } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info) }
  }

  function Set-Disposition {
    param([IntPtr]$Handle)
    $buf = [Runtime.InteropServices.Marshal]::AllocHGlobal(1)
    try {
      [Runtime.InteropServices.Marshal]::WriteByte($buf, 0, 1)
      if (-not [E28Native]::SetFileInformationByHandle($Handle, $FILE_DISPOSITION_INFO, $buf, 1)) {
        throw "SetFileInformationByHandle(disposition) failed err=$(Get-Win32Err)"
      }
    } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($buf) }
  }

  function Invoke-RightKit {
    param([string]$ExeName, [string]$Target, [object]$Errors)
    $exe = Join-Path (Split-Path -Parent $RightKitExe) $ExeName
    if (-not (Test-Path $exe)) { throw "rightkit op exe not found: $exe" }
    $out = & $exe $Target 2>&1
    if ($LASTEXITCODE -ne 0) { throw ("{0} rc={1}: {2}" -f $ExeName, $LASTEXITCODE, (($out | ForEach-Object { "$_" }) -join ' | ')) }
  }

  function Measure-Flush {
    param([IntPtr]$Handle)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $ok = [E28Native]::FlushFileBuffers($Handle)
    $sw.Stop()
    return [pscustomobject]@{ Ok=$ok; Err=$(if ($ok) { 0 } else { Get-Win32Err }); Ms=[math]::Round($sw.Elapsed.TotalMilliseconds,3) }
  }

  # Tracer: wpr DiskIO first; same selection logic as E21 but simpler.
  $wprPath = Join-Path $env:SystemRoot 'System32\wpr.exe'
  if (Test-Path $wprPath) {
    $selftest = Join-Path $script:Root ("e28-selftest-{0}.etl" -f $script:Stamp)
    $out = & $wprPath -start DiskIO -filemode 2>&1
    if ($LASTEXITCODE -eq 0) {
      Start-Sleep -Milliseconds 300
      & $wprPath -stop $selftest 2>&1 | Out-Null
      if (Test-Path $selftest) { $script:Tracer='wpr'; $script:ProvidersUsed='wpr DiskIO profile' }
      Remove-Item $selftest -Force -ErrorAction SilentlyContinue
    }
  }

  function Start-Trace { param([string]$Etl)
    if ($script:Tracer -eq 'wpr') { & (Join-Path $env:SystemRoot 'System32\wpr.exe') -start DiskIO -filemode 2>&1 | Out-Null }
  }
  function Stop-Trace { param([string]$Etl)
    if ($script:Tracer -eq 'wpr') { Start-Sleep -Milliseconds 300; & (Join-Path $env:SystemRoot 'System32\wpr.exe') -stop $Etl 2>&1 | Out-Null }
  }
  function Convert-Trace { param([string]$Etl,[string]$Csv)
    if (-not (Test-Path $Etl)) { return $null }
    & tracerpt $Etl -o $Csv -of CSV -y 2>&1 | Out-Null
    if (Test-Path $Csv) { return $Csv } else { return $null }
  }
  function Count-Events { param([string]$Csv)
    $writes=0; $flushes=0
    if (-not $Csv) { return [pscustomobject]@{ Writes=0; Flushes=0 } }
    foreach ($line in [IO.File]::ReadLines($Csv)) {
      if ($line -match '(?i)^[^,]*,\s*Write\s*,') { $writes++ }
      if ($line -match '(?i)FlushBuffers|FlushInit') { $flushes++ }
    }
    return [pscustomobject]@{ Writes=$writes; Flushes=$flushes }
  }

  # A-D are the same controls as E27. E-H are separate single-operation binaries
  # built against the unmodified rightkit-fs crate:
  #   E rightkit-create-dir   F rightkit-rename   G rightkit-delete
  #   H rightkit-write (atomic publish as a positive internal control)
  $arms = @('A','B','C','D','E','F','G','H')
  $opExe = @{
    E = 'rightkit-create-dir.exe'
    F = 'rightkit-rename.exe'
    G = 'rightkit-delete.exe'
    H = 'rightkit-write.exe'
  }
  if (-not $RightKitExe) { $arms = @('A','B','C','D') }
  $results = New-Object System.Collections.Generic.List[object]
  $debugErrors = @{}

  foreach ($arm in $arms) {
    $armDir = Join-Path $script:WorkDir $arm
    New-Item -ItemType Directory -Force -Path $armDir | Out-Null
    $etl = Join-Path $script:Root ("e28-{0}-{1}.etl" -f $arm, $script:Stamp)
    $csv = Join-Path $script:Root ("e28-{0}-{1}.csv" -f $arm, $script:Stamp)
    $handles = New-Object System.Collections.Generic.List[object]
    $errors = New-Object System.Collections.Generic.List[string]
    $dirH = $null; $rootH = $null
    $setupRoot = $null

    # Setup is OUTSIDE the trace window for flush-only arms. For operation arms E/F/G,
    # setup of the source state is outside, but the measured operation itself is inside.
    if ($arm -in @('A','B','C','D')) {
      for ($r=0; $r -lt $Reps; $r++) {
        try { $handles.Add((New-PaddedFile (Join-Path $armDir ("a28_{0}_r{1}.bin" -f $arm,$r)) ("e28-$arm-r$r"))) | Out-Null }
        catch { $errors.Add($_.Exception.Message) | Out-Null }
      }
      if ($arm -eq 'C') { try { $dirH = Open-Directory $armDir } catch { $errors.Add($_.Exception.Message) | Out-Null } }
      if ($arm -eq 'D') { try { $rootH = Open-Directory ($Volume + '\') } catch { $errors.Add($_.Exception.Message) | Out-Null } }
    } else {
      # Rightkit single-op binaries create all state inside their own measured process.
      $setupRoot = [IntPtr]::Zero
    }

    Write-Host ("[E28 {0}] tracer={1} reps={2}" -f $arm, $script:Tracer, $Reps)
    try { Start-Trace $etl } catch { $script:ToolErrors.Add("arm $arm start: " + $_.Exception.Message) | Out-Null }

    $timings = New-Object System.Collections.Generic.List[object]
    for ($r=0; $r -lt $Reps; $r++) {
      switch ($arm) {
        'A' { }
        'B' { if ($r -lt $handles.Count) { $timings.Add((Measure-Flush $handles[$r])) | Out-Null } }
        'C' { if ($r -lt $handles.Count) { $timings.Add((Measure-Flush $handles[$r])) | Out-Null }; if ($dirH) { $timings.Add((Measure-Flush $dirH)) | Out-Null } }
        'D' { if ($r -lt $handles.Count) { $timings.Add((Measure-Flush $handles[$r])) | Out-Null }; if ($rootH) { $timings.Add((Measure-Flush $rootH)) | Out-Null } }
        'E' { Invoke-RightKit $opExe['E'] (Join-Path $armDir ("rk_$r")) $errors }
        'F' { Invoke-RightKit $opExe['F'] (Join-Path $armDir ("rk_$r")) $errors }
        'G' { Invoke-RightKit $opExe['G'] (Join-Path $armDir ("rk_$r")) $errors }
        'H' { Invoke-RightKit $opExe['H'] (Join-Path $armDir ("rk_$r")) $errors }
      }
    }

    try { Stop-Trace $etl } catch { $script:ToolErrors.Add("arm $arm stop: " + $_.Exception.Message) | Out-Null }
    foreach ($h in $handles) { [void][E28Native]::CloseHandle($h) }
    if ($dirH) { [void][E28Native]::CloseHandle($dirH) }
    if ($rootH) { [void][E28Native]::CloseHandle($rootH) }
    if ($setupRoot) { [void][E28Native]::CloseHandle($setupRoot) }

    $csvOut = Convert-Trace $etl $csv
    $ev = Count-Events $csvOut
    $avg = if ($timings.Count) { [math]::Round((($timings | Measure-Object Ms -Average).Average),3) } else { '' }
    $debugErrors[$arm] = @($errors.ToArray())
    $row = [pscustomobject]@{ Arm=$arm; Writes=$ev.Writes; Flushes=$ev.Flushes; AvgMs=$avg; Errors=$errors.Count; Csv=$(if ($csvOut) { Split-Path -Leaf $csvOut } else { '' }) }
    $results.Add($row) | Out-Null
    Write-Host ("     writes={0} flushes={1} avgMs={2} errors={3}" -f $row.Writes,$row.Flushes,$row.AvgMs,$row.Errors)
  }

  $lines = New-Object System.Collections.Generic.List[string]
  $b = $results | Where-Object Arm -eq 'B'
  $lines.Add("E28 Tier-1 report - $($script:Stamp)")
  $lines.Add("Host: $env:COMPUTERNAME")
  $lines.Add("OS: " + (Get-CimInstance Win32_OperatingSystem).Caption + " build " + [Environment]::OSVersion.Version.Build)
  $lines.Add("Volume: $Volume Tracer: $($script:Tracer) Providers: $($script:ProvidersUsed) Reps: $Reps")
  $lines.Add("Environment: GitHub Actions. ETW-only; no power-cut claim.")
  $lines.Add("")
  $lines.Add("arm  writes  flushes  avgMs  errors  csv")
  foreach ($r in $results) { $lines.Add(("{0}    {1,6}  {2,7}  {3,6}  {4,6}  {5}" -f $r.Arm,$r.Writes,$r.Flushes,$r.AvgMs,$r.Errors,$r.Csv)) }
  $lines.Add("")
  $lines.Add("Deltas vs B (flush file only):")
  foreach ($r in ($results | Where-Object Arm -in @('C','D','E','F','G'))) {
    $lines.Add(("  {0}-B writes = {1}" -f $r.Arm, ($r.Writes - $b.Writes)))
  }
  if ($script:ToolErrors.Count -gt 0) { $lines.Add("TOOL ERRORS:"); foreach ($e in $script:ToolErrors) { $lines.Add("  $e") } }
  $lines | Set-Content -Path $script:ReportPath -Encoding UTF8
  Write-Host ""; Write-Host ("Report: {0}" -f $script:ReportPath); $lines | ForEach-Object { Write-Host $_ }
}
catch {
  $script:Failed=$true
  Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
  if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed }
}
finally { try { Stop-Transcript | Out-Null } catch {} }
if ($script:Failed) { exit 1 }
