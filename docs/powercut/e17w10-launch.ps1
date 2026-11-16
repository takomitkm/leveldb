# E17-W10 arm VM: OS golden via qcow2 overlay (discarded), data disk under
# blklogwrites, no CD, boot from disk, probe auto-runs via the ONSTART task
# (E14Probe -> C:\e14\e14pe3.exe, updated in the golden by arm 90 installer).
param(
  [string]$Arm = '0',
  [string]$Reps = '3',
  [int]$Port = 4476
)
$task = '<TASKDIR>'
$qemu = '<MSYS2>\ucrt64\bin\qemu-system-x86_64.exe'
$os   = "$task\e17w10-arm-a$Arm.qcow2"
$data = "$task\e17w10-datadisk-a$Arm.img"
$log  = "$task\e17w10-log-a$Arm.img"
$ser  = "$task\e17w10-serial-a$Arm.log"
& '<MSYS2>\ucrt64\bin\qemu-img.exe' create -f qcow2 -b "$task\os-golden.raw" -F raw $os 2>&1 | Out-Null
$qargs = @(
  "`"$qemu`"",
  '-machine','pc','-accel','tcg,thread=multi','-cpu','max','-smp','8','-m','6144',
  '-drive', "file=$os,if=ide,index=0,format=qcow2",
  '-blockdev', "driver=file,node-name=d0,filename=$data",
  '-blockdev', "driver=file,node-name=l0,filename=$log",
  '-blockdev', 'driver=blklogwrites,node-name=data,file=d0,log=l0,log-sector-size=512,log-super-update-interval=1',
  '-device', 'ide-hd,drive=data,bus=ide.0,unit=1',
  '-boot','order=c',
  '-smbios', "type=1,serial=E17ARM=$Arm;REPS=$Reps",
  '-display','none',
  '-monitor',"telnet:<guest>:$Port,server,nowait",
  '-serial',"file:$ser"
)
$cmd = ($qargs -join ' ')
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
