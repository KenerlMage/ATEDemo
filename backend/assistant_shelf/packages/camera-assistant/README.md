# 工业相机助手 · Basler 调试与光斑测量

独立的桌面工程：直接运行、可打包为 exe，面向"相机参数调试 → 像素图处理 → 光斑测量"一条线。
界面用 PySide2 编写（Python 3.11+ 自动使用 PySide6），业务按 MVP 分层，算法只依赖 numpy。

## 一、两个主流程

| 流程 | 界面 | 能做什么 |
| --- | --- | --- |
| ① 相机调试 | 左侧实时图像 + 右侧参数面板 | 连接/断开、曝光时间（含滑块联动）、增益、自动曝光、帧率、像素格式、触发方式、视野 ROI、连续采集启停、单帧保存 |
| ② 图像处理 | 左侧 matplotlib 画布 + 右侧数据表格 | 对"相机不断曝光的最新帧"按 ROI 截取、阈值分割、连通域分析，输出每个光斑的亮度中心、光斑中心、等效直径、面积、峰值与饱和标记 |

两个流程共享同一路图像：流程一负责把相机调好，流程二始终取最新一帧处理，因此调曝光、调 ROI 的效果会立刻反映在测量值上。

配套交互：在流程一的画面上左键拖拽即可框选 ROI，松开后自动切到流程二并按该区域截取。

## 界面预览

| 流程一 · 相机调试 | 流程二 · 图像处理 |
| --- | --- |
| ![流程一](docs/preview-camera.png) | ![流程二](docs/preview-process.png) |

截图取自仿真模式（关闭显示器渲染的离屏截图），真机界面布局一致。

## 二、目录结构

```text
camera_assistant/
├── main.py                     入口：python main.py
├── camera_assistant/
│   ├── models.py               数据模型：CameraParams / Frame / AnalyzeParams / Blob / AnalyzeResult
│   ├── analyze.py              光斑检测算法（Otsu、连通域、形态量测），纯 numpy
│   ├── camera.py               相机后端：PylonCamera（pypylon）/ SimulatedCamera、采集线程、PNG 落盘
│   ├── mvp.py                  View 契约与 Presenter 基类（无 Qt 依赖）
│   ├── presenters.py           CameraPresenter / ProcessPresenter（无 Qt 依赖，可单测）
│   ├── qt_compat.py            PySide2 ↔ PySide6 兼容层
│   ├── app.py                  装配 Model / Service / View / Presenter
│   └── views/
│       ├── main_window.py      导航 + 视图切换 + 状态栏 + 定时刷新
│       ├── stream_view.py      流式图像显示与 ROI 框选
│       ├── camera_panel.py     流程一参数面板
│       ├── process_panel.py    流程二画布 + 表格 + 检测参数
│       └── adapters.py         把控件聚合成 Presenter 需要的 View 接口
├── tests/                      27 条算法/相机/Presenter 测试（不需要 Qt）
├── camera_assistant.spec       PyInstaller 打包配置
├── requirements.txt            运行与开发依赖
└── requirements-basler.txt     真机依赖（pypylon，可选）
```

## 三、运行

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

接真机时再装 `pip install -r requirements-basler.txt`（pypylon 8.0）。

没有相机或没有 pypylon 时，程序自动进入仿真模式并在状态栏提示原因：仿真源按曝光时间与增益合成背景梯度、三个漂移高斯光斑与固定种子噪声，因此算法、界面、表格、曲线都能在工位上先调通，再接真机。

## 四、架构（MVP）

```text
       事件（点击/参数变更）
View ──────────────────────► Presenter ──────► Model（状态）
 ▲                              │                 │
 │        渲染（画布/表格/状态）    │                 │ 最新帧
 └──────────────────────────────┘                 ▼
                                          CameraService ──► 采集线程 ──► 相机后端
```

约定：

