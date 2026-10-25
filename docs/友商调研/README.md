# 友商调研 · 索引

> 用途：为《ATE 工具业界实现方式》汇报提供可引用的原始材料。
> 调研对象：荷兰 **ASML**、美国 **NI（National Instruments，2023 年起属 Emerson）**。
> 归档时间：2026-10-25。

---

## 一、三分钟结论

1. **ASML 不做 ATE。** 它的产品是光刻扫描机（TWINSCAN）、光学量测（YieldStar）、电子束检测（HMI e-beam）与计算光刻（Brion）。把它放进 ATE 对标章节，正确写法是「**设备内建量测与自校准**」这一路线，而不是「另一家 ATE 厂商」。
2. **NI 做的是平台化 ATE。** 硬件用 PXI/PXIe 模块拼装，软件用 TestStand（测试执行器）+ TestStand Semiconductor Module（多 site / pin map）构成运行时。核心思想是「**测试执行与仪器解耦，靠驱动和配置拼装**」。
3. **真正的高端量产 ATE 是第三条路线：专用 per-pin 架构**（Advantest V93000、Teradyne UltraFLEX）。每个引脚独立时序与电平，性能天花板最高，但封闭、贵、改型慢。NI 与 ASML 的可借鉴价值，恰恰来自它们**不在**这条路上。
4. **两条可落地的借鉴**：向 NI 学「执行器 / 驱动 / 配置」三层解耦与 site 抽象；向 ASML 学「量测结果反哺参数」的闭环，以及「量测与执行并行」的流水线化。
5. **对我们的直接落点**：ATE Runner 现有的 L5–L1 五层，已经接近 NI 的形态；缺的是 **site 概念**（一台 Runner 管多台位时的逻辑隔离）与 **参数闭环通道**（结果不只是 pass/fail，而是下一轮的可调参数）。

---

## 二、文件索引

| 文件 | 内容 | 建议用途 |
| --- | --- | --- |
| `01-定位纠偏与三条技术路线.md` | 先纠正 ASML 的定位，再把业界实现归为三条路线并给出判据 | 汇报的「背景与坐标系」一页 |
| `02-NI-STS-平台化ATE拆解.md` | STS T1/T2/T4 硬件谱系、TestStand + TSM 软件栈、site/pin 抽象、关键指标 | 汇报的「平台化路线」一页 |
| `03-ASML-量测与设备侧测试闭环.md` | TWINSCAN 双工件台、YieldStar、HMI e-beam、holistic lithography 闭环 | 汇报的「设备侧闭环」一页 |
| `04-可搬进PPT的页纲与要点.md` | 三页页纲、12 条可直接上屏的短句、一张对比表、讲稿提示 | 直接抄进 PPT |
| **`05-ASML制造与被测部件.md`** | **换角度：造光刻机时每个部件怎么被自动测——四层被测对象与各层装备** | **制造测试装备篇的正文** |
| **`06-测试装备谱系与我们的切入点.md`** | **四类测试手段 × 四层对象矩阵、六个场景特征、四个能力缺口、明确不做的事** | **落地判断与取舍** |
| **`07-半导体ATE控制架构.md`** | **控制面/数据面分离、三种控制形态、TestStand+LabVIEW+TSM 拆解、四大厂商对比、四条设计约束** | **架构选型与控制面/数据面划分** |
| `三条路线对照.png` | 三条路线的分层对照图（2400×1350） | 汇报正文插图 |
| **`ASML制造链与测试装备分层.png`** | **L1–L4 四层制造链与各层测试装备（2400×1350）** | **制造篇第 2 页** |
| **`部件测试装备谱系矩阵.png`** | **四类手段 × 四层对象矩阵，橙框标出我们的目标格（2400×1350）** | **制造篇第 3 页** |
| `友商调研-两页.pptx` | 三页原生形状幻灯片（无位图） | 直接并入你的 PPT |
| **`ASML制造测试装备.pptx`** | **五页：重新定义问题 / 制造链 / 装备谱系 / 三个判断 / 来源口径（含两张自制图）** | **制造篇主交付** |
| **`ATE三种控制形态.png`** | **单站集中 / 中心下发多站 / 站点自治 三形态对照（2400×1350）** | **控制架构篇第 2 页** |
| **`TestStand到LabVIEW调用链.png`** | **分层与进程边界 + 三 site 并行时序（2400×1350）** | **控制架构篇第 4 页** |
| **`半导体ATE控制架构.pptx`** | **六页：两问分解 / 三形态 / NI 分工 / 调用链 / 其他厂商 / 我们的取舍** | **控制架构篇主交付** |
| **`08-NI-ATE软件角色.md`** | **NI 五层软件分工、逐个软件的角色与交接点、买 vs 自建、对我们的启示** | **NI 软件栈正文** |
| **`NI-ATE软件栈.pptx`** | **两页图文：五层软件栈 + 两张官网产品图；角色表 + 执行链 + 两张采集图** | **本轮主交付** |
| **`NI-ATE软件栈.png`** | **五层栈 + 六段交接链一览图（2000×1125）** | **聊天/汇报插图** |
| **`NI-ATE汇报-硬件拓扑与软件架构.pptx`** | **汇报版（2 页）：P1 硬件拓扑 + 适用场景；P2 软件架构 + 适用场景；白底细线版式、全篇微软雅黑** | **向领导汇报用** |
| **`业界方案洞察-NI.pptx`** | **汇报定稿版（2 页）：页标题统一「业界方案洞察」，灰底框内为 NI 公司简介，页底加小结，右侧配图** | **上一版（条带式）** |
| **`业界方案洞察-NI-拓扑版.pptx`** | **两页：左栏为节点加连线拓扑（P1 五层硬件、P2 四层软件）** | **汇报用** |
| **`业界方案洞察-NI-单页.pptx`** | **单页：合入软件沿革简介，标注三大组件在拓扑中的位置，新增仪器虚拟化五条做法** | **已备份，见下** |
| **`业界方案洞察-NI-单页-备份.pptx`** | **单页版备份（文字框节点版），供对照与回退** | **备份** |
| **`业界方案洞察-NI-图标版.pptx`** | **本轮主交付：拓扑节点改为图标/图片，三大组件改引官网原文，新增产业应用八项** | **汇报用** |
| `ni-images/icons/` | 12 枚节点图标（线稿 PNG，由 gen-node-icons.py 生成） | 可复现 |
| **`NI-节点图标总览.png`** | **图标总览图（1732×1022）** | **聊天/汇报插图** |
| `ni-images/` | 自 ni.com 采集的 5 张产品图（TestStand、PXI 机箱、LabVIEW、InstrumentStudio、NI 标识） | PPT 可复现 |

