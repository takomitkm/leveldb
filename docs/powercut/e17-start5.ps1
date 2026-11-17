# E17 corrected-NT arms starter (detached via WMI).
$bash = '<HOSTDIR>\MSYS2\usr\bin\bash.exe'
$cmd = "`"$bash`" --login -c `"cd <TASKDIR> && bash e17-all.sh 20 21 22 23`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
