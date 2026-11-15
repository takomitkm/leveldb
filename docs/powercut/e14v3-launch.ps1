param(
  [string]$Disk = 'e14v3-vm.img',
  [string]$Log  = 'e14v3-log.img',
  [string]$ArmArg = 'E14ARM=98;REPS=3',
  [int]$Port = 4463
)
# E14 v3 launcher: one arm per boot. -no-reboot so a real guest crash exits
# QEMU instead of reboot-looping. Data node = default writeback (models the
# volatile disk cache); blklogwrites records every guest WRITE/FLUSH.
$task = '<TASKDIR>'
$qemu = '<MSYS2>\ucrt64\bin\qemu-system-x86_64.exe'
$qargs = @(
  "`"$qemu`"",
  '-machine','pc','-accel','tcg,thread=multi','-cpu','max','-smp','8','-m','4096',
  '-no-reboot',
  '-smbios', "type=1,serial=$ArmArg",
  '-blockdev', "driver=file,node-name=d0,filename=$task\$Disk",
  '-blockdev', "driver=file,node-name=l0,filename=$task\$Log",
  '-blockdev', 'driver=blklogwrites,node-name=data,file=d0,log=l0,log-sector-size=512,log-super-update-interval=1',
  '-device', 'ide-hd,drive=data,bus=ide.0,unit=0',
  '-drive', "file=$task\pe-e14v3.iso,if=ide,media=cdrom,index=1",
  '-boot','order=d','-display','none',
  '-monitor',"telnet:<guest>:$Port,server,nowait",
  '-serial',"file:$task\e14v3-serial.log"
)
$cmd = ($qargs -join ' ')
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")