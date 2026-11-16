# E17-W10 autopsy starter for arms 16/17 (detached via WMI).
$bash = '<HOSTDIR>\MSYS2\usr\bin\bash.exe'
$cmd = "`"$bash`" --login -c `"cd <TASKDIR> && PREFIX=e17w10 ARMS='16 17' bash e17-autopsy-all.sh`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
