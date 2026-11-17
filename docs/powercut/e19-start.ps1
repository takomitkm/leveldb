# E19 all-arms starter (detached via WMI).
$bash = '<HOSTDIR>\MSYS2\usr\bin\bash.exe'
$cmd = "`"$bash`" --login -c `"cd <TASKDIR> && bash e19-all.sh`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
