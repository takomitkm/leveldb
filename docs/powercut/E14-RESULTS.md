# E14 实验终报 — Windows NTFS 命名空间持久化的块层证据（2026-11-14）

**问题**（承接 E11/E12/E13，librime userdb / leveldb #195 家族）：
Windows 上把 MANIFEST rename 到位后，**目录项（命名空间变更）是否已在盘上**？
FlushFileBuffers(file)（上游现状）够不够？MOVEFILE_WRITE_THROUGH（fork durable-rename 补丁）够不够？
目录句柄 FlushFileBuffers（fork 30b34e0）是否真的把目录项推到盘上？

**方法**（QEMU/TCG + WinPE，全部校机 guest，宿主零接触）：
数据盘挂 QEMU `blklogwrites`（记录每条 WRITE/FLUSH 到块设备），探针每臂一次开机跑 3 轮，
路标/试验文件内容垫到 2048B 强制非驻留（数据写在日志中唯一出现，内容可寻址）；
宿主轮询日志见到 `E14DONE` 即强杀；**断电时刻由离线切点定义**（worst=最后屏障前的保证态 /
best=设备收到的全部写入）；臂号经 SMBIOS serial 注入（`how=S` 全部成功）；
ntfs-3g 双挂载验尸（ro,norecover=裸盘态 / rw+dirty位=重放态）。

## 六臂结果（arm=SMBIOS 全成功，reps=3，模式完全一致）

| 臂 | 操作 | 数据写到设备？ | 返回时屏障？ | 落盘的命名空间？ |
|---|---|---|---|---|
| 0 | create+write+close（什么都不刷） | **否**（OS 缓存） | 无 | 无 |
| 1 | +FlushFileBuffers(file)=上游现状 | 是@120 | **有** flush@123 | **无**（MFT/目录页全在内存） |
| 2 | 1 + FlushDir(父目录) | 是 | 有 | **无**（op 自身无 FlushDir） |
| 3 | MoveFileEx(REPLACE) 裸 rename | （预态有） | 无（142<148） | **无**（src 还在原名） |
| 4 | rename + WRITE_THROUGH（fork 补丁） | 同上 | **无**（143<152） | **无**——与臂 3 无差别 |
| 5 | CreateFile(FILE_FLAG_WRITE_THROUGH) | 是@121 | 无（110=引导期） | 无（孤簇） |

全日志 **FUA=0**。MFT 区（<8MiB）写入计数：arm0=0 / arm1=4（全在 park 窗口，切点外）/
arm2=4（同）/ **arm3=13、arm4=13（其中 rep0 窗口内 4-5 笔被屏障 141 覆盖 = FlushDir 干的）** / arm5=4（park 窗口）。

**验尸（12 张镜像 × ro+rw 重放，全部一致）**：
- a0/a1/a2/a5：只有 arm.txt——试验文件不存在（a1 数据簇在盘上但是孤儿）。
- a3/a4：`go.txt` + `rn*_r0_src.bin` **在盘上**（预态 FlushDir 推出来的！），`rn*_r0_dst.bin` 不存在。

## 结论

1. **上游现状（只 FlushFileBuffers(file)）**：文件**内容**有屏障保证，但**目录项不在盘上**——
   掉电后文件是否重现完全取决于 lazy writer/恢复时序。arm 1/2 vs arm 3/4 的对照直接证明：
   把命名空间推到盘上的是**目录句柄 flush**（go.txt 只在调了 FlushDir 的臂里出现在盘上）。
   → **fork 30b34e0（父目录 sync）有直接实证支持**。
2. **MOVEFILE_WRITE_THROUGH 对同卷 rename 不产生任何设备级屏障**（无 flush 无 FUA），
   命名空间变更与裸 rename 完全同形。PR 里"Windows 内核层尚未验证"的措辞现在有了实测内容：
   WRITE_THROUGH 的"落盘才返回"在这个栈上不等于存储屏障。
3. NTFS 元数据页（MFT/目录索引）在操作窗口内**从不自动落盘**（lazy writer 未及触发），
   只有三条路能推出去：文件 FlushFileBuffers、目录 FlushFileBuffers、lazy writer 定期刷。
4. E13 的"牙齿臂也存活"谜团解释：当时 25 分钟盲杀，杀在 lazy writer 已刷之后；
   E14 的离线切点把 park 期写入排除掉，牙齿臂如实地什么都没有。

## Caveats（如实记录）

- WinPE + QEMU IDE + TCG：FUA/flush 语义可能与真硬件栈（NVMe 等）不同；但 arm1 证明了
  guest→设备 flush 通路存在且 OS 决定刷时确实刷。
