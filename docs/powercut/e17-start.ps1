# E17 arm-campaign starter: run e17-all.sh detached via WMI (survives ssh).
$bash = '<MSYS2>\usr\bin\bash.exe'
$cmd = "`"$bash`" --login -c `"cd <TASKDIR> && bash e17-all.sh 0 1 10 5 8 7 9 2 3 4 6`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