---

## 三、引用清单

| 编号 | 来源 | 用于支撑 |
| --- | --- | --- |
| R1 | NI，《What Is the Semiconductor Test System (STS)?》<br>`https://www.ni.com/en/shop/electronic-test-instrumentation/what-is-the-semiconductor-test-system.html` | STS 的定位、PXI 架构与软件配套 |
| R2 | Apex Waves，《Using NI STS for Mixed-Signal and MEMS Devices》<br>`https://www.apexwaves.com/blog/using-national-instruments-sts-for-mixed-signal-and-mems-devices` | T1/T2/T4 分别容纳 1/2/4 个 18 槽 PXI 机箱 |
| R3 | NI，《PXIe-6570 Overview / Specifications》<br>`https://www.ni.com/docs/en-US/bundle/pxie-6570/page/overview.html` | 32 通道、2 槽、100 MVector/s 数字通道指标 |
| R4 | Electro Rent，PXIe-6570 参数页<br>`https://www.electrorent.com/us/products/other/other/ni/pxie-6570/` | 39.0625 ps 边沿分辨率、-2~6 V、PPMU、200 Mb/s |
| R5 | NI，《TestStand Semiconductor Module》手册（PDF）<br>`https://www.farnell.com/datasheets/3990084.pdf` | TSM 的存在与功能范围 |
| R6 | CIOReview，《NI Releases TestStand Semiconductor Module》<br>`https://www.cioreview.com/news/ni-releases-teststand-semiconductor-module-for-semiconductor-test-systems-nid-13316-cid-85.html` | 动态多 site 编程、代码在可扩展 site 数上复用 |
| R7 | NI，《Semiconductor Test Library (.NET)》<br>`https://ni.github.io/semi-test-library-dotnet` | STS 上可用 C# 编写测试程序 |
| R8 | Emerson，《Emerson Completes Acquisition of NI》<br>`https://www.emerson.com/en/corporate/news/2023/emerson-completes-ni-acquisition` | 2023-10-11 完成收购，$8.2B |
| R9 | ASML，《Products》<br>`https://www.asml.com/en/products` | holistic 说法：计算建模 + 光学量测 + 电子束检测与光刻系统集成 |
| R10 | ASML，《Measuring accuracy》<br>`https://www.asml.com/en/technology/lithography-principles/measuring-accuracy` | 扫描机量测软件用机床过程的计算模型协调机电模块；YieldStar 衍射式测量 |
| R11 | ASML，《YieldStar 1385 / 500》<br>`https://www.asml.com/en/products/metrology-and-inspection-systems/yieldstar-1385` | 独立式后蚀刻 overlay 量测；500 为产片上直接监测 overlay/focus |
| R12 | ASML，《HMI eScan 1000》<br>`https://www.asml.com/en/products/metrology-and-inspection-systems/hmi-escan-1000` | 9 束 3×3 阵列、单源架构 |
| R13 | ASML，《TWINSCAN: 20 years》<br>`https://www.asml.com/en/company/stories/2021/twinscan-20-years-innovation` | 双工件台（两个完整 wafer stage） |
| R14 | ASM International，《ASML to acquire HMI》(2016) | 2016 年收购 HMI，补电子束量测 |
| R15 | Umbrex，《ASML Strategy and Business Model》 | 2007 年收购 Brion，补计算光刻软件 |
| R16 | Advantest，《V93000》产品页 / Pin Scale 400 数据手册 | 对照路线：test processor per pin、每引脚 AC/DC 资源 |

---

## 四、写作口径

- 文中所有数字与产品能力均来自上表来源；凡属**推断**（例如「对应到我们哪一层」）一律标注「判断」，不与事实混写。
- ASML 与 NI 不在同一竞争象限，本文不对二者做「谁更强」的比较，只做**实现方式**的对照。
- 涉及金额、日期、型号一律保留原始表述，避免二次换算。