- 重放保真度：ntfs-3g 重放 ≠ ntfs.sys 重放。本实验里重放没有恢复出任何文件
  （journal 记录在盘上但重放未落地）；真 Windows 的恢复行为仍需真机验证。
  **盘上有什么字节**（本报告的主结论）与 OS 无关，是硬证据。
- worst-case = 「未加屏障的写入可能丢」的标准约定；best-case = 设备收到的全部写入。

## 产物位置

- 校机 `<TASKDIR>\`：e14v3-{base,vm,log}-a{0..5}.img、
  e14v3-crash-a{0..5}-{worst,best}.img（12 张）、e14v3-run-a*.log、e14v3-check.log、
  探针 e14pe3.cpp/e14pe3.exe、ISO pe-e14v3.iso。
- 本机工作区：全部分析器（e14_analyze.py / e14v3_analyze.py /
  e14v3_verdict.py / e14v3_crash.py / e14v3_dirty.py / e14v3_mftzone.py / ppm2png.py）——
  现归档于本目录及仓库 README 的 docs/powercut/ 一节。
- 校机 Debian CI guest（<guest-ssh-port>）负责 ntfs-3g 挂载验尸；EDA VM 与 CI VM 未动。

---

# E15 增补 — 完整 Windows（Win10 Pro 22H2）在 QEMU 中复测（2026-11-14）

**目的**：补 WinPE 局限（完整 ntfs.sys + 完整服务栈 + storahci 路径 + Defender 等真实环境）。

**环境**：QEMU/TCG 中无人值守安装的完整 Win10 Pro 22H2（19045.2965），无网络；同款 blklogwrites + SMBIOS 臂号 + 离线切点 + ntfs-3g 双挂载验尸。OS 盘快照只读金身（每臂 qcow2 overlay 丢弃），数据盘每臂 fresh 副本。探针自启 = ONSTART 计划任务（SYSTEM），接线在 OOBE 阶段经 Shift+F10 SYSTEM cmd 用 sendkey 打字完成（setup15.cmd）。

**安装陷阱实录**：①xorriso mkisofs 兼容层不支持 -udf → 不重打包 win10.iso，改 PE 引导 + setup.exe /unattend 对原盘装；②第一轮 XML 在 windowsPE 拒（组件不存在）→ 砍 International 段通过；③消费版介质无人值守必填 ProductKey（加 Pro 通用 key）；④语言页用 monitor `sendkey alt-n` 过（焦点无关）；⑤specialize 段在完整 Windows 又拒（组件不存在，diagerr 意外为空无法定位）→ **整段砍掉 specialize**（本地管理员账户改由 oobeSystem LocalAccounts 创建）——OOBE 仍掉进交互流 → Shift+F10 通道手工接线成功（串口回执 `E15-FIRSTLOGON-OK task=0`）。⑥ sendkey 反斜杠被 ssh/cmd 层剥掉 → 用 `d:` + `cd e14` 零分隔符路径规避；⑦QEMU telnet monitor 的 abrupt close 会毒化 chardev（CLOSE_WAIT 拒新连接，数分钟后自愈）→ 截屏脚本改 SHUT_RDWR + 重试。

**六臂结果（每臂 3 轮，与 WinPE 完全同构）**：

| 臂 | 操作 | worst屏障位置 | op数据写到设备？ | 落盘命名空间（验尸） |
|---|---|---|---|---|
| 0 | 裸创建写关 | 114 < 全部 op | 是（与 WinPE 不同：数据写到了设备但未屏障） | 无 |
| 1 | +FlushFileBuffers(file) | **122 ≥ 120 覆盖** | 是 | 无（数据孤儿） |
| 2 | +FlushDir | 126 ≥ 123 覆盖 | 是 | 无（op 自身未刷目录） |
| 3 | 裸 rename | 141 < 146 | （预态有） | **无：src 仍在原名，dst 不存在** |
| 4 | rename+WRITE_THROUGH | 141 < 148 | 同上 | **与臂 3 完全同形** |
| 5 | create+WRITE_THROUGH | 112 < 122 | 是（未屏障） | 无 |

全日志 FUA=0（与 WinPE 一致）。验尸：a0/a1/a2/a5 只有 arm.txt；**a3/a4 里 go.txt + src 在盘上（FlushDir 预态效应，完整 Windows 同样成立）**、dst 不存在（worst 与 best 同）。

**结论：E14 全部结论在完整 Windows 上原样成立**——WRITE_THROUGH 同卷 rename 无设备级屏障、只刷文件不保目录项、目录句柄 flush 是唯一实证把目录项推上盘的操作。剩余未知（ntfs.sys 重放恢复行为、真硬件）不变。
