# Kill qemu VMs whose command line matches ci-disk (CI guest + E19 variants).
Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" |
  Where-Object { $_.CommandLine -match 'ci-disk' } |
  ForEach-Object { Write-Output ("killing " + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Output done
