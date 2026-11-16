# E17-W10 autopsy starter (detached via WMI): arms 1,11,13,14,15, prefix e17w10.
$bash = '<MSYS2>\usr\bin\bash.exe'
$cmd = "`"$bash`" --login -c `"cd <TASKDIR> && PREFIX=e17w10 ARMS='1 11 13 14 15' bash e17-autopsy-all.sh`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
