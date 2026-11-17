# E19 — Linux (ntfs-3g) on NTFS: does fsync(parent dir) matter?（2026-11-17）

**问题**（承接 E11/E17）：POSIX 侧「rename/create 后必须 fsync 父目录」这条规则，
在 **Linux 挂 NTFS（ntfs-3g）** 时是否同样成立？E11 在 ext4 上测过（fsync(dir) 必需），
E17 在 Windows 原生 NTFS 上测过（父目录刷无效、卷根刷才有效）。本实验补上第三方组合。

**方法**（E11 协议 + E17 磁盘资产）：
Debian 12 CI guest（ext4 根）挂 **3 块 NTFS 数据盘**（来自 E17 的 `e17-base.img`，
每块独立挂一个 `blklogwrites`），`mount -t ntfs-3g` 以读写方式挂载；探针 `e19lin.c`：
先 fsync 一个预置态文件（把挂载期元数据写入限制在窗口外），再创建 2048B 内容可寻址文件，
按臂执行 flush；探针退出后 **1 秒**宿主强杀 QEMU（= 断电）；
验尸 = 同一 guest 重新引导，3 块只读原件 + 3 块 rw 副本，分别 `ro,norecover`（裸盘态）
与 `rw`（ntfs-3g 日志重放）挂载后列目录。

**三臂 × 各 1 轮（一次引导、一次断电、三块独立盘）**：

| 臂 | 操作 | RO-NORECOVER（裸盘） | RW-REPLAY（重放） |
|---|---|---|---|
| 0 | create+write+**fsync(file)** | **a0_r0.bin 在盘** | 在盘 |
| 1 | 同上 + **fsync(dirfd)** | **a1_r0.bin 在盘** | 在盘 |
| 2 | create+write，**不刷**（牙齿对照） | **不在盘**（目录空） | 不在盘 |

探针 rc 全 0；三块盘各 4GB blklogwrites 日志。

## 结论

1. **ntfs-3g 上 `fsync(file)` 就足以把目录项推下盘**——臂 0 与臂 1 同结果，
   `fsync(父目录)` **不增加任何东西**。原因是 ntfs-3g 的 fsync 提交的是整个
   inode+索引事务（NTFS 的目录索引 `$I30` 与文件 MFT 记录在它眼里属于同一次提交），
   不存在 ext4 那种「文件数据与目录项分开落盘」的窗口。
2. **牙齿对照有效**：不刷的臂 2 确实丢文件，说明 1 秒窗口/断电模型本身不会替我们刷盘。
3. **与 ext4 相反**：E11 在 ext4 上 `fsync(file)` 8/8 丢、必须 `fsync(dir)`；
   ntfs-3g 上 `fsync(file)` 即持久。=> **「必须 fsync 父目录」是文件系统驱动相关的
   契约，不是通用 POSIX 保证**。对 leveldb/SQLite 的含义：它们的 POSIX 侧
   `fsync(dir)` 在 ext4 上是必需的（正确），在 ntfs-3g 上是冗余但无害的；
   而 Windows 原生侧那条路（父目录刷）连设备都不碰（E17）——三种栈三种行为。

## Caveats

- ntfs-3g = FUSE 用户态驱动（Debian 12 默认）。**Linux 内核 ntfs3 驱动未测**
  （CI guest 内核没有该模块，装 modules-extra 需联网+内核包）。
- 每臂 1 轮（E11 协议原样），样本量小；但三臂互相对照且牙齿臂复现了「不刷就丢」，
  判别力已被证实。若需统计强度，可扩到 5 轮。
- ntfs-3g 的 fsync 语义是它自己的实现（用户态事务提交），与内核 ntfs3、与 Windows
  原生 ntfs.sys 都不同——这正是本实验的结论本身，不是缺陷。
- 断电 = 宿主强杀 QEMU（内存中的 FUSE 写缓存随进程消失），与 E11 一致。

## 产物

- 校机 `<TASKDIR>`：e19-datadisk-a{0,1,2}.img、e19-log-a{0,1,2}.img、
  e19-autopsy-rw-a{0,1,2}.img、e19-all.log、e19-autopsy.log、e19-serial.log。
- 脚本：`e19lin.c`（探针）、`e19-all-launch.ps1`（三盘 + 三 blklogwrites 的 QEMU 启动）、
  `e19-all.sh`（单靴三臂驱动）、`e19-autopsy-launch.ps1` / `e19-autopsy.sh`（3 ro + 3 rw 验尸）。

## 附：E20（Windows + ext4）可行性判定 — 未执行

- Windows 无原生 ext4 支持；唯一可写第三方驱动是 Ext2Fsd（2017，需 WDK 构建或
  老安装包；SourceForge 官方路径 404，GitHub 镜像只有源码无二进制）。
- 校机宿主非管理员（`net session` → NOT-ADMIN），装不了内核驱动；宿主也无任何
  ext2/ext4/Paragon 驱动（driverquery 零命中）。
- 即使装上：Ext2Fsd 是第三方 FSD，其 `FlushFileBuffers` 实现是它自己的语义，
  既不能代表 Linux 的 ext4（E11 已测），也不能代表 NTFS（E17 已测），
  而且 Windows+ext4 不是任何数据库会跑的生产栈。=> 测它没有可推广的结论，**不执行**。
