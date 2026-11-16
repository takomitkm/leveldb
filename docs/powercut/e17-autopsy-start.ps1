# E17 autopsy starter: run e17-autopsy-all.sh detached via WMI.
$bash = '<MSYS2>\usr\bin\bash.exe'
$cmd = "`"$bash`" --login -c `"cd <TASKDIR> && bash e17-autopsy-all.sh`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
