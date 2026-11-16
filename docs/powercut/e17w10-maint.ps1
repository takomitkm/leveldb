# E17-W10 maintenance boot: WinPE + the Win10 golden (read-write, ONE-time
# probe update) + a data disk.  SMBIOS E17ARM=90 -> the probe's installer
# arm copies itself into the golden's \e14\e14pe3.exe and marks installed.txt
# on the data volume; host polls the blklogwrites log for the marker.
param([int]$Port = 4477)
$task = '<TASKDIR>'
$qemu = '<MSYS2>\ucrt64\bin\qemu-system-x86_64.exe'
$os   = "$task\os-golden.raw"
$data = "$task\e17w10-maint-data.img"
$log  = "$task\e17w10-maint-log.img"
$qargs = @(
  "`"$qemu`"",
  '-machine','pc','-accel','tcg,thread=multi','-cpu','max','-smp','8','-m','4096',
  '-no-reboot',
  '-smbios', 'type=1,serial=E17ARM=90;REPS=1',
  '-drive', "file=$os,if=ide,index=0,format=raw",
  '-blockdev', "driver=file,node-name=d0,filename=$data",
  '-blockdev', "driver=file,node-name=l0,filename=$log",
  '-blockdev', 'driver=blklogwrites,node-name=data,file=d0,log=l0,log-sector-size=512,log-super-update-interval=1',
  '-device', 'ide-hd,drive=data,bus=ide.0,unit=1',
  '-drive', "file=$task\pe-e17.iso,if=ide,index=2,media=cdrom",
  '-boot','order=d',
  '-display','none',
  '-monitor',"telnet:<guest>:$Port,server,nowait",
  '-serial',"file:$task\e17w10-maint-serial.log"
)
$cmd = ($qargs -join ' ')
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