- **Model 不含 Qt**：`CameraModel` 只保存参数、状态与"最近一帧"的引用；
- **Presenter 不含 Qt**：只处理事件与编排；界面刷新由 `MainWindow` 的 `QTimer`（33 ms）驱动，调用 `presenter.on_tick()`；
- **采集线程不碰控件**：`CameraWorker` 只把帧写入模型，界面线程按节拍拉取，避免跨线程操作 UI；
- **View 只装配**：控件、信号、绘制；判定逻辑一律在 Presenter 或算法层。

## 五、检测与量测口径

处理链：ROI 裁切 → 背景估计（分位数）→ 阈值分割 → 四邻域连通域 → 逐连通域量测 → 按面积排序截断。

| 参数 | 说明 |
| --- | --- |
| 阈值模式 | `Otsu 自动`（直方图最大化类间方差）、`绝对阈值`（0–255 灰阶）、`相对峰值 %`（背景到峰值动态范围的百分比） |
| 最小面积 | 过滤噪点；小于该值的连通域不计入结果 |
| 最多光斑 | 按面积降序截断，避免强噪声下的长表格 |
| 反相 | 亮背景上的暗斑检测 |
| 饱和判据 | 像素灰阶 ≥ `saturate_level`（默认 250）即标记该光斑饱和 |

表格字段：

| 字段 | 定义 |
| --- | --- |
| 亮度中心 X/Y | 灰度加权质心，反映能量分布重心 |
| 光斑中心 X/Y | 二值几何质心，反映形状几何中心 |
| 等效直径 | `2 × sqrt(面积 / π)`，与面积严格一致 |
| 面积 | 阈值以上连通域像素数 |
| 峰值 | 连通域内最大灰阶 |
| 饱和 | 是否含达到饱和判据的像素 |

坐标一律为**整幅图像坐标系**（已加回 ROI 偏移），与流程一画面上的框选位置可直接对照。

安全阀：前景占比超过 60% 或前景像素超过 15 万时不做连通域遍历，直接返回"阈值可能失准"的提示——这既避免误判，也避免在阈值失效时卡住界面。

## 六、打包

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller camera_assistant.spec --noconfirm
```

产物在 `dist\CameraAssistant\`，双击 `CameraAssistant.exe` 即可运行。若需单文件分发，把 spec 中的 `COLLECT` 段删掉并对 `EXE` 传入 `a.binaries, a.datas` 后重打包，或直接使用：

```powershell
pyinstaller --noconfirm --onefile --windowed --name CameraAssistant main.py ^
  --hidden-import matplotlib.backends.backend_qtagg
```

打包注意：PySide6 的 `Qt6WebEngine*` 体积很大且本工程未使用，spec 已用 `excludes` 排除；matplotlib 的 `backend_qtagg` 需要显式列入 `hiddenimports`。

## 七、测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests
```

27 条用例覆盖：光斑数量/中心/直径正确性、ROI 坐标偏移、空帧与全暗帧、最小面积过滤、反相模式、前景护栏、饱和标记、Otsu 区间、相对阈值；仿真相机尺寸/曝光/确定性；服务连接与采集启停；PNG 落盘头部；Presenter 连接、参数下发、帧推送、保存、表格填充、同帧去重、ROI 传播。

## 八、已知边界

1. **仅按 Mono8 采集**：可选 Mono12/Mono12Packed，但真机下发时会提示"暂不支持，按 Mono8 采集"，避免出现 "参数显示为 12 位、实际拿到 8 位" 的误判。
2. **PySide2 的 Python 版本上限**：PySide2 官方轮子止于 Python 3.10；3.11+ 用 PySide6，代码通过 `qt_compat.py` 兼容，两套绑定共用同一份界面代码。
3. **真机参数失败不阻断**：Basler 的部分节点存在互相约束（如 Offset 与 Width），下发失败只收集为告警并在状态栏提示，不会中断连接。
4. **仿真不等于真机**：仿真帧用于验证链路与算法，正式测量结论必须来自真机；状态栏会明确标注"仿真"。
5. **ROI 只做矩形**：框选为矩形区域；旋转矩形、圆环等异形区域不在本期范围。
