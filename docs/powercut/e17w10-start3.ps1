# E17-W10 arms 16/17 starter (detached via WMI).
$bash = '<HOSTDIR>\MSYS2\usr\bin\bash.exe'
$cmd = "`"$bash`" --login -c `"cd <TASKDIR> && bash e17w10-all.sh 16 17`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
