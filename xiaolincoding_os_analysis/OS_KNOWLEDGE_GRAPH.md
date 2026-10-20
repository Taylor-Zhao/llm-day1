# 小林 Coding 操作系统面试题知识图谱

本文将 [操作系统面试题](https://xiaolincoding.com/interview/os.html) 的 6 个章节、51 个问题整理为一张 Mermaid 总图。重点不是孤立背诵定义，而是理解 CPU 特权级、任务调度、并发同步、虚拟内存、中断和网络 I/O 如何共同组成操作系统运行链路。

## 读图约定

- `[01]` 到 `[06]` 对应原文六个章节：用户态和内核态、进程管理、锁、内存管理、中断、网络 I/O。
- 实线表示组成、执行、数据流或直接依赖；虚线表示对比、约束、风险、优化或跨主题关系。
- 同一节点的出边按顺序使用不同颜色，便于区分它连接到的不同概念。
- 跨主题关系使用 3px 连线，主题内部关系使用 2px 连线。
- 图中的 Linux 实现细节（如 `epoll`、`brk`、`mmap`、`kswapd`）与通用操作系统概念分层表达。
- 节点速查、面试问答与逐边索引均使用图中显示名称，不要求记忆内部标识；箭头文字区分执行、组成、对比和约束，不把所有连接读成同一条执行顺序。
- 堆栈布局、分配阈值、回收算法和服务器架构均有平台与版本条件；下文将 Linux 机制与可在当前 macOS 验证的教学代码分开。

## 总图

```mermaid
---
config:
  theme: base
  themeVariables:
    edgeLabelBackground: "#fff7d6"
---
flowchart TB
    ROOT["操作系统全景<br/>隔离 + 抽象 + 调度 + 资源管理 + I/O"]

    subgraph MODE_AREA["用户态与内核态"]
        MODE["CPU 运行模式<br/>权限与可执行指令的边界"]
        USER["用户态 User Mode<br/>不能任意执行特权指令或访问设备"]
        KERNEL["内核态 Kernel Mode<br/>可执行特权操作<br/>仍受硬件、虚拟化与系统策略约束"]
        KFUNC["内核核心职责<br/>进程 / 内存 / 设备 / 文件系统"]
        SYSCALL["系统调用 System Call<br/>受控进入内核的接口"]
        MODE_SWITCH["特权级切换<br/>保存现场 -> 内核处理 -> 返回"]
        PROTECTION["保护目标<br/>安全性 / 稳定性 / 隔离性"]
        APP["用户程序<br/>通过库与系统调用请求服务"]
    end

    subgraph PROCESS_AREA["进程管理"]
        PROCESS["进程 Process<br/>资源分配与隔离的基本单位"]
        THREAD["线程 Thread<br/>CPU 调度与执行的基本单位"]
        COROUTINE["协程 Coroutine<br/>用户态轻量执行单元"]
        PROC_RESOURCE["进程资源<br/>虚拟地址空间 / 文件描述符<br/>信号处理 / IPC / 凭据"]
        SHARED["同进程线程共享<br/>代码 / 数据 / 堆 / 打开文件"]
        PRIVATE["线程私有上下文<br/>寄存器 / PC / 栈 / TLS"]
        PCB["PCB<br/>进程状态、资源与上下文"]
        TCB["TCB<br/>线程状态、寄存器与栈"]
        ISOLATION["进程隔离<br/>独立地址空间限制故障传播"]
        CONCURRENCY["并发与并行<br/>多任务交替 / 多核同时执行"]
        THREAD_TRADEOFF["多线程权衡<br/>吞吐与多核利用 vs 竞争与开销"]
        CONTEXT["CPU 上下文<br/>寄存器 + 程序计数器 + 栈指针"]
        PROC_SWITCH["进程切换<br/>地址空间 + 内核栈 + CPU 上下文"]
        THREAD_SWITCH["线程切换<br/>共享地址空间时少切换内存映射"]
        READY["就绪态 Ready<br/>等待 CPU"]
        RUNNING["运行态 Running<br/>占用 CPU"]
        BLOCKED["阻塞态 Blocked<br/>等待 I/O 或事件"]
        CREATED["创建态 New"]
        TERMINATED["结束态 Terminated"]
        SCHEDULER["调度器 Scheduler<br/>从就绪队列选择任务"]
        PREEMPT["抢占 / 时间片<br/>时钟中断收回 CPU"]
        FCFS["FCFS<br/>先来先服务，长作业可能拖延短作业"]
        SJF["SJF<br/>平均等待低，长作业可能饥饿"]
        HRRN["HRRN<br/>响应比 = (等待+服务)/服务"]
        RR["Round Robin<br/>时间片公平，片长影响响应与切换"]
        PRIORITY["优先级调度<br/>静态/动态，抢占/非抢占"]
        MLFQ["多级反馈队列 MLFQ<br/>多优先级 + 不同时间片 + 反馈"]
        IPC["进程间通信 IPC<br/>跨隔离边界交换数据与事件"]
        PIPE["管道 Pipe<br/>匿名/命名，内核 FIFO 字节流"]
        MSGQ["消息队列<br/>保留消息边界<br/>内部结构、类型与优先级依接口"]
        SHM["共享内存<br/>不同虚拟地址映射同一物理页"]
        SIGNAL["信号 Signal<br/>异步事件通知"]
        SEM["信号量 Semaphore<br/>P/V 原子操作，计数同步"]
        SOCKET["Socket<br/>本机或跨主机 TCP/UDP/Unix 通信"]
        THREAD_SYNC["线程同步与通信<br/>锁 / 条件变量 / 信号量"]
    end

    subgraph LOCK_AREA["锁、同步与死锁"]
        RACE["竞态条件 Race Condition<br/>结果依赖并发执行顺序"]
        CRITICAL["临界区 Critical Section<br/>访问共享可变状态"]
        ATOMIC["原子性 / 可见性 / 有序性<br/>并发正确性的基础"]
        MUTEX["互斥锁 Mutex<br/>无竞争时可走用户态快速路径<br/>竞争时可自旋或阻塞，依实现"]
        SPIN["自旋锁 Spinlock<br/>竞争失败后忙等待"]
        CAS["CAS 原子指令<br/>Compare-And-Swap"]
        PAUSE["PAUSE 指令<br/>降低忙等待开销"]
        RWLOCK["读写锁<br/>多读共享 / 写独占"]
        COND["条件变量<br/>等待-唤醒，通常配合 Mutex"]
        LOCK_SEM["信号量<br/>限制并发资源数量"]
        LOCK_CHOICE["锁选型<br/>临界区长度 / 竞争度 / 读写比例"]
        DEADLOCK["死锁 Deadlock<br/>任务永久互相等待"]
        D_MUTEX["互斥条件"]
        D_HOLD["持有并等待"]
        D_NO_PREEMPT["不可剥夺"]
        D_CYCLE["环路等待"]
        ORDERING["资源有序分配<br/>统一加锁顺序"]
        BANKER["银行家算法<br/>分配前检查安全状态"]
        SAFE_SEQ["安全序列<br/>所有进程都能依次完成"]
        OPTIMISTIC["乐观锁<br/>版本号/CAS，冲突时重试"]
        PESSIMISTIC["悲观锁<br/>访问前加锁，阻止并发修改"]
    end

    subgraph MEMORY_AREA["内存管理"]
        VM["虚拟内存<br/>每进程独立地址空间"]
        PM["物理内存<br/>实际 RAM 页框"]
        LOCALITY["局部性原理<br/>时间局部性 + 空间局部性"]
        PAGING["分页 Paging<br/>固定大小 Page / Frame"]
        PAGE_TABLE["页表 Page Table<br/>VPN -> PFN + 权限/存在位"]
        MULTILEVEL["多级页表<br/>按需分配页表层级"]
        MMU["MMU<br/>硬件地址翻译"]
        TLB["TLB<br/>页表项高速缓存"]
        TRANSLATE["地址转换<br/>页号查映射 + 页内偏移"]
        PAGE_FAULT["缺页异常 Page Fault<br/>映射不存在或权限违规"]
        SEGMENT["分段 Segmentation<br/>段基址 + 偏移 + 界限"]
        FRAGMENT["内存碎片<br/>分页有内部碎片 / 分段有外部碎片"]
        LAYOUT["进程虚拟内存布局<br/>代码 / Data / BSS / Heap / mmap / Stack"]
        HEAP["堆 Heap<br/>动态分配<br/>布局与分配器、ABI有关"]
        STACK["栈 Stack<br/>调用帧与局部变量，LIFO"]
        MALLOC["malloc<br/>C 库分配器，申请虚拟内存"]
        BRK["brk<br/>调整 program break<br/>部分分配器用于扩展堆区"]
        MMAP["mmap<br/>文件映射区 / 匿名映射"]
        FORK["fork<br/>复制页表与进程元数据"]
        COW["Copy-on-Write<br/>私有映射暂共享页<br/>通过页表限制写权限"]
        WRITE_FAULT["写保护异常<br/>COW时按需复制或复用页<br/>更新映射与写权限"]
        PRESSURE["内存压力<br/>空闲页不足"]
        KSWAPD["后台回收 kswapd<br/>异步回收"]
        DIRECT["直接回收 Direct Reclaim<br/>同步阻塞申请进程"]
        FILE_PAGE["文件页<br/>干净页丢弃 / 脏页先回写"]
        ANON_PAGE["匿名页<br/>堆栈等，可换出到 Swap"]
        SWAP["Swap<br/>磁盘暂存匿名页"]
        LRU_LIST["Active / Inactive LRU 链表<br/>按活跃程度选择回收页"]
        OOM["OOM Killer<br/>回收不足时终止进程"]
        REPLACE["页面置换<br/>需要腾出页框时选择牺牲页<br/>真实回收不只发生在缺页时"]
        OPT["OPT<br/>淘汰未来最晚访问页，仅作理论基准"]
        FIFO["FIFO<br/>淘汰驻留最久页面"]
        LRU["LRU<br/>淘汰最近最久未访问页面"]
        CLOCK["Clock<br/>环形链表 + 访问位，近似 LRU"]
        LFU["LFU<br/>淘汰访问次数最少页面"]
    end

    subgraph INTERRUPT_AREA["中断与异常"]
        INTERRUPT["中断机制<br/>响应异步事件或同步异常<br/>返回前可能发生调度"]
        EXTERNAL["外部中断 / 硬件中断<br/>来自 CPU 外部设备"]
        MASKABLE["可屏蔽中断 INTR<br/>普通设备事件"]
        NMI["不可屏蔽中断 NMI<br/>不受常规中断屏蔽控制<br/>硬件告警或诊断等用途"]
        INTERNAL["内部中断<br/>软件主动或 CPU 检测异常"]
        TRAP["陷阱 Trap<br/>x86例：断点、单步等<br/>通常返回后继续下一条指令"]
        FAULT["故障 Fault<br/>可修复后重试当前指令"]
        ABORT["终止 Abort<br/>致命且不可恢复"]
        VECTOR["中断向量表<br/>类型 -> Handler 入口"]
        SAVE["保存执行现场<br/>PC / 寄存器 / 状态"]
        ISR["中断处理程序 ISR<br/>确认并处理事件"]
        IRET["恢复现场并返回<br/>继续被中断任务"]
        TIMER["时钟中断<br/>时间片与抢占调度"]
        POLLING["轮询 Polling<br/>CPU 主动反复查询设备"]
        RESPONSIVENESS["中断价值<br/>提高 CPU 利用率与事件响应性"]
    end

    subgraph IO_AREA["网络 I/O 与高性能服务器"]
        IO_MODEL["Unix 网络 I/O 模型<br/>等待就绪 + 数据拷贝"]
        BLOCKING["阻塞 I/O<br/>调用线程等待就绪与完成"]
        NONBLOCK["非阻塞 I/O<br/>不能继续时返回EAGAIN等<br/>通常配合就绪通知"]
        MULTIPLEX["I/O 多路复用<br/>一个线程等待多个 FD 事件"]
        SIGIO["信号驱动 I/O<br/>就绪时由信号通知"]
        AIO["异步 I/O<br/>内核完成操作后通知应用"]
        SERVER_ARCH["并发服务器架构<br/>连接数、CPU、任务耗时的权衡"]
        SINGLE_SERVER["单线程逐请求<br/>简单但并发能力低"]
        THREAD_SERVER["多进程/多线程<br/>并行处理但资源与切换成本高"]
        EVENT_SERVER["事件驱动服务器<br/>多路复用少量线程处理多连接"]
        HYBRID_SERVER["多路复用 + 工作线程/进程池<br/>I/O 与业务计算分离"]
        SELECT["select<br/>fd_set / FD_SETSIZE 常见为 1024<br/>集合拷贝与扫描，限制依平台"]
        POLL["poll<br/>动态数组 / 仍为 O(n) 扫描"]
        EPOLL["epoll<br/>内核维护关注集合与就绪事件"]
        EPOLL_RBTREE["红黑树<br/>epoll_ctl 管理关注 FD"]
        READY_LIST["就绪链表<br/>回调加入活跃 FD"]
        LT["水平触发 LT<br/>只要仍就绪就持续通知"]
        ET["边缘触发 ET<br/>按I/O活动报告就绪<br/>不保证剩余数据会再次提醒"]
        DRAIN["非阻塞读写到 EAGAIN<br/>读到EAGAIN、EOF或错误<br/>写到待发完或EAGAIN，并保持公平"]
        REACTOR["Reactor<br/>监听分发事件 + Handler 资源池"]
        REDIS["Redis 6.0 前<br/>单 Reactor 单进程"]
        NETTY["Netty<br/>多 Reactor 多线程"]
        NGINX["Nginx<br/>多 Reactor 多进程 + accept 协调"]
        TRADITIONAL_IO["传统文件传输<br/>典型 read + write 路径<br/>两次系统调用与多次数据搬运<br/>不把模式切换都算作任务切换"]
        DMA["DMA<br/>设备与内存间搬运，不占用 CPU 拷贝"]
        ZERO_COPY["零拷贝<br/>减少用户态往返与 CPU 拷贝"]
        SENDFILE["sendfile<br/>内核内完成文件到 Socket 路径"]
        C10K["C10K<br/>大量并发连接的资源与事件问题"]
    end

    ROOT -- "`**特权隔离**`" --> MODE
    ROOT -- "`**任务抽象**`" --> PROCESS
    ROOT -- "`**并发正确性**`" --> RACE
    ROOT -- "`**地址空间**`" --> VM
    ROOT -- "`**异步事件**`" --> INTERRUPT
    ROOT -- "`**设备与网络**`" --> IO_MODEL

    MODE -- "`**低权限执行**`" --> USER
    MODE -- "`**高权限执行**`" --> KERNEL
    MODE -- "`**划分目的**`" --> PROTECTION
    APP -- "`**请求内核服务**`" --> SYSCALL
    USER -- "`**发起**`" --> SYSCALL
    SYSCALL -- "`**触发**`" --> MODE_SWITCH
    MODE_SWITCH -- "`**进入**`" --> KERNEL
    KERNEL -- "`**执行**`" --> KFUNC
    KERNEL -- "`**返回**`" --> USER
    PROTECTION -- "`**限制故障与越权**`" --> APP

    PROCESS -- "`**拥有**`" --> PROC_RESOURCE
    PROCESS -- "`**由一个或多个组成**`" --> THREAD
    PROCESS -- "`**内核记录**`" --> PCB
    PROCESS -- "`**地址空间隔离实现**`" --> ISOLATION
    THREAD -- "`**共享**`" --> SHARED
    THREAD -- "`**独有**`" --> PRIVATE
    THREAD -- "`**内核记录**`" --> TCB
    THREAD -- "`**支撑**`" --> CONCURRENCY
    COROUTINE -- "`**由用户运行时调度**`" --> THREAD
    COROUTINE -. "`**避免频繁内核切换**`" .-> THREAD_SWITCH
    ISOLATION -. "`**进程崩溃通常不传播**`" .-> PROCESS
    CONCURRENCY -- "`**收益与代价**`" --> THREAD_TRADEOFF
    THREAD_TRADEOFF -. "`**线程过多导致切换与竞争**`" .-> THREAD_SWITCH
    CONTEXT -- "`**保存在**`" --> PCB
    CONTEXT -- "`**线程级保存在**`" --> TCB
    PROC_SWITCH -- "`**保存/恢复**`" --> CONTEXT
    THREAD_SWITCH -- "`**保存/恢复**`" --> PRIVATE
    PROC_SWITCH -. "`**通常开销更大**`" .-> THREAD_SWITCH
    SCHEDULER -- "`**选择**`" --> READY
    READY -- "`**获得 CPU**`" --> RUNNING
    RUNNING -- "`**等待事件**`" --> BLOCKED
    BLOCKED -- "`**事件完成**`" --> READY
    RUNNING -- "`**时间片耗尽**`" --> READY
    RUNNING -- "`**完成或错误**`" --> TERMINATED
    CREATED -- "`**初始化完成**`" --> READY
    PREEMPT -- "`**驱动**`" --> SCHEDULER
    SCHEDULER -- "`**可采用**`" --> FCFS
    SCHEDULER -- "`**可采用**`" --> SJF
    SCHEDULER -- "`**可采用**`" --> HRRN
    SCHEDULER -- "`**可采用**`" --> RR
    SCHEDULER -- "`**可采用**`" --> PRIORITY
    SCHEDULER -- "`**可采用**`" --> MLFQ
    RR -- "`**关键参数**`" --> PREEMPT
    PRIORITY -. "`**可用反馈与老化缓解饥饿**`" .-> MLFQ
    MLFQ -- "`**融合**`" --> RR
    MLFQ -- "`**融合**`" --> PRIORITY
    PROCESS -- "`**通过**`" --> IPC
    IPC -- "`**字节流**`" --> PIPE
    IPC -- "`**离散消息**`" --> MSGQ
    IPC -- "`**共享映射传递数据**`" --> SHM
    IPC -- "`**异步通知**`" --> SIGNAL
    IPC -- "`**同步互斥**`" --> SEM
    IPC -- "`**本机或跨主机通信**`" --> SOCKET
    SHM -. "`**需要同步保护**`" .-> SEM
    THREAD -- "`**同地址空间协作**`" --> THREAD_SYNC
    THREAD_SYNC -- "`**互斥**`" --> MUTEX
    THREAD_SYNC -- "`**条件等待**`" --> COND
    THREAD_SYNC -- "`**计数同步**`" --> LOCK_SEM

    RACE -- "`**发生于**`" --> CRITICAL
    CRITICAL -- "`**需要**`" --> ATOMIC
    CRITICAL -- "`**可用**`" --> MUTEX
    CRITICAL -- "`**可用**`" --> SPIN
    CRITICAL -- "`**读多写少**`" --> RWLOCK
    SPIN -- "`**可用CAS等原子操作实现**`" --> CAS
    SPIN -- "`**忙等待优化**`" --> PAUSE
    MUTEX -. "`**竞争时可阻塞，需权衡等待长度**`" .-> LOCK_CHOICE
    SPIN -. "`**忙等待，适合短且不阻塞的临界区**`" .-> LOCK_CHOICE
    RWLOCK -. "`**取决于读写比例**`" .-> LOCK_CHOICE
    COND -- "`**原子释放锁并等待**`" --> MUTEX
    LOCK_SEM -- "`**控制资源计数**`" --> CRITICAL
    DEADLOCK -- "`**必要条件 1**`" --> D_MUTEX
    DEADLOCK -- "`**必要条件 2**`" --> D_HOLD
    DEADLOCK -- "`**必要条件 3**`" --> D_NO_PREEMPT
    DEADLOCK -- "`**必要条件 4**`" --> D_CYCLE
    ORDERING -- "`**破坏**`" --> D_CYCLE
    BANKER -- "`**预执行安全性检查**`" --> SAFE_SEQ
    SAFE_SEQ -. "`**存在则可安排完成并避免死锁**`" .-> DEADLOCK
    OPTIMISTIC -- "`**冲突检测**`" --> CAS
    OPTIMISTIC -. "`**适合读多写少**`" .-> LOCK_CHOICE
    PESSIMISTIC -- "`**提前占有**`" --> MUTEX
    PESSIMISTIC -. "`**适合冲突频繁**`" .-> LOCK_CHOICE

    VM -- "`**映射到**`" --> PM
    VM -- "`**利用**`" --> LOCALITY
    VM -- "`**实现方式**`" --> PAGING
    VM -- "`**另一种方式**`" --> SEGMENT
    VM -- "`**形成**`" --> LAYOUT
    PAGING -- "`**维护映射**`" --> PAGE_TABLE
    PAGING -- "`**固定页产生**`" --> FRAGMENT
    SEGMENT -- "`**可变段产生**`" --> FRAGMENT
    PAGE_TABLE -- "`**可组织为**`" --> MULTILEVEL
    MMU -- "`**查询**`" --> TLB
    TLB -- "`**未命中再查**`" --> PAGE_TABLE
    MMU -- "`**执行**`" --> TRANSLATE
    PAGE_TABLE -- "`**提供 PFN 与权限**`" --> TRANSLATE
    TRANSLATE -- "`**得到**`" --> PM
    TRANSLATE -. "`**映射缺失或权限错误**`" .-> PAGE_FAULT
    PAGE_FAULT -- "`**可恢复时建立或修复映射**`" --> PAGE_TABLE
    LAYOUT -- "`**动态区**`" --> HEAP
    LAYOUT -- "`**调用区**`" --> STACK
    LAYOUT -- "`**映射区**`" --> MMAP
    MALLOC -- "`**小块常用**`" --> BRK
    MALLOC -- "`**大块常用**`" --> MMAP
    BRK -- "`**扩展**`" --> HEAP
    FORK -- "`**复制**`" --> PAGE_TABLE
    FORK -- "`**启用**`" --> COW
    COW -- "`**共享**`" --> PM
    COW -- "`**写入触发**`" --> WRITE_FAULT
    WRITE_FAULT -- "`**必要时复制，独占时可复用页**`" --> PM
    WRITE_FAULT -- "`**更新**`" --> PAGE_TABLE
    PRESSURE -- "`**按水位等条件唤醒**`" --> KSWAPD
    PRESSURE -- "`**分配路径可能同步回收**`" --> DIRECT
    PRESSURE -- "`**可回收**`" --> FILE_PAGE
    PRESSURE -- "`**可换出**`" --> ANON_PAGE
    FILE_PAGE -- "`**按活跃度选择**`" --> LRU_LIST
    ANON_PAGE -- "`**按活跃度选择**`" --> LRU_LIST
    ANON_PAGE -- "`**写入**`" --> SWAP
    DIRECT -. "`**仍不足且符合OOM策略时**`" .-> OOM
    PAGE_FAULT -- "`**需分配页且内存紧张时可能触发**`" --> REPLACE
    REPLACE -- "`**理论最优**`" --> OPT
    REPLACE -- "`**按进入顺序**`" --> FIFO
    REPLACE -- "`**按最近访问**`" --> LRU
    REPLACE -- "`**近似 LRU**`" --> CLOCK
    REPLACE -- "`**按访问频率**`" --> LFU
    CLOCK -. "`**同为近似回收思路而非同一算法**`" .-> LRU_LIST

    INTERRUPT -- "`**来源之一**`" --> EXTERNAL
    INTERRUPT -- "`**来源之一**`" --> INTERNAL
    EXTERNAL -- "`**普通设备**`" --> MASKABLE
    EXTERNAL -- "`**严重事件**`" --> NMI
    INTERNAL -- "`**有意事件**`" --> TRAP
    INTERNAL -- "`**可恢复异常**`" --> FAULT
    INTERNAL -- "`**不可恢复异常**`" --> ABORT
    INTERRUPT -- "`**步骤 1**`" --> SAVE
    INTERRUPT -- "`**步骤 2 查入口**`" --> VECTOR
    VECTOR -- "`**定位**`" --> ISR
    ISR -- "`**处理完成**`" --> IRET
    IRET -- "`**使用此前保存的现场**`" --> SAVE
    TIMER -- "`**属于**`" --> MASKABLE
    TIMER -- "`**触发**`" --> PREEMPT
    SYSCALL -. "`**受控入口不等同于x86 Trap异常**`" .-> TRAP
    PAGE_FAULT -- "`**属于**`" --> FAULT
    INTERRUPT -. "`**替代持续查询**`" .-> POLLING
    INTERRUPT -- "`**带来**`" --> RESPONSIVENESS

    IO_MODEL -- "`**模型 1**`" --> BLOCKING
    IO_MODEL -- "`**模型 2**`" --> NONBLOCK
    IO_MODEL -- "`**模型 3**`" --> MULTIPLEX
    IO_MODEL -- "`**模型 4**`" --> SIGIO
    IO_MODEL -- "`**模型 5**`" --> AIO
    SERVER_ARCH -- "`**简单基线**`" --> SINGLE_SERVER
    SERVER_ARCH -- "`**每任务并行**`" --> THREAD_SERVER
    SERVER_ARCH -- "`**少量执行单元**`" --> EVENT_SERVER
    SERVER_ARCH -- "`**I/O 与计算分工**`" --> HYBRID_SERVER
    MULTIPLEX -- "`**支撑**`" --> EVENT_SERVER
    MULTIPLEX -- "`**接口**`" --> SELECT
    MULTIPLEX -- "`**接口**`" --> POLL
    MULTIPLEX -- "`**Linux 高并发接口**`" --> EPOLL
    SELECT -. "`**固定集合且 O(n)**`" .-> POLL
    POLL -. "`**动态集合但仍 O(n)**`" .-> EPOLL
    EPOLL -- "`**关注集合**`" --> EPOLL_RBTREE
    EPOLL -- "`**返回活跃事件**`" --> READY_LIST
    EPOLL -- "`**触发模式**`" --> LT
    EPOLL -- "`**触发模式**`" --> ET
    ET -- "`**通常搭配**`" --> NONBLOCK
    ET -- "`**要求**`" --> DRAIN
    LT -. "`**持续提醒，更易编程**`" .-> ET
    EPOLL -- "`**缓解**`" --> C10K
    REACTOR -- "`**基于**`" --> MULTIPLEX
    REACTOR -- "`**耗时业务可委派有界工作池**`" --> HYBRID_SERVER
    REDIS -- "`**采用**`" --> REACTOR
    NETTY -- "`**采用**`" --> REACTOR
    NGINX -- "`**采用**`" --> REACTOR
    TRADITIONAL_IO -- "`**设备拷贝由**`" --> DMA
    TRADITIONAL_IO -. "`**用户态与内核态往返多**`" .-> ZERO_COPY
    ZERO_COPY -- "`**典型系统调用**`" --> SENDFILE
    SENDFILE -- "`**内核内传输并使用**`" --> DMA

    SYSCALL -. "`**引发用户态/内核态切换成本**`" .-> IO_MODEL
    CONTEXT -. "`**切换成本限制线程扩张**`" .-> THREAD_SERVER
    SOCKET -. "`**网络端点表现为 FD**`" .-> MULTIPLEX
    SIGNAL -. "`**可实现**`" .-> SIGIO
    SHM -. "`**依赖虚拟内存映射**`" .-> VM
    MMAP -. "`**可建立共享映射**`" .-> SHM
    KERNEL -. "`**统一管理**`" .-> SCHEDULER
    KERNEL -. "`**统一管理**`" .-> VM
    KERNEL -. "`**统一处理**`" .-> INTERRUPT
    RACE -. "`**多线程服务器需防护**`" .-> THREAD_SERVER
    PAGE_FAULT -. "`**处理时进入内核**`" .-> MODE_SWITCH
    BLOCKED -. "`**等待时让出CPU并触发调度**`" .-> SCHEDULER
    READY_LIST -. "`**减少无效扫描**`" .-> RESPONSIVENESS

    %% Same-source outgoing edges use ordinal colors; cross-section relations use 3px.
    linkStyle 0,61,182,183,185,192 stroke:#2563eb,stroke-width:3px;
    linkStyle 1,62,143,144,184,189 stroke:#0f766e,stroke-width:3px;
    linkStyle 2,63,145,180,181,186 stroke:#16a34a,stroke-width:3px;
    linkStyle 3,187,190 stroke:#ca8a04,stroke-width:3px;
    linkStyle 4,188 stroke:#ea580c,stroke-width:3px;
    linkStyle 5 stroke:#dc2626,stroke-width:3px;
    linkStyle 6,9,10,11,12,13,15,16,20,24,26,27,28,29,31,32,34,35,36,37,40,41,48,49,50,53,59,64,65,69,71,73,74,75,76,80,81,82,83,85,87,92,94,95,96,97,100,102,103,106,108,109,111,113,115,119,120,122,124,129,130,132,134,139,140,141,142,148,153,157,161,162,163,167,169,171,173,174,175,176,178,179 stroke:#2563eb,stroke-width:2px;
    linkStyle 7,14,17,21,25,30,33,38,42,51,54,66,70,77,84,86,88,93,98,99,101,104,107,110,112,114,116,121,123,125,131,133,135,149,154,158,164,168,172,177,191 stroke:#0f766e,stroke-width:2px;
    linkStyle 8,18,22,39,43,55,67,72,78,89,105,117,126,136,137,150,155,159,165 stroke:#16a34a,stroke-width:2px;
    linkStyle 19,23,44,56,68,79,90,118,127,138,151,156,160,166 stroke:#ca8a04,stroke-width:2px;
    linkStyle 45,52,57,60,91,128,146,152,170 stroke:#ea580c,stroke-width:2px;
    linkStyle 46,58,147 stroke:#dc2626,stroke-width:2px;
    linkStyle 47 stroke:#c026d3,stroke-width:2px;

    classDef root fill:#111827,stroke:#f59e0b,color:#fff,stroke-width:3px;
    classDef mode fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef process fill:#ccfbf1,stroke:#0f766e,color:#134e4a;
    classDef lock fill:#dcfce7,stroke:#16a34a,color:#14532d;
    classDef memory fill:#fef3c7,stroke:#ca8a04,color:#713f12;
    classDef interrupt fill:#ffedd5,stroke:#ea580c,color:#7c2d12;
    classDef io fill:#fee2e2,stroke:#dc2626,color:#7f1d1d;
    classDef cross fill:#e0e7ff,stroke:#4f46e5,color:#312e81;

    class ROOT root;
    class MODE,USER,KERNEL,KFUNC,SYSCALL,MODE_SWITCH,PROTECTION,APP mode;
    class PROCESS,THREAD,COROUTINE,PROC_RESOURCE,SHARED,PRIVATE,PCB,TCB,ISOLATION,CONCURRENCY,THREAD_TRADEOFF,CONTEXT,PROC_SWITCH,THREAD_SWITCH,READY,RUNNING,BLOCKED,CREATED,TERMINATED,SCHEDULER,PREEMPT,FCFS,SJF,HRRN,RR,PRIORITY,MLFQ,IPC,PIPE,MSGQ,SHM,SIGNAL,SEM,SOCKET,THREAD_SYNC process;
    class RACE,CRITICAL,ATOMIC,MUTEX,SPIN,CAS,PAUSE,RWLOCK,COND,LOCK_SEM,LOCK_CHOICE,DEADLOCK,D_MUTEX,D_HOLD,D_NO_PREEMPT,D_CYCLE,ORDERING,BANKER,SAFE_SEQ,OPTIMISTIC,PESSIMISTIC lock;
    class VM,PM,LOCALITY,PAGING,PAGE_TABLE,MULTILEVEL,MMU,TLB,TRANSLATE,PAGE_FAULT,SEGMENT,FRAGMENT,LAYOUT,HEAP,STACK,MALLOC,BRK,MMAP,FORK,COW,WRITE_FAULT,PRESSURE,KSWAPD,DIRECT,FILE_PAGE,ANON_PAGE,SWAP,LRU_LIST,OOM,REPLACE,OPT,FIFO,LRU,CLOCK,LFU memory;
    class INTERRUPT,EXTERNAL,MASKABLE,NMI,INTERNAL,TRAP,FAULT,ABORT,VECTOR,SAVE,ISR,IRET,TIMER,POLLING,RESPONSIVENESS interrupt;
    class IO_MODEL,BLOCKING,NONBLOCK,MULTIPLEX,SIGIO,AIO,SERVER_ARCH,SINGLE_SERVER,THREAD_SERVER,EVENT_SERVER,HYBRID_SERVER,SELECT,POLL,EPOLL,EPOLL_RBTREE,READY_LIST,LT,ET,DRAIN,REACTOR,REDIS,NETTY,NGINX,TRADITIONAL_IO,DMA,ZERO_COPY,SENDFILE,C10K io;
    class KERNEL,SYSCALL,CONTEXT,PAGE_FAULT,INTERRUPT,MULTIPLEX cross;
```

## 六条复习主线

1. **权限边界**：`用户程序 -> 系统调用 -> 特权级切换 -> 内核服务 -> 返回用户态`。
2. **任务运行**：`进程/线程 -> 就绪队列 -> 调度器 -> 运行 -> 阻塞/抢占 -> 再次就绪`。
3. **并发正确性**：`共享状态 -> 竞态 -> 临界区 -> Mutex/Spin/RWLock/Semaphore -> 死锁治理`。
4. **地址翻译**：`虚拟地址 -> TLB/页表 -> MMU -> 物理页；映射缺失 -> Page Fault -> 调页/置换`。
5. **异步事件**：设备/异常触发中断机制，保存执行现场并通过中断向量表定位“中断处理程序 ISR”，处理后恢复现场并返回。
6. **高性能网络**：`Socket FD -> epoll -> Reactor -> 少量线程处理多连接 -> sendfile/DMA 减少拷贝`。

## 易混概念

| 概念 | 核心区别 |
| --- | --- |
| 用户态与内核态 | 是 CPU 权限模式，不是两个独立 CPU；系统调用和异常会触发受控切换。 |
| 进程、线程、协程 | 进程拥有隔离资源；线程是内核调度单元并共享进程资源；协程通常由用户态运行时调度。 |
| 信号与信号量 | 信号传递异步事件；信号量是计数同步原语，通过 P/V 控制资源访问。 |
| 并发与并行 | 并发是多个任务在时间上推进；并行是多个任务在不同 CPU 核上同时执行。 |
| 自旋锁与互斥锁 | 自旋锁忙等而不主动睡眠，但仍受抢占等条件影响；互斥锁可先走快速路径或自旋，再按实现阻塞。 |
| 分页与分段 | 分页按固定物理管理单位划分；分段按程序逻辑划分，分别产生内部与外部碎片。 |
| 缺页与 OOM | 缺页是地址翻译异常，通常可修复；OOM 是回收仍无法满足内存申请后的生存决策。 |
| 阻塞/非阻塞与同步/异步 | 前者描述调用是否等待；后者描述完成工作和通知由谁负责，不能简单画等号。 |
| select/poll/epoll | select/poll 每次传递并扫描关注集合；epoll 在内核维护集合并返回就绪项。 |
| LT 与 ET | LT 在就绪条件持续时可反复通知；ET 不保证未处理完的数据会再次提醒，通常配合非阻塞读写和就绪状态管理，不等同于 EPOLLONESHOT。 |
| Reactor 与异步 I/O | Reactor 分发“就绪事件”，应用执行 I/O；真正 AIO 通知的是内核已完成 I/O。 |
| 零拷贝 | 不是绝对零次数据搬运，而是减少用户态往返和 CPU 参与的拷贝。 |

## 原文章节索引

| 编号 | 章节 | 重点 |
| --- | --- | --- |
| 01 | 用户态和内核态 | 权限、系统调用、隔离与保护 |
| 02 | 进程管理 | 进程/线程/协程、上下文、状态、IPC、同步与调度算法 |
| 03 | 锁 | 竞态、锁类型、CAS、死锁条件、避免策略与银行家算法 |
| 04 | 内存管理 | 虚拟内存、页表、地址翻译、布局、COW、回收与页面置换 |
| 05 | 中断 | 类型、响应流程、向量表、异常与 CPU 利用率 |
| 06 | 网络 I/O | 五种 I/O 模型、并发服务器、epoll、Reactor 与零拷贝 |

## 面试准备复习资料

### 1. 分层阅读与整体认知

入口：[节点速查](#review-nodes)、[面试问答](#review-questions)、[贯穿案例与代码](#review-example)、[逐边关系索引](#review-edge-index)。图中的“操作系统全景”不是一条线性调用链，而是隔离、执行、同步、地址映射、异步事件与 I/O 的关系网。

**一分钟回答：** 用户程序在受限权限下运行，通过系统调用使用内核资源；进程隔离资源，线程承载执行，调度器决定谁用 CPU；共享可变数据需要同步；虚拟地址通过硬件与页表映射到物理内存，缺页由内核按原因处理；中断把设备事件接回系统，I/O 模型与 Reactor 决定大量连接怎样被少量执行单元处理。

**贯穿例子：** 一个 HTTP 请求到达服务器，设备/网络栈使 Socket 就绪，多路复用唤醒等待线程；调度器安排执行，程序解析数据、访问缓存时涉及页表与锁，再通过写调用或文件发送返回。不是每次请求都会缺页、切进程或调用 sendfile；图的边表示可能关系与职责。

<a id="review-nodes"></a>

### 2. 节点速查

#### 2.1 用户态与内核态

| 图中节点 | 是什么、连接什么 |
| --- | --- |
| **CPU 运行模式** | 硬件权限等级，约束能执行的指令与资源访问。 |
| **用户态 User Mode** | 受限执行普通应用，不可任意访问内核地址和设备。 |
| **内核态 Kernel Mode** | 可执行特权操作，仍受硬件、虚拟化与内核策略约束。 |
| **内核核心职责** | 管任务、内存、设备、文件与相关抽象。 |
| **系统调用 System Call** | 应用受控请求内核服务的接口。 |
| **特权级切换** | 保存必要现场、进入内核并返回，不必然更换运行任务。 |
| **保护目标** | 防越权、故障扩散和资源破坏。 |
| **用户程序** | 通过库和系统调用请求服务，库函数未必每次都系统调用。 |

#### 2.2 任务、调度与通信

| 图中节点 | 是什么、连接什么 |
| --- | --- |
| **进程 Process** | 资源与隔离的抽象，拥有地址空间及相关资源。 |
| **线程 Thread** | 内核调度的执行上下文，同进程线程通常共享资源。 |
| **协程 Coroutine** | 用户运行时调度的执行单元，最终仍依附线程。 |
| **进程资源** | 地址空间、FD、信号、凭据等，具体共享方式依创建参数。 |
| **同进程线程共享** | 代码、数据、堆、打开文件等，不等于并发访问自动安全。 |
| **线程私有上下文** | 寄存器、PC、栈与 TLS；同进程仍可能通过指针访问其他线程栈。 |
| **PCB** | 进程状态和资源的概念记录，不要求 Linux 有独立同名结构。 |
| **TCB** | 线程执行状态的概念记录；Linux 用统一任务结构表达许多职责。 |
| **进程隔离** | 通过地址空间与权限限制故障和数据访问。 |
| **并发与并行** | 时间上都在推进与同一时刻多核执行的区别。 |
| **多线程权衡** | 吞吐和响应收益，换取竞争、内存与调度开销。 |
| **CPU 上下文** | 恢复执行所需的寄存器、PC、栈指针等。 |
| **进程切换** | 保存执行现场并可能切地址空间，代价依实现与缓存。 |
| **线程切换** | 同地址空间时可少改映射，仍有调度和缓存成本。 |
| **就绪态 Ready** | 除 CPU 外满足运行条件，在等调度。 |
| **运行态 Running** | 正在某 CPU 上执行。 |
| **阻塞态 Blocked** | 等 I/O、锁或事件，不能因有空闲 CPU 就直接运行。 |
| **创建态 New** | 任务初始化阶段。 |
| **结束态 Terminated** | 执行结束，资源回收可能有后续阶段。 |
| **调度器 Scheduler** | 在可运行任务中选择执行者，不替设备完成 I/O。 |
| **抢占 / 时间片** | 通过调度时机收回 CPU，时钟是重要来源之一。 |
| **FCFS** | 按到达顺序，长任务可能造成 convoy effect。 |
| **SJF** | 优先短作业，需服务时间估计，长任务可能饥饿。 |
| **HRRN** | 响应比考虑等待与服务时间，缓解只偏爱短任务。 |
| **Round Robin** | 时间片轮转，片长影响响应与切换开销。 |
| **优先级调度** | 按静态/动态优先级选择，需防饥饿与优先级反转。 |
| **多级反馈队列 MLFQ** | 分级时间片与反馈；需提升/老化策略才可缓解饥饿。 |
| **进程间通信 IPC** | 跨资源隔离边界交换数据与事件。 |
| **管道 Pipe** | 常见为内核维护的字节流，匿名/命名语义和双向性依平台。 |
| **消息队列** | 以消息边界组织通信，容量和持久性依接口。 |
| **共享内存** | 不同地址映射共同物理内容，仍需同步与生命周期治理。 |
| **信号 Signal** | 异步事件通知，不是计数同步原语。 |
| **信号量 Semaphore** | 计数同步与资源限制，可用于跨进程。 |
| **Socket** | 本机或跨主机通信端点，不都意味着 TCP。 |
| **线程同步与通信** | 使用共享地址空间配合锁、条件变量等协调。 |

#### 2.3 并发、锁与死锁

| 图中节点 | 是什么、连接什么 |
| --- | --- |
| **竞态条件 Race Condition** | 结果依赖不受控的执行交错。 |
| **临界区 Critical Section** | 需要保护共享状态不变量的操作范围。 |
| **原子性 / 可见性 / 有序性** | 操作不可分、写入可见与允许的重排是不同维度。 |
| **互斥锁 Mutex** | 排他访问，快速路径、自旋与阻塞策略依实现。 |
| **自旋锁 Spinlock** | 忙等待换取避免睡眠切换，不能任意持锁做阻塞操作。 |
| **CAS 原子指令** | 比较当前值并在匹配时更新，内存序和 ABA 要另行考虑。 |
| **PAUSE 指令** | 某些架构的忙等待提示，不是自动释放锁或通用跨平台指令。 |
| **读写锁** | 多读共享、写独占，公平性与额外维护成本需考虑。 |
| **条件变量** | 等待条件变化的通知机制，配合 Mutex 和谓词循环。 |
| **信号量** | 此处指锁分区的计数资源控制，和信号完全不同。 |
| **锁选型** | 看临界区时长、竞争、读写比例、CPU 与阻塞限制。 |
| **死锁 Deadlock** | 一组任务永久等待彼此持有的资源。 |
| **互斥条件** | 资源同一时刻不能被共享使用。 |
| **持有并等待** | 持有资源同时申请其他资源。 |
| **不可剥夺** | 已持资源不能被外部强制安全拿走。 |
| **环路等待** | 等待依赖形成环；单/多实例资源的判定条件要区分。 |
| **资源有序分配** | 统一资源申请顺序，破坏环路等待。 |
| **银行家算法** | 已知最大需求等前提下，分配前检查安全性。 |
| **安全序列** | 存在按当前可用资源让任务依次完成的顺序。 |
| **乐观锁** | 先读改，提交时用版本/CAS 检测冲突并重试。 |
| **悲观锁** | 访问前排他或加锁，限制冲突并发。 |

#### 2.4 虚拟内存与回收

| 图中节点 | 是什么、连接什么 |
| --- | --- |
| **虚拟内存** | 提供进程地址空间、保护和映射，不等于磁盘 Swap。 |
| **物理内存** | 实际页框等 RAM 资源。 |
| **局部性原理** | 近期访问和相邻访问常重复出现，是缓存与回收的重要依据。 |
| **分页 Paging** | 虚拟页映射物理页框，偏移保持不变。 |
| **页表 Page Table** | 保存映射、权限、存在与访问状态。 |
| **多级页表** | 稀疏地址空间按需配置层级，节省页表空间但增加遍历成本。 |
| **MMU** | 硬件地址翻译与访问检查。 |
| **TLB** | 缓存地址映射相关信息，不缓存应用数据本身。 |
| **地址转换** | 页号翻译后拼页内偏移；命中 TLB 不必遍历完整页表。 |
| **缺页异常 Page Fault** | 不存在、写保护或权限等异常，按原因可恢复或终止/报错。 |
| **分段 Segmentation** | 基址、界限与段语义；现代常见系统多以分页为主。 |
| **内存碎片** | 固定页/块有内部浪费，可变连续分配可能有外部碎片。 |
| **进程虚拟内存布局** | 代码、数据、BSS、堆、映射、栈，地址与方向不是所有平台固定。 |
| **堆 Heap** | 动态分配区域及分配器管理，不等于每次 malloc 都扩堆。 |
| **栈 Stack** | 调用帧与局部存储，方向、大小和逃逸优化依 ABI/运行时。 |
| **malloc** | 用户态分配器接口，可能重用缓存、arena 或请求新映射。 |
| **brk** | 调整程序 break 的机制，不是所有 malloc 实现都会使用。 |
| **mmap** | 文件或匿名映射，可用于共享与大块分配，不能一律称“更快”。 |
| **fork** | 建立子进程并继承资源语义，具体内核工作与页表处理依实现。 |
| **Copy-on-Write** | 共享页面直到写入需要私有副本，减少初始复制。 |
| **写保护异常** | COW 写入的一种触发来源，不能将所有写保护都当作 COW。 |
| **内存压力** | 可用页不足或达到限制，需要回收、回写或其他策略。 |
| **后台回收 kswapd** | Linux 后台回收机制之一，不是所有 OS 都采用该线程。 |
| **直接回收 Direct Reclaim** | 分配路径同步回收，可能提高请求尾延迟。 |
| **文件页** | 干净可丢后重读，脏页通常需要回写或按映射语义处理。 |
| **匿名页** | 堆栈等非文件直接后备页，换出依赖可用 Swap 与配置。 |
| **Swap** | 匿名内存等的交换后备，不是虚拟内存的全部。 |
| **Active / Inactive LRU 链表** | Linux 回收的一类实现视角；新版本可能启用多代 LRU 等机制。 |
| **OOM Killer** | 特定内存不足策略下选择牺牲任务，不是任何分配失败都必然触发。 |
| **页面置换** | 需要腾出页框时选择牺牲页，真实回收不只在缺页时发生。 |
| **OPT** | 用未来访问选择最晚再用页，只作理想基准。 |
| **FIFO** | 淘汰最早进入的页，可能有 Belady 异常。 |
| **LRU** | 淘汰最近最久未访问页，精确维护有成本。 |
| **Clock** | 用访问位与环形扫描近似近期使用，不等于 Linux 两链表算法。 |
| **LFU** | 按访问频率选择，需处理老热点和计数衰减。 |

#### 2.5 中断与异常

| 图中节点 | 是什么、连接什么 |
| --- | --- |
| **中断机制** | 响应事件、保存与恢复执行的机制族。 |
| **外部中断 / 硬件中断** | 设备等异步事件来源。 |
| **可屏蔽中断 INTR** | 可按中断控制机制屏蔽的事件，具体架构术语不同。 |
| **不可屏蔽中断 NMI** | 常规屏蔽机制不能屏蔽，亦可用于诊断，不都意味着致命错误。 |
| **内部中断** | 教学分类中的同步异常/主动事件，需与外部中断区分。 |
| **陷阱 Trap** | x86 典型分类中返回后继续下一条指令。 |
| **故障 Fault** | 某些情况可修复后重试原指令，如按需缺页。 |
| **终止 Abort** | 严重且不能可靠恢复的异常类别。 |
| **中断向量表** | 从事件编号定位入口，具体结构随架构。 |
| **保存执行现场** | 保存恢复所需状态，硬件和软件分工依实现。 |
| **中断处理程序 ISR** | 确认并处理事件，可能把大量工作下放到后续机制。 |
| **恢复现场并返回** | 恢复保存的状态；返回前也可能发生调度。 |
| **时钟中断** | 提供计时和调度机会，不代表每次都换任务。 |
| **轮询 Polling** | 主动查看设备状态，高负载下可与中断混合。 |
| **中断价值** | 减少无效等待、改善响应，但中断风暴也有代价。 |

#### 2.6 网络 I/O 与服务器

| 图中节点 | 是什么、连接什么 |
| --- | --- |
| **Unix 网络 I/O 模型** | 以等待就绪与数据传递阶段比较不同接口。 |
| **阻塞 I/O** | 调用可能等待条件与完成，线程期间不推进后续用户代码。 |
| **非阻塞 I/O** | 不能立即操作时返回 EAGAIN 等，不等于操作已异步完成。 |
| **I/O 多路复用** | 等待多个 FD 的就绪，数据读写仍另行执行。 |
| **信号驱动 I/O** | 通过信号告知就绪，应用仍需处理数据。 |
| **异步 I/O** | 提交后通过完成语义取得结果，具体接口支持有边界。 |
| **并发服务器架构** | 按连接、I/O、CPU 和状态共享需求组织执行单元。 |
| **单线程逐请求** | 简单串行基线，慢请求影响其他请求。 |
| **多进程/多线程** | 任务隔离或并行，代价是资源、同步和切换。 |
| **事件驱动服务器** | 少量线程管理多连接，不把慢业务塞满事件线程。 |
| **多路复用 + 工作线程/进程池** | I/O 就绪分发与耗时计算分离。 |
| **select** | fd_set 与集合扫描，常见 FD_SETSIZE 限制依平台。 |
| **poll** | 关注集合为动态数组等形式，仍需扫描相关集合。 |
| **epoll** | Linux 内核维护关注关系并返回就绪项，非所有工作都常数时间。 |
| **红黑树** | 常见 Linux epoll 关注集合实现细节，非接口对实现的永久承诺。 |
| **就绪链表** | 常见实现管理待上报事件，不必遍历全部空闲连接。 |
| **水平触发 LT** | 就绪条件持续时继续通知，仍要处理竞争与短读。 |
| **边缘触发 ET** | 关注就绪状态变化，需正确排空并避免遗漏。 |
| **非阻塞读写到 EAGAIN** | 用循环排空/写尽或达到不能继续状态，同时注意公平性。 |
| **Reactor** | 分发就绪事件，Handler 执行 I/O，与完成通知模型不同。 |
| **Redis 6.0 前** | 历史典型 Reactor 主路径，后台任务与后续版本不能忽略。 |
| **Netty** | 常见多 Reactor 多线程设计，业务执行仍要隔离阻塞操作。 |
| **Nginx** | 常见多进程事件驱动与连接分配设计，细节依版本/配置。 |
| **传统文件传输** | read/write 典型路径含模式往返与多次搬运，次数依条件。 |
| **DMA** | 设备与内存间传输，减少 CPU 逐字节复制，CPU 仍会协调。 |
| **零拷贝** | 减少 CPU 拷贝和用户态往返，不是数据完全不移动。 |
| **sendfile** | 文件到 Socket 等内核路径优化，文件系统/设备/TLS 条件会影响。 |
| **C10K** | 高连接数的事件、内存、FD 和调度问题，不只一个 API 问题。 |

<a id="review-questions"></a>

### 3. 面试问答

#### Q01：用户态与内核态为什么要分开？

**面试回答：** 用硬件权限与受控入口保护资源，防止应用任意改内存、设备和调度状态。系统调用是请求服务的接口，普通函数调用未必进入内核。内核态也不是超越虚拟化和硬件限制的“绝对权限”。

#### Q02：系统调用一定发生进程切换吗？

**面试回答：** 不一定。用户到内核是特权级/模式切换，同一线程可以处理完立即返回；进程/线程切换才是调度另一个执行上下文。阻塞、抢占等可能导致后者，不能把每次 syscall 都计成两个任务切换。

#### Q03：进程、线程、协程怎样用一个服务器例子解释？

**面试回答：** 进程提供地址空间和资源隔离；线程承载内核调度与多核执行；协程由用户运行时挂起/恢复，最终在线程上运行。协程遇到未适配的阻塞系统调用可能堵住所在线程，轻量不等于无成本或自动并行。

#### Q04：线程共享什么，私有栈就完全不可访问吗？

**面试回答：** 同进程通常共享代码、数据、堆和打开文件，有各自寄存器、栈与 TLS。私有指每线程有独立栈使用与生命周期，不是同地址空间内硬件隔离；拿到有效地址仍可能访问，因此对象生命周期与同步仍重要。

#### Q05：就绪、运行、阻塞怎样转换？

**面试回答：** 就绪获得 CPU 后运行，运行等待 I/O/锁进入阻塞，事件满足后回就绪再等待调度；时间片耗尽也可能运行转就绪。设备完成不代表程序立刻获得 CPU，忙轮询也不等同于被内核挂起。

#### Q06：调度算法怎么比较，不背缩写怎样回答？

**面试回答：** 按响应、周转、吞吐、公平、估计信息和切换成本比较。FCFS 简单但长任务拖延；SJF 偏短且依赖估计；HRRN 引入等待；RR 由片长权衡响应与开销；优先级需防饥饿；MLFQ 用多级反馈但仍需老化/提升策略。真实 Linux 调度不是直接套一张教学算法表。

#### Q07：共享内存为什么快，又为什么危险？

**面试回答：** 建好映射后可避免某些消息传递的数据复制，但映射建立、同步、缓存一致性和争用仍有成本。多个进程直接访问相同内容，必须定义数据布局、同步、可见性和崩溃恢复；不能只说“最快所以全用共享内存”。

#### Q08：信号、信号量、条件变量有什么区别？

**面试回答：** 信号通知异步事件；信号量是有计数的资源/同步原语；条件变量通知某个受锁保护的谓词可能变化。条件变量不保存“条件为真”这个事实，等待前后必须在锁内检查谓词并用 while 处理虚假唤醒和竞争。

#### Q09：一个自增为什么可能有竞态？

**面试回答：** 读、修改、写可能交错，两线程基于相同旧值更新造成丢失。原子性、可见性、顺序是不同要求；仅 volatile 等语言机制不能普遍提供互斥。选原子或锁要看要维护的是单值还是跨多个字段的不变量。

#### Q10：Mutex 与 Spinlock 怎么选？

**面试回答：** 看等待长度、竞争、CPU 和能否睡眠。短且不会阻塞的内核临界区可考虑自旋；长等待更适合阻塞。实际 Mutex 常有用户态快速路径和适应性自旋，不能把两者实现说成绝对二分；单核、抢占与中断上下文也影响选择。

#### Q11：读写锁和乐观锁一定适合“读多写少”吗？

**面试回答：** 这只是候选信号。读写锁维护与公平成本可能超过小临界区收益，写者可能饥饿；乐观重试在高冲突下会消耗 CPU，还要处理版本、ABA 与内存序。实际按临界区、冲突率和尾延迟测量。

#### Q12：死锁四条件与安全序列怎样连接？

**面试回答：** 典型资源死锁需互斥、持有并等待、不可剥夺和环路等待；统一加锁顺序破坏环路。银行家算法在最大需求等已知假设下，试分配后寻找安全序列；不安全不等于已死锁，多实例资源的环也不总是充分判据。

#### Q13：虚拟内存为什么不只是“内存不够用磁盘”？

**面试回答：** 它首先提供地址抽象、隔离、保护与灵活映射，即使无 Swap 也有价值。页表映射虚拟页到页框，MMU 检查权限，局部性支持缓存和回收；Swap 只是某些页的后备手段，不是虚拟内存定义。

#### Q14：MMU、TLB、页表怎么完成地址转换？

**面试回答：** 地址分为页号和偏移，MMU 先利用 TLB；未命中时按架构做页表遍历或相应处理，拿到页框和权限后拼偏移。多级页表节约稀疏空间，TLB 缓解遍历成本；切地址空间、ASID/PCID 和失效策略会影响复用。

#### Q15：TLB miss、缺页、非法访问、OOM 一样吗？

**面试回答：** TLB miss 可能只是查页表，映射仍有效；缺页异常可能是按需建立映射、COW 或权限错误；非法访问可能无法修复而发信号；OOM 是资源不足策略，分配也可能先失败返回错误。缺页不都读磁盘，OOM 也不是每次缺页的结局。

#### Q16：malloc 成功就占了同样多物理内存吗？

**面试回答：** 不一定。分配器可能从已有空闲块满足，申请新虚拟区也可能按需分配页，第一次触碰才建立物理后备。实际受提交、overcommit、映射方式和系统配置影响。brk/mmap 阈值是分配器策略，不是通用 OS 常量。

#### Q17：fork 的 COW 为什么既省又可能昂贵？

**面试回答：** 初始共享页面并写保护，写入时异常触发私有复制；仍需进程元数据和相应映射处理。大量写会增加缺页与复制成本，大地址空间页表管理也有代价。exec、共享映射与特殊页还有不同语义，不等于 fork 完全零拷贝。

#### Q18：内存压力怎样影响请求 P99？

**面试回答：** 后台回收、脏页回写、Swap、直接回收和缺页都可能增加延迟，申请线程同步回收尤其影响尾延迟。文件干净页与匿名页回收路径不同；Swap 未启用时不能假设匿名页总可换出。回收算法与 OOM 策略按内核、cgroup 和配置分析。

#### Q19：OPT、FIFO、LRU、Clock、LFU 如何比较？

**面试回答：** OPT 需要未来信息，是理论参照；FIFO 简单但可能反常；LRU 用近期性但精确维护贵；Clock 用访问位近似；LFU 看频率但需防老热点。Linux Active/Inactive 或多代 LRU 是具体实现，不等于教材中的一个 Clock 环。

#### Q20：外部中断、Trap、Fault 与 Abort 有何不同？

**面试回答：** 外部中断通常异步来自设备；Trap/Fault/Abort 是典型架构对同步异常的分类，分别涉及下一指令继续、可修复重试与严重不可恢复。现代 syscall 可使用专门指令进入，不必都通过同一软件中断路径，术语需结合架构。

#### Q21：中断后一定恢复原任务吗？

**面试回答：** 保存现场、定位入口、处理并返回是一条概念路径，但返回前可能调度其他就绪任务。硬中断处理需要短、受限，耗时工作常移到后续机制；高负载可采用中断与轮询混合，不能说轮询永远低效。

#### Q22：阻塞、非阻塞、同步、异步怎样区分？

**面试回答：** 阻塞描述调用是否等待，非阻塞无法立即操作时返回；同步/异步关注操作与完成语义，不能只看返回速度。Reactor 等待就绪后由应用读写，真正完成通知模型报告操作结果；具体 API 和平台仍要区分。

#### Q23：select、poll、epoll 为什么不同？

**面试回答：** select/poll 通常每次提交关注集合并扫描，select 的 fd_set 还有常见容量限制；epoll 维护内核关注关系，返回就绪集合。大量空闲连接时可减少无效扫描，但控制操作、事件数量和竞争仍有开销，不能宣称整个 epoll 路径恒为 O(1)。

#### Q24：LT 与 ET 为什么通常都用非阻塞 I/O？

**面试回答：** 就绪后也可能被其他消费者抢走或只能完成部分操作，非阻塞防止事件线程意外挂住。ET 下通常读到 EAGAIN/EOF/错误，写到待发数据完成或 EAGAIN；若为公平性提前让出，应自行保留可继续处理状态，不能盲等新通知。ET 不等于 EPOLLONESHOT，事件可能合并，也不是“一包对应一次通知”。参见 [epoll 手册](https://man7.org/linux/man-pages/man7/epoll.7.html)。

#### Q25：Reactor 与 Proactor/完成通知的区别？

**面试回答：** Reactor 分发“可读可写”，应用 Handler 做 I/O；完成模型分发“操作已完成”的结果。它们是架构语义，不是只按库名判断；一个实现可能混用不同机制，接口可模拟异步但内部仍靠线程。

#### Q26：事件驱动为什么仍需要工作池？

**面试回答：** 多路复用减少连接等待线程，但 CPU 密集或阻塞业务仍可能堵住事件循环。将合适的工作分派给有界线程/进程池，处理队列背压和结果返回；无限线程池只是把过载换个地方。是否使用进程还要看语言运行时与共享成本。

#### Q27：Redis、Netty、Nginx 可以用一句架构公式记死吗？

**面试回答：** 可以用历史典型设计建立模型，不能代替版本事实。Redis 后续引入 I/O 多线程等能力，Netty 事件循环不等于业务随便阻塞，Nginx 的 accept 协调也有版本/配置条件。回答先给主路径，再说明背景任务与演进。

#### Q28：DMA、sendfile 和零拷贝究竟减少什么？

**面试回答：** DMA 减少 CPU 执行设备数据搬运；sendfile 可减少文件到 Socket 路径的用户态往返与 CPU 拷贝，实际仍有数据移动、页引用和设备传输。路径受文件系统、缓存、网卡、内核与 TLS 支持影响，不背固定四次拷贝就当所有情况。

#### Q29：服务器连接很多但 CPU 不高，怎样排查？

**面试回答：** 看 FD/连接限制、事件循环是否阻塞、下游等待、队列背压、内存与回收、锁竞争和调度延迟。少量 CPU 不等于系统没瓶颈，epoll 也不能解决慢数据库或内存压力。连接数量、活跃连接与请求吞吐要分开。

#### Q30：怎样把整张 OS 图讲成一件事？

**面试回答：** 从网络事件让 FD 就绪，到调度线程执行，系统调用在权限边界提供服务，用户代码通过映射访问内存、用同步保护共享状态，再发送响应；任何阻塞、缺页或竞争都可能反馈到队列与尾延迟。逐步说明每个“可能”是否真实发生，避免把所有机制强塞进每个请求。

<a id="review-example"></a>

### 4. 贯穿案例与代码：一个请求怎样读完再处理

**场景：** 小服务器收到一条题目 ID，请求事件在就绪后被读取，解析 ID，查询共享规则，再回复。真实网络会经过网卡、协议栈和调度，下面只验证应用侧就绪读取与地址翻译的逻辑，不模拟完整 TCP、内核中断或锁实现。

| 图中关系 | 在请求中意味着什么 |
| --- | --- |
| 用户程序 → 系统调用 System Call → 特权级切换 | read/write 等请求内核服务，不必发生任务切换。 |
| Socket → I/O 多路复用 | 端点以可监测 FD 等接口进入事件等待。 |
| 就绪态 Ready → 运行态 Running | 被唤醒后还要获 CPU，数据到达不等于已处理。 |
| MMU → TLB → 页表 Page Table | 用户地址按映射与权限翻译，不是直接物理地址。 |
| 临界区 Critical Section → 互斥锁 Mutex | 多请求更新共享缓存时保护不变量。 |
| 边缘触发 ET → 非阻塞读写到 EAGAIN | 循环排空并处理短读；例子仅验证读循环，不声称开启 ET。 |
| 零拷贝 → sendfile | 文件响应可按平台与协议条件优化，动态 JSON 不必强行走此路径。 |

代码使用 `selectors.DefaultSelector` 和本地 `socketpair`，不启动监听服务器，不需要 root。当前 macOS 通常选择 KqueueSelector，Linux 通常选择 EpollSelector；它们的默认触发细节不同，本例不以跨平台接口冒充 Linux ET 实测。

```python
import selectors
import socket

page_size = 4096
virtual_address = 0x1234
page_number, offset = divmod(virtual_address, page_size)
page_table = {1: 0xABC}
physical_address = page_table[page_number] * page_size + offset
assert physical_address == 0xABC234
assert 2 not in page_table
print("physical", hex(physical_address))

reader, writer = socket.socketpair()
try:
    reader.setblocking(False)
    with selectors.DefaultSelector() as selector:
        selector.register(reader, selectors.EVENT_READ)
        writer.sendall(b"q-42\n")
        events = selector.select(timeout=1)
        assert any(key.fileobj is reader for key, mask in events)
        chunks = []
        while True:
            try:
                chunk = reader.recv(2)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        message = b"".join(chunks)
        assert message == b"q-42\n"
        assert len(chunks) > 1
        print(type(selector).__name__, message.decode().strip())
finally:
    reader.close()
    writer.close()
```

预期第一行为 `physical 0xabc234`；当前 macOS 上第二行通常为 `KqueueSelector q-42`。页表是字典教学模型，不能证明真实 TLB 行为或页大小；socketpair 是本地端点，不证明网卡中断、epoll ET、TCP 可靠性或 sendfile 性能。Linux 专属机制需在 Linux 环境另测。

### 5. 复习卡与闭卷自测

**复述模板：** “权限控制入口，调度分配执行，映射抽象内存，同步保护共享不变量，中断接入事件，多路复用减少空等线程；区别就绪与完成、模式与任务切换、缓存未命中与缺页、教学算法与真实内核实现。”

1. read 返回之前一定换过进程吗？对应 Q02/Q05。
2. TLB miss 为什么可能完全不访问磁盘？对应 Q14/Q15。
3. malloc 返回成功，首次触碰却变慢，可能是哪几层？对应 Q16/Q18。
4. 条件变量为什么不能用一次 if 代替 while？对应 Q08/Q09。
5. epoll 通知可读后只读一小块就返回，在 ET 下有什么风险？对应 Q24。
6. CPU 不高但 P99 高，为何仍要看锁、回收和阻塞业务？对应 Q18/Q26/Q29。

下方逐边索引将 193 条关系逐项对照图中名称。连接上的“属于”“可采用”“约束”“对比”不能全部解释成请求先后顺序。

<a id="review-edge-index"></a>

### 6. 逐边关系索引：直接按节点名称对照总图

本节逐条列出图中关系，不再把多个目标合成一格。源节点与目标节点使用节点框首行名称；关系列对应箭头上的文字。实线可以表示流程、依赖或归属，虚线可以表示约束、影响或可选组合，必须结合具体关系阅读。

覆盖核对：143 个节点，193 条绘制连线，按端点、线型与关系文字去重后为 193 种关系。相同关系的重复绘线只解释一次，不代表要额外执行一次；不改原图的样式编号。

#### 6.1 总入口与跨区节点

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **操作系统全景** | 特权隔离 | **CPU 运行模式** | 实线 |
| **操作系统全景** | 任务抽象 | **进程 Process** | 实线 |
| **操作系统全景** | 并发正确性 | **竞态条件 Race Condition** | 实线 |
| **操作系统全景** | 地址空间 | **虚拟内存** | 实线 |
| **操作系统全景** | 异步事件 | **中断机制** | 实线 |
| **操作系统全景** | 设备与网络 | **Unix 网络 I/O 模型** | 实线 |

#### 6.2 用户态与内核态

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **CPU 运行模式** | 低权限执行 | **用户态 User Mode** | 实线 |
| **CPU 运行模式** | 高权限执行 | **内核态 Kernel Mode** | 实线 |
| **CPU 运行模式** | 划分目的 | **保护目标** | 实线 |
| **用户程序** | 请求内核服务 | **系统调用 System Call** | 实线 |
| **用户态 User Mode** | 发起 | **系统调用 System Call** | 实线 |
| **系统调用 System Call** | 触发 | **特权级切换** | 实线 |
| **特权级切换** | 进入 | **内核态 Kernel Mode** | 实线 |
| **内核态 Kernel Mode** | 执行 | **内核核心职责** | 实线 |
| **内核态 Kernel Mode** | 返回 | **用户态 User Mode** | 实线 |
| **保护目标** | 限制故障与越权 | **用户程序** | 实线 |
| **系统调用 System Call** | 受控入口不等同于x86 Trap异常 | **陷阱 Trap** | 虚线 |
| **系统调用 System Call** | 引发用户态/内核态切换成本 | **Unix 网络 I/O 模型** | 虚线 |
| **内核态 Kernel Mode** | 统一管理 | **调度器 Scheduler** | 虚线 |
| **内核态 Kernel Mode** | 统一管理 | **虚拟内存** | 虚线 |
| **内核态 Kernel Mode** | 统一处理 | **中断机制** | 虚线 |

#### 6.3 进程管理

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **进程 Process** | 拥有 | **进程资源** | 实线 |
| **进程 Process** | 由一个或多个组成 | **线程 Thread** | 实线 |
| **进程 Process** | 内核记录 | **PCB** | 实线 |
| **进程 Process** | 地址空间隔离实现 | **进程隔离** | 实线 |
| **线程 Thread** | 共享 | **同进程线程共享** | 实线 |
| **线程 Thread** | 独有 | **线程私有上下文** | 实线 |
| **线程 Thread** | 内核记录 | **TCB** | 实线 |
| **线程 Thread** | 支撑 | **并发与并行** | 实线 |
| **协程 Coroutine** | 由用户运行时调度 | **线程 Thread** | 实线 |
| **协程 Coroutine** | 避免频繁内核切换 | **线程切换** | 虚线 |
| **进程隔离** | 进程崩溃通常不传播 | **进程 Process** | 虚线 |
| **并发与并行** | 收益与代价 | **多线程权衡** | 实线 |
| **多线程权衡** | 线程过多导致切换与竞争 | **线程切换** | 虚线 |
| **CPU 上下文** | 保存在 | **PCB** | 实线 |
| **CPU 上下文** | 线程级保存在 | **TCB** | 实线 |
| **进程切换** | 保存/恢复 | **CPU 上下文** | 实线 |
| **线程切换** | 保存/恢复 | **线程私有上下文** | 实线 |
| **进程切换** | 通常开销更大 | **线程切换** | 虚线 |
| **调度器 Scheduler** | 选择 | **就绪态 Ready** | 实线 |
| **就绪态 Ready** | 获得 CPU | **运行态 Running** | 实线 |
| **运行态 Running** | 等待事件 | **阻塞态 Blocked** | 实线 |
| **阻塞态 Blocked** | 事件完成 | **就绪态 Ready** | 实线 |
| **运行态 Running** | 时间片耗尽 | **就绪态 Ready** | 实线 |
| **运行态 Running** | 完成或错误 | **结束态 Terminated** | 实线 |
| **创建态 New** | 初始化完成 | **就绪态 Ready** | 实线 |
| **抢占 / 时间片** | 驱动 | **调度器 Scheduler** | 实线 |
| **调度器 Scheduler** | 可采用 | **FCFS** | 实线 |
| **调度器 Scheduler** | 可采用 | **SJF** | 实线 |
| **调度器 Scheduler** | 可采用 | **HRRN** | 实线 |
| **调度器 Scheduler** | 可采用 | **Round Robin** | 实线 |
| **调度器 Scheduler** | 可采用 | **优先级调度** | 实线 |
| **调度器 Scheduler** | 可采用 | **多级反馈队列 MLFQ** | 实线 |
| **Round Robin** | 关键参数 | **抢占 / 时间片** | 实线 |
| **优先级调度** | 可用反馈与老化缓解饥饿 | **多级反馈队列 MLFQ** | 虚线 |
| **多级反馈队列 MLFQ** | 融合 | **Round Robin** | 实线 |
| **多级反馈队列 MLFQ** | 融合 | **优先级调度** | 实线 |
| **进程 Process** | 通过 | **进程间通信 IPC** | 实线 |
| **进程间通信 IPC** | 字节流 | **管道 Pipe** | 实线 |
| **进程间通信 IPC** | 离散消息 | **消息队列** | 实线 |
| **进程间通信 IPC** | 共享映射传递数据 | **共享内存** | 实线 |
| **进程间通信 IPC** | 异步通知 | **信号 Signal** | 实线 |
| **进程间通信 IPC** | 同步互斥 | **信号量 Semaphore** | 实线 |
| **进程间通信 IPC** | 本机或跨主机通信 | **Socket** | 实线 |
| **共享内存** | 需要同步保护 | **信号量 Semaphore** | 虚线 |
| **线程 Thread** | 同地址空间协作 | **线程同步与通信** | 实线 |
| **线程同步与通信** | 互斥 | **互斥锁 Mutex** | 实线 |
| **线程同步与通信** | 条件等待 | **条件变量** | 实线 |
| **线程同步与通信** | 计数同步 | **信号量** | 实线 |
| **CPU 上下文** | 切换成本限制线程扩张 | **多进程/多线程** | 虚线 |
| **Socket** | 网络端点表现为 FD | **I/O 多路复用** | 虚线 |
| **信号 Signal** | 可实现 | **信号驱动 I/O** | 虚线 |
| **共享内存** | 依赖虚拟内存映射 | **虚拟内存** | 虚线 |
| **阻塞态 Blocked** | 等待时让出CPU并触发调度 | **调度器 Scheduler** | 虚线 |

#### 6.4 锁、同步与死锁

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **竞态条件 Race Condition** | 发生于 | **临界区 Critical Section** | 实线 |
| **临界区 Critical Section** | 需要 | **原子性 / 可见性 / 有序性** | 实线 |
| **临界区 Critical Section** | 可用 | **互斥锁 Mutex** | 实线 |
| **临界区 Critical Section** | 可用 | **自旋锁 Spinlock** | 实线 |
| **临界区 Critical Section** | 读多写少 | **读写锁** | 实线 |
| **自旋锁 Spinlock** | 可用CAS等原子操作实现 | **CAS 原子指令** | 实线 |
| **自旋锁 Spinlock** | 忙等待优化 | **PAUSE 指令** | 实线 |
| **互斥锁 Mutex** | 竞争时可阻塞，需权衡等待长度 | **锁选型** | 虚线 |
| **自旋锁 Spinlock** | 忙等待，适合短且不阻塞的临界区 | **锁选型** | 虚线 |
| **读写锁** | 取决于读写比例 | **锁选型** | 虚线 |
| **条件变量** | 原子释放锁并等待 | **互斥锁 Mutex** | 实线 |
| **信号量** | 控制资源计数 | **临界区 Critical Section** | 实线 |
| **死锁 Deadlock** | 必要条件 1 | **互斥条件** | 实线 |
| **死锁 Deadlock** | 必要条件 2 | **持有并等待** | 实线 |
| **死锁 Deadlock** | 必要条件 3 | **不可剥夺** | 实线 |
| **死锁 Deadlock** | 必要条件 4 | **环路等待** | 实线 |
| **资源有序分配** | 破坏 | **环路等待** | 实线 |
| **银行家算法** | 预执行安全性检查 | **安全序列** | 实线 |
| **安全序列** | 存在则可安排完成并避免死锁 | **死锁 Deadlock** | 虚线 |
| **乐观锁** | 冲突检测 | **CAS 原子指令** | 实线 |
| **乐观锁** | 适合读多写少 | **锁选型** | 虚线 |
| **悲观锁** | 提前占有 | **互斥锁 Mutex** | 实线 |
| **悲观锁** | 适合冲突频繁 | **锁选型** | 虚线 |
| **竞态条件 Race Condition** | 多线程服务器需防护 | **多进程/多线程** | 虚线 |

#### 6.5 内存管理

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **虚拟内存** | 映射到 | **物理内存** | 实线 |
| **虚拟内存** | 利用 | **局部性原理** | 实线 |
| **虚拟内存** | 实现方式 | **分页 Paging** | 实线 |
| **虚拟内存** | 另一种方式 | **分段 Segmentation** | 实线 |
| **虚拟内存** | 形成 | **进程虚拟内存布局** | 实线 |
| **分页 Paging** | 维护映射 | **页表 Page Table** | 实线 |
| **分页 Paging** | 固定页产生 | **内存碎片** | 实线 |
| **分段 Segmentation** | 可变段产生 | **内存碎片** | 实线 |
| **页表 Page Table** | 可组织为 | **多级页表** | 实线 |
| **MMU** | 查询 | **TLB** | 实线 |
| **TLB** | 未命中再查 | **页表 Page Table** | 实线 |
| **MMU** | 执行 | **地址转换** | 实线 |
| **页表 Page Table** | 提供 PFN 与权限 | **地址转换** | 实线 |
| **地址转换** | 得到 | **物理内存** | 实线 |
| **地址转换** | 映射缺失或权限错误 | **缺页异常 Page Fault** | 虚线 |
| **缺页异常 Page Fault** | 可恢复时建立或修复映射 | **页表 Page Table** | 实线 |
| **进程虚拟内存布局** | 动态区 | **堆 Heap** | 实线 |
| **进程虚拟内存布局** | 调用区 | **栈 Stack** | 实线 |
| **进程虚拟内存布局** | 映射区 | **mmap** | 实线 |
| **malloc** | 小块常用 | **brk** | 实线 |
| **malloc** | 大块常用 | **mmap** | 实线 |
| **brk** | 扩展 | **堆 Heap** | 实线 |
| **fork** | 复制 | **页表 Page Table** | 实线 |
| **fork** | 启用 | **Copy-on-Write** | 实线 |
| **Copy-on-Write** | 共享 | **物理内存** | 实线 |
| **Copy-on-Write** | 写入触发 | **写保护异常** | 实线 |
| **写保护异常** | 必要时复制，独占时可复用页 | **物理内存** | 实线 |
| **写保护异常** | 更新 | **页表 Page Table** | 实线 |
| **内存压力** | 按水位等条件唤醒 | **后台回收 kswapd** | 实线 |
| **内存压力** | 分配路径可能同步回收 | **直接回收 Direct Reclaim** | 实线 |
| **内存压力** | 可回收 | **文件页** | 实线 |
| **内存压力** | 可换出 | **匿名页** | 实线 |
| **文件页** | 按活跃度选择 | **Active / Inactive LRU 链表** | 实线 |
| **匿名页** | 按活跃度选择 | **Active / Inactive LRU 链表** | 实线 |
| **匿名页** | 写入 | **Swap** | 实线 |
| **直接回收 Direct Reclaim** | 仍不足且符合OOM策略时 | **OOM Killer** | 虚线 |
| **缺页异常 Page Fault** | 需分配页且内存紧张时可能触发 | **页面置换** | 实线 |
| **页面置换** | 理论最优 | **OPT** | 实线 |
| **页面置换** | 按进入顺序 | **FIFO** | 实线 |
| **页面置换** | 按最近访问 | **LRU** | 实线 |
| **页面置换** | 近似 LRU | **Clock** | 实线 |
| **页面置换** | 按访问频率 | **LFU** | 实线 |
| **Clock** | 同为近似回收思路而非同一算法 | **Active / Inactive LRU 链表** | 虚线 |
| **缺页异常 Page Fault** | 属于 | **故障 Fault** | 实线 |
| **mmap** | 可建立共享映射 | **共享内存** | 虚线 |
| **缺页异常 Page Fault** | 处理时进入内核 | **特权级切换** | 虚线 |

#### 6.6 中断与异常

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **中断机制** | 来源之一 | **外部中断 / 硬件中断** | 实线 |
| **中断机制** | 来源之一 | **内部中断** | 实线 |
| **外部中断 / 硬件中断** | 普通设备 | **可屏蔽中断 INTR** | 实线 |
| **外部中断 / 硬件中断** | 严重事件 | **不可屏蔽中断 NMI** | 实线 |
| **内部中断** | 有意事件 | **陷阱 Trap** | 实线 |
| **内部中断** | 可恢复异常 | **故障 Fault** | 实线 |
| **内部中断** | 不可恢复异常 | **终止 Abort** | 实线 |
| **中断机制** | 步骤 1 | **保存执行现场** | 实线 |
| **中断机制** | 步骤 2 查入口 | **中断向量表** | 实线 |
| **中断向量表** | 定位 | **中断处理程序 ISR** | 实线 |
| **中断处理程序 ISR** | 处理完成 | **恢复现场并返回** | 实线 |
| **恢复现场并返回** | 使用此前保存的现场 | **保存执行现场** | 实线 |
| **时钟中断** | 属于 | **可屏蔽中断 INTR** | 实线 |
| **时钟中断** | 触发 | **抢占 / 时间片** | 实线 |
| **中断机制** | 替代持续查询 | **轮询 Polling** | 虚线 |
| **中断机制** | 带来 | **中断价值** | 实线 |

#### 6.7 网络 I/O 与高性能服务器

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **Unix 网络 I/O 模型** | 模型 1 | **阻塞 I/O** | 实线 |
| **Unix 网络 I/O 模型** | 模型 2 | **非阻塞 I/O** | 实线 |
| **Unix 网络 I/O 模型** | 模型 3 | **I/O 多路复用** | 实线 |
| **Unix 网络 I/O 模型** | 模型 4 | **信号驱动 I/O** | 实线 |
| **Unix 网络 I/O 模型** | 模型 5 | **异步 I/O** | 实线 |
| **并发服务器架构** | 简单基线 | **单线程逐请求** | 实线 |
| **并发服务器架构** | 每任务并行 | **多进程/多线程** | 实线 |
| **并发服务器架构** | 少量执行单元 | **事件驱动服务器** | 实线 |
| **并发服务器架构** | I/O 与计算分工 | **多路复用 + 工作线程/进程池** | 实线 |
| **I/O 多路复用** | 支撑 | **事件驱动服务器** | 实线 |
| **I/O 多路复用** | 接口 | **select** | 实线 |
| **I/O 多路复用** | 接口 | **poll** | 实线 |
| **I/O 多路复用** | Linux 高并发接口 | **epoll** | 实线 |
| **select** | 固定集合且 O(n) | **poll** | 虚线 |
| **poll** | 动态集合但仍 O(n) | **epoll** | 虚线 |
| **epoll** | 关注集合 | **红黑树** | 实线 |
| **epoll** | 返回活跃事件 | **就绪链表** | 实线 |
| **epoll** | 触发模式 | **水平触发 LT** | 实线 |
| **epoll** | 触发模式 | **边缘触发 ET** | 实线 |
| **边缘触发 ET** | 通常搭配 | **非阻塞 I/O** | 实线 |
| **边缘触发 ET** | 要求 | **非阻塞读写到 EAGAIN** | 实线 |
| **水平触发 LT** | 持续提醒，更易编程 | **边缘触发 ET** | 虚线 |
| **epoll** | 缓解 | **C10K** | 实线 |
| **Reactor** | 基于 | **I/O 多路复用** | 实线 |
| **Reactor** | 耗时业务可委派有界工作池 | **多路复用 + 工作线程/进程池** | 实线 |
| **Redis 6.0 前** | 采用 | **Reactor** | 实线 |
| **Netty** | 采用 | **Reactor** | 实线 |
| **Nginx** | 采用 | **Reactor** | 实线 |
| **传统文件传输** | 设备拷贝由 | **DMA** | 实线 |
| **传统文件传输** | 用户态与内核态往返多 | **零拷贝** | 虚线 |
| **零拷贝** | 典型系统调用 | **sendfile** | 实线 |
| **sendfile** | 内核内传输并使用 | **DMA** | 实线 |
| **就绪链表** | 减少无效扫描 | **中断价值** | 虚线 |
