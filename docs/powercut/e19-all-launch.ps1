# E19 all-arms launcher: CI guest (qcow2 as two-level blockdev, bootindex on
# the device) + THREE NTFS data disks, each under its own blklogwrites.
$qemu = '<HOSTDIR>\MSYS2\ucrt64\bin\qemu-system-x86_64.exe'
$root = '<QEMUDIR>'
$task = '<TASKDIR>'
$qargs = @(
  "`"$qemu`"",
  '-machine','pc','-accel','tcg,thread=multi','-smp','8','-m','4096',
  '-blockdev', "driver=file,node-name=osfile,filename=$root\images\ci-disk.qcow2",
  '-blockdev', 'driver=qcow2,node-name=os,file=osfile',
  '-device', 'virtio-blk-pci,drive=os,bootindex=0'
)
foreach ($a in 0,1,2) {
  $qargs += @(
    '-blockdev', "driver=file,node-name=d$a,filename=$task\e19-datadisk-a$a.img",
    '-blockdev', "driver=file,node-name=l$a,filename=$task\e19-log-a$a.img",
    '-blockdev', ('driver=blklogwrites,node-name=data{0},file=d{0},log=l{0},log-sector-size=512,log-super-update-interval=1' -f $a),
    '-device', ('virtio-blk-pci,drive=data{0},bootindex={1}' -f $a, ($a + 1))
  )
}
$qargs += @(
  '-netdev', 'user,id=n0,hostfwd=tcp:<guest>:<guest-ssh-port>-:22',
  '-device', 'virtio-net-pci,netdev=n0',
  '-device', 'virtio-rng-pci',
  '-smbios', 'type=1,serial=ds=nocloud-net;s=http://10.0.2.2:8080/',
  '-display', 'none', '-monitor', 'none',
  '-serial', "file:$task\e19-serial.log"
)
$cmd = ($qargs -join ' ')
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
