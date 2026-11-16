# E17 实验终报 — 各家 DB 目录 flush 修法在 QEMU 断电模型上的逐家验证（2026-11-17）

**问题**（承接 E14/E15，用户题设：按近日各家 DB 修法逐家验证 + 与 leveldb 测试对照）：
这几天给各数据库提的 Windows 父目录 flush 补丁（leveldb 30b34e0 / SQLite win32-dirsync / LMDB
mdb_mdirsync / PostgreSQL pgwin32_open_dir_for_sync / MySQL os_parent_dir_fsync_win /
FrankenSQLite #539），**它们共同的 API 形状——创建/改名后在父目录句柄上 FlushFileBuffers——
在这套断电模型里到底能不能把命名空间变更推到设备？**

**方法**（E14 rig 原样复用，全在校机）：
QEMU/TCG + WinPE（PE ISO 由验证过的 pe-e14v3.iso 抽树重建，探针经 WIM 注入），
数据盘挂 `blklogwrites`，臂号经 SMBIOS serial 注入（16 臂全部 `how=S`），每臂 3 轮，
宿主轮询日志见 `E17DONE` 即强杀；断电时刻由离线切点定义（worst=最后屏障前的保证态 /
best=设备收到的全部写入）；ntfs-3g 双挂载验尸（ro,norecover=裸盘态 / rw=重放态）；
MFT 区（<8MiB）写入按轮窗口计数。探针源码 `e17pe.cpp`（16 臂），驱动 `e17-run.sh` /
`e17-launch.ps1`，切点 `e17_verdict.py`，验尸 `e17-autopsy-all.sh`，MFT 分析 `e17_mftzone.py`。

**运行质量**：16 臂 × 3 轮全部完成，DONE 标记全数回收，**全臂 errs=0,0,0**（无 Defender/ACL 干扰）。

## 一、主结果表（每臂 rep0 窗口验尸；worst 与 best 全部一致）

| 臂 | 操作（=哪家修法） | MFT区写/轮 | 裸盘上可见 | 结论 |
|---|---|---|---|---|
| 0 | 只刷文件（上游现状，对照负组） | 0 | arm.txt | 试验文件不在盘 ✓（复现 E14） |
| 1 | create+刷文件+**刷父目录**（leveldb 30b34e0 / SQLite / MySQL / FrankenSQLite 共同形状） | 0 | arm.txt | **试验文件不在盘** |
| 2 | 同 1，但相对路径 + `"."` 解析（SQLite S7 形状） | 0 | arm.txt | 不在盘（形状差异不影响设备行为） |
| 3 | 同 1 + GetFinalPathNameByHandleW 回读（LMDB 形状） | 0 | arm.txt | 不在盘 |
| 4 | 同 1，但走 `_open_osfhandle`+`_commit(fd)`（PG transient-fd 形状） | 0 | arm.txt | 不在盘 |
| 5 | 耐久前置态→MySQL 删除舞步（MoveFileW+DeleteFileW）→刷父目录 | 0 | arm.txt | 前置态本身没上盘，删除不可判（设计缺陷，见下） |
| 6 | rename+WRITE_THROUGH（MySQL rename 位，前置态=双刷） | 4/3/3 | **src+go.txt** | dst 不在盘；WRITE_THROUGH 无屏障 ✓（复核 E14） |
| 7 | mkdir+刷父目录（MySQL create_directory 位） | 0 | arm.txt | 新子目录不在盘 |
| 8 | 耐久前置态（单刷）→裸删除，不刷 | 0 | arm.txt | 前置态没上盘，删除不可判 |
| 9 | mkdir 不刷（对照负组） | 0 | arm.txt | ✓ |
| 10 | rename 后刷父目录（E11 协议的 Windows 直译） | 4/3/3 | **src+go.txt** | **dst 不在盘**——Linux 有效的协议直译到 Windows 无效 |
| 11 | create+刷文件+**只刷卷根** | 4/3/3 | **a11_r0.bin+go.txt** | **卷根单刷即持久！** |
| 12 | create+刷文件+刷父目录+刷卷根 | 4/3/3 | **a12_r0.bin+go.txt** | 双刷有效（起作用的是 root） |
| 13 | create+刷文件+刷父目录×2 | 0 | arm.txt | 父目录刷两次仍无效 |
| 14 | rename 后刷父目录+刷卷根（修正协议） | 6/5/5 | **a14_r0_dst.bin**（src 消失） | **rename 持久化成立** |
| 15 | 耐久前置态（双刷）→删除→刷父目录+刷卷根 | 7/6/6 | **a15_r0.bin 从盘上消失** | **删除持久化成立** |

## 二、结论

1. **起作用的是 `FlushFileBuffers(卷根)`，不是 `FlushFileBuffers(父目录)`。**
   消歧三臂（11/12/13）+ 块级 MFT 计数完全一致：对**卷根句柄**的 FlushFileBuffers 一次调用就把
   本轮全部 MFT/index 元数据推下盘（4 笔/轮，1.05–1.13MB 处 = MFT 区），文件/改名/删除全部持久；
   对**子目录句柄**的 FlushFileBuffers 无论刷几次（臂 1/2/3/4/5/7/8/13）**零设备级元数据写入**，
   文件数据有屏障但命名空间留在内存。
   机制假说（未在真机验证）：ntfs.sys 对根目录句柄的 flush 走的是**卷级**刷写路径
   （与 `FlushFileBuffers(\\.\C:)` 同类），而子目录句柄只刷该目录自身的流——新建文件的
   MFT 记录不属于父目录的任何流，所以父目录刷不到它。这与 MS-FSA §2.1.5.7 注记 <80> 的
   "directory flush" 文义不冲突（根目录也是目录），但把「刷哪个目录」的工程含义彻底分开了。

