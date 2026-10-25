# 08 · NI 的 ATE 软件栈：每个软件的角色

> 配图与幻灯片：`NI-ATE软件栈.pptx`（2 页，含 ni.com 采集的产品图）
> 图片采集自 ni.com 产品页，仅用于内部技术评审。

## 一、五层分工

| 层 | 软件 | 角色 |
| --- | --- | --- |
| L5 数据与运维 | SystemLink、DIAdem | 机群与测试数据管理；离线分析与报告 |
| L4 执行与调度 | TestStand、TSM | 序列引擎、流程模型、site/pin map、binning 与 STDF |
| L3 测量与交互 | LabVIEW、InstrumentStudio | 测量代码本体；仪器软面板与交互调试 |
| L2 路由与驱动 | Switch Executive、NI-VISA / 488.2、NI-DAQmx | 开关矩阵路径；会话层；传感器与模拟量驱动 |
| L1 平台与硬件 | PXI 机箱与模块、PXI Platform Services、NI MAX | 机箱、模块、时钟与触发的发现、配置与自检 |

分层的好处：换一层不改相邻层。换仪器只动 L2，换被测件只动 L3 的 VI 与 L4 的序列，换机箱只动 L1。

## 二、逐个软件的角色

| 软件 | 角色 | 交接点 |
| --- | --- | --- |
| **TestStand** | 序列引擎：按 .seq 解释执行 step，跑流程模型（Test UUTs / Batch / Parallel），收集结果 | ②③ |
| **TSM（TestStand Semiconductor Module）** | 在 TestStand 上加半导体语义：site 抽象、pin map、多工位复用、binning、STDF 输出 | ③ |
| **LabVIEW** | 测量代码本体：一个 step 对应一个 VI，VI 内做采集、计算、判定 | ④ |
| **Switch Executive** | 开关与矩阵的路径配置与互斥，避免共享资源串扰 | ⑤ |
| **InstrumentStudio** | 仪器软面板：不写代码先手工点，确认通道与量程，再固化成 VI；调试期留证 | ④ |
| **NI-VISA / NI-488.2** | 统一会话层：LAN、GPIB、USB、串口仪器用同一套 API | ⑤ |
| **NI-DAQmx** | 传感器与模拟量采集、定时与触发、计数器 | ⑤ |
| **PXI Platform Services / NI MAX** | 机箱与模块发现、时钟与触发路由配置、自检与固件 | ① |
| **SystemLink / DIAdem** | 机群与测试数据管理（部署、资产、结果）；离线分析与报告 | ⑥ |

## 三、一次多工位测试的六个交接点

1. **① 配置**：NI MAX 发现机箱与模块，PXI Platform Services 配时钟与触发。
2. **② 序列**：TestStand 加载 .seq，选 Process Model，进入 PreUUT。
3. **③ 并行**：TSM 按 site 建 socket，Batch/Parallel 为每个 site 复制一份序列到新线程。
4. **④ 测量**：每个 step 调一个 VI，VI 内调驱动；InstrumentStudio 用于前期试探。
5. **⑤ 路由与驱动**：Switch Executive 切路径，VISA/DAQmx 落地为真实电气动作。
6. **⑥ 归档**：结果写 TestStand 结果库与 STDF；SystemLink 汇总，DIAdem 出报告。

## 四、买 vs 自建

| 能力 | 建议 | 理由 |
| --- | --- | --- |
| ②③ 执行与调度 | 采购 | 通用能力强，自建等于重写一个 TestStand |
| ⑥ 数据与报告 | 采购或轻量自建 | 与 MES 对接需定制，但分析引擎不必自研 |
| ④ 测量代码 | 自研 | 与被测件、夹具强绑定 |
| ⑤ 驱动与路由 | 采购 + 薄封装 | 驱动用厂商的，路由配置可自研 |

## 五、对我们的启发

- TestStand 的价值不在「能跑序列」，而在**流程模型与结果容器**：把 UUT 循环、初始化收尾、binning 固化下来，测试作者只写 step。
- TSM 的价值在**把 site 变成一等公民**：这正是我们缺的那层。
- InstrumentStudio 提醒一件事：**调试期的软面板与量产期的序列是两套东西**，不能混在同一个界面里做成半成品。
