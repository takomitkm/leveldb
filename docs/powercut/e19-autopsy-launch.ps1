# E19 autopsy launcher: CI guest (blockdev, bootindex=0) + 3 ro originals
# (bootindex 1..3) + 3 rw replay copies (bootindex 4..6).
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
$bi = 1
foreach ($a in 0,1,2) {
  $qargs += @(
    '-blockdev', "driver=file,node-name=ro$a,filename=$task\e19-datadisk-a$a.img,read-only=on",
    '-device', ('virtio-blk-pci,drive=ro{0},bootindex={1}' -f $a, $bi)
  )
  $bi++
}
foreach ($a in 0,1,2) {
  $qargs += @(
    '-blockdev', "driver=file,node-name=rw$a,filename=$task\e19-autopsy-rw-a$a.img",
    '-device', ('virtio-blk-pci,drive=rw{0},bootindex={1}' -f $a, $bi)
  )
  $bi++
}
$qargs += @(
  '-netdev', 'user,id=n0,hostfwd=tcp:<guest>:<guest-ssh-port>-:22',
  '-device', 'virtio-net-pci,netdev=n0',
  '-device', 'virtio-rng-pci',
  '-smbios', 'type=1,serial=ds=nocloud-net;s=http://10.0.2.2:8080/',
  '-display', 'none', '-monitor', 'none',
  '-serial', "file:$task\e19-autopsy-serial.log"
)
$cmd = ($qargs -join ' ')
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Output ("wmi-return=$($r.ReturnValue) pid=$($r.ProcessId)")