2. **E14 的头条结论需要修正。** E14 写的是"the directory-handle flush is the only observed
   operation that puts the namespace change on the device"——E14 的阳性证据（go.txt/src 只在
   臂 3/4 上盘）现在有了精确解释：臂 3/4 的前置态里**恰好包含 FlushDir(root)**（e14pe3.cpp
   RunArm 的 `FlushDir(dir); FlushDir(root);`），起作用的是那一下**卷根刷**；E14 臂 2（单刷
   父目录）的试验文件本来就不在盘上（其验尸记录如此，当时的结论表也如实写了"无"），但归因
   错了对象。**单刷父目录不产生任何设备级元数据写入**——这一条对 leveldb 30b34e0 与五家 DB
   补丁同样成立（臂 1–4 形状全部无效）。

3. **修正后的协议在 Windows 上成立**（本栈）：
   - create：刷文件 + **刷卷根** → 持久（臂 11/12）；
   - rename：裸 rename + **刷父目录+刷卷根** → dst 在盘、src 消失（臂 14）；arm 10 证明
     漏掉卷根就无效；
   - delete：耐久删除 + 刷父目录+刷卷根 → 删除持久（臂 15；前置态落盘由臂 12 同形状保证）；
   - `MOVEFILE_WRITE_THROUGH` 对同卷 rename 仍然无屏障、与裸 rename 同形（臂 6 复核）。
   - 五家补丁的形状差异（相对路径/GetFinalPathNameByHandle/_commit(fd)）在设备级**零差异**
     （臂 2/3/4 与臂 1 逐字同结果）——差异全在 flush 的**对象**，不在路径解析或 fd 层。

4. **对本系列 12 个 PR 的直接含义**：这些补丁当前形状（刷父目录）在这套栈上不提供设备级
   耐久——要落地真耐久需要把 flush 对象改为**卷根**（或在父目录刷后追加一次卷根刷），
   或者如实把补丁降级为"行为对齐 POSIX、耐久性未证"。真机（NVMe/Win11/ReFS）上的
   root-flush 行为未测，改形状前必须先在真机复测。

## 三、Caveats（如实记录）

- WinPE + QEMU IDE + TCG 栈：FUA=0（全程），flush 语义与真 NVMe 栈可能不同。
- ntfs-3g 重放 ≠ ntfs.sys 重放；但"盘上有什么字节"与 OS 无关，是硬证据。
- crash 镜像只做了 rep0（worst/best 双切点），rep1/2 仅日志级判读（与 rep0 同形）。
- 臂 5/8 的删除可判性缺陷：耐久前置态用的单刷不落盘（当时还不知道单刷无效），
  删除语义由臂 15（双刷前置态）补测成立。
- 验尸时 ntfs-3g 对脏卷的 RW 挂载打印 `unclean (0,0)` 警告后正常重放；所有镜像 RO/RW
  结论一致。
- 根目录 flush 的**代价**未测（卷级刷写预计远贵于单目录刷，E14 量过单目录刷 0.66–3.5ms）——
  改补丁形状前必须补代价测量。
- 真硬件（NVMe/Win11/ReFS/SMB）未测；本结论限 WinPE/TCG/IDE/NTFS。

## 四、产物位置

- 校机 `<TASKDIR>\`：e17-{base,vm,log}-a{0..15}.img、
  e17-crash-a{0..15}-{worst,best}.img（32 张）、e17-run-a*.log、e17-all.log、
  e17-autopsy-all.log、探针 e17pe.cpp/e17pe.exe、ISO pe-e17.iso、e17-phase{1,1b,1c,2}.log。
- 本机 `S:\apps\llm\agentwork\.tmp\lvl-fb\`：探针与全套驱动/分析器（已同步 fork
  `docs/powercut/`）；本报告。
- 仓库归档：takomitkm/leveldb `docs/powercut/`（E11/E14/E17 全套 + 本报告）。

## 五、与 E14 测试代码的对照（用户问题：和 leveldb 那套比，差异在哪）

| 维度 | E14 rig（leveldb） | E17 rig（按各家 DB 修法） |
|---|---|---|
| rig 机制 | SMBIOS 臂号 / blklogwrites / 离线切点 / ntfs-3g 双验尸 / 2048B 内容寻址标记 | **原样复用，一字未改**（同 base 盘、同 launch 配方、同分析器） |
| 探针 | e14pe3.cpp，6 臂，leveldb 场景形状 | e17pe.cpp，16 臂，逐家复刻 DB 补丁的 API 序列 |
| 新覆盖面 | create/rename 两类 | + 删除（含 MySQL 两步舞步）、mkdir、**卷根消歧臂**（11–15） |
| 关键差异 | 臂 2 的 FlushDir 只刷父目录；阳性信号来自前置态里未单独验证的 FlushDir(root) | 消歧臂把「刷谁」拆成受控变量，修正了归因 |
| 切点/验尸 | worst/best 双切点，rep0 镜像 | 同 |
