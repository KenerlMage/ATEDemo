# ATE Runner 仪器驱动库 · 使用手册

> 适用版本：`ate_drivers` 1.2.0 · 契约 **1.2** · 门禁 19 项
> 目标读者：写 TPS 用例、做工具卡片、接平台的开发人员。**你不需要懂任何仪器命令**。

## 1. 环境与安装

| 项 | 要求 |
| --- | --- |
| Python | 3.10+（本机 `D:\ATE\backend\.venv\Scripts\python.exe`） |
| 运行时依赖 | **标准库**。串口链路额外需要 `pyserial`；`backend="visa"` 额外需要 `pyvisa`（两者都惰性导入，不装也能用网口、串口原生栈与仿真） |
| 离线安装 | 工控机无外网时，直接把 `sdk/` 目录拷过去，用 `PYTHONPATH` 指到它，或 `pip install --no-index --find-links <本地wheel目录> .` |
| 无硬件 | 完全可用。不写地址就是**仿真模式**，所有方法都能跑通 |

```powershell
# 本机开发：把 sdk 目录加进 PYTHONPATH
$env:PYTHONPATH = "D:\ATE\sdk"
python -c "import ate_drivers; print(ate_drivers.__version__)"
```

## 2. 五分钟上手

### 2.1 仿真（不接任何硬件）

```python
from ate_drivers import open_scope

with open_scope("MSO54") as scope:            # 型号 = 派发依据；不给地址 = 仿真
    scope.auto_setup(freq_hz=1000.0, volts_pp=2.4)
    w = scope.capture(channel="CH1", points=1000)
    print(f"{w['points']} 点，采样间隔 {w['seconds_per_sample']:.3e} s，单位 {w['unit']}")
    print(scope.measure(("PK2PK", "FREQUENCY", "RISE")))
```

真实输出：

```
1000 点，采样间隔 5.005e-06 s，单位 V
{'PK2PK': {'value': 2.4375, 'unit': 'V', 'quality': 'good', 'source': 'simulate'},
 'FREQUENCY': {'value': 1002.005013, 'unit': 'Hz', 'quality': 'good', 'source': 'simulate'},
 'RISE': {'value': 0.000500250125, 'unit': 's', 'quality': 'good', 'source': 'simulate'}}
```

### 2.2 网口（LAN）

```python
with open_scope("MSO54", host="192.168.10.41", port=4000) as scope:
    scope.self_test()                          # 上产线前先自检
    scope.auto_setup(freq_hz=1000.0, volts_pp=2.4)
    print(scope.measure(["PK2PK", "FREQUENCY"]))
```

### 2.3 串口（SERIAL）

```python
with open_scope("MSO54", serial_port="COM6", baudrate=115200) as scope:   # 帧格式默认 8N1
    scope.self_test()
    print(scope.capture(points=64)["volts"][:5])
```

**同一台设备的两种接法，代码只差连接参数。** 换品牌同理：型号从 `MSO54` 改成 `MXO44`，其余一行不改。

### 2.4 直接用设备档案打开（平台侧推荐）

设备档案（SQLite 行 / dict）里已经有型号与连接参数，直接交给它：

```python
from ate_drivers import open_scope

row = {"device_id": "SPM-RH-DYN-01", "model": "MSO54", "interface": "LAN",
       "host": "192.168.10.41", "port": 4000}
with open_scope(device=row) as scope:
    print(scope.model, scope.interface_kind, scope.endpoint_detail)
```

`device=` 支持 dict 或 ORM 行对象，自动读 `model` / `interface` / `host` / `port` / `serial_port` / `baudrate` / `name` / `device_id`。**型号或连接方式写错会当场报错**，不会替你猜。

### 2.5 选后端：原生栈还是 PyVISA

链路（网口 / 串口）和后端（原生栈 / VISA）是两件事。默认 `native`，换上 VISA 只多一个参数：

```python
from ate_drivers import open_scope

# 原生栈（默认）：标准库 socket / pyserial，零依赖
with open_scope("MSO54", host="192.168.10.41", port=4000) as scope:
    print(scope.backend, scope.endpoint)          # native 192.168.10.41:4000

# PyVISA：同一台设备、同一段业务代码
with open_scope("MSO54", host="192.168.10.41", port=4000, backend="visa") as scope:
    print(scope.backend, scope.visa_resource)     # visa TCPIP0::192.168.10.41::4000::SOCKET

# 串口走 VISA：波特率与帧格式作为会话属性下发（资源名是 ASRL6::INSTR）
with open_scope("MSO54", serial_port="COM6", baudrate=115200, backend="visa") as scope:
    print(scope.visa_resource)                    # ASRL6::INSTR

# auto：装了 pyvisa 就走 VISA，否则走原生；实际用了哪个从 backend 查
with open_scope("MSO54", host="192.168.10.41", port=4000, backend="auto") as scope:
    print(scope.backend_detail())                 # {'requested': 'auto', 'used': ..., 'visa_available': ...}
```

也可以把后端写进设备档案（`extra.backend`），现场统一走 NI-VISA 时不用改用例：

```python
row = {"model": "MXO44", "host": "192.168.10.51", "port": 5025, "backend": "pyvisa"}
with open_scope(device=row) as scope:            # 档案里的 pyvisa 是别名，归一化后即 visa
    print(scope.backend)
```

安装（离线工控机用本地 wheel）：

```bash
pip install pyvisa pyvisa-py     # pyvisa-py = 纯 Python 的 VISA 实现，不用装 NI-VISA
pip install pyvisa               # 已有 NI-VISA / Keysight VISA 时只装 pyvisa 即可
```

注意事项：

* **没装 pyvisa 就报错，不会静默退回原生栈**：`E_CONFIG: VISA 后端需要 pyvisa（当前环境未安装）`；
* 型号必须声明该后端（`BACKENDS`），否则 `E_CONFIG: MSO54 不支持 visa 后端`（并列出该型号支持哪些）；
* 仿真模式与后端无关，`mode="simulate"` 时不需要任何第三方库；
* `auto` 只是省事，**它挑了什么一定写在 `scope.backend` 上**；
* 网口 VISA 用 raw socket 资源（`…::SOCKET`）；若现场走 VXI-11/GPIB，直接在档案里给资源名或扩展 `endpoint` 层，不要在用例里拼命令。

## 3. 门面方法一览（`Scope`）

| 方法 | 参数（单位） | 返回要点 |
| --- | --- | --- |
| `open_scope(model=None, host=None, port=None, *, device=None, interface=None, serial_port=None, baudrate=None, bytesize=None, parity=None, stopbits=None, mode="auto", timeout=2.0, transport=None, alias="", verify_model=True, open=False)` | 型号 + 连接参数 | `Scope` 实例；`open=True` 立即连接 |
| `connect(verify_model=None)` | — | 连接并按需核对型号 |
| `identity()` | — | `{vendor, model, serial, firmware, idn}` |
| `reset()` | — | 复位仪器 |
| `auto_setup(freq_hz=None, volts_pp=None)` | 目标频率 Hz、峰峰值 V | 自动设置时基与垂直档位 |
| `set_timebase(seconds_per_div=None, offset_seconds=None, divisions=None)` | 秒/格、秒、格数 | 时基状态 |
| `set_channel(channel="CH1", volts_per_div=None, coupling=None, offset_volts=None, probe_ratio=None, enabled=None)` | V/格、耦合、V | 通道状态 |
| `configure(channel="CH1", seconds_per_div=None, volts_per_div=None, coupling=None, offset_volts=None, divisions=None)` | — | 时基 + 通道一次配好 |
| `capture(channel="CH1", points=1000)` | 通道、点数 | 波形：`volts`、`seconds_per_sample`、`first_sample_seconds`、`unit` |
| `measure(items=None, channel="CH1", points=2000)` | 测量项列表 | `{项: {value, unit, quality, source}}` |
| `run()` / `stop()` / `single()` | — | 连续 / 停止 / 单次触发 |
| `status()` | — | 会话全量状态（含 `interface_kind`、`resource`、`endpoint_detail`） |
| `metadata()` | — | 驱动与契约元信息（`driver`、`driver_version`、`contract_api`、`interfaces`、`mode`） |
| `self_test()` | — | 逐项自检清单 |
| `close()` | — | 安全退出，返回本次会话发出的命令条数 |

只读属性：`model` · `vendor` · `label` · `interface` · `interfaces` · `interface_kind` · `endpoint` · `endpoint_detail` · `mode` · `simulated` · `metadata()`（方法）。

## 4. 返回结构怎么看

### 4.1 动作类

```json
{"ok": true, "action": "set_timebase", "simulated": true, "source": "simulate",
 "value": {"scale_s_per_div": 0.0005, "divisions": 10, "offset_s": 0.0,
           "window_s": 0.005, "unit": "s/div"}}
```

### 4.2 采集类（`capture`）

```json
{"ok": true, "simulated": true, "channel": "CH1", "points": 1000,
 "seconds_per_sample": 5.005005005005005e-06, "first_sample_seconds": 0.0,
 "unit": "V", "source": "simulate",
 "volts": [0.0, 0.0625, 0.0625, 0.109375, ...]}
```

`unit` 恒为 `V`（**不返回格数或 ADC 计数**），时间轴用 `first_sample_seconds + n × seconds_per_sample` 还原。

### 4.3 测量类

```json
{"PK2PK": {"value": 2.4375, "unit": "V", "quality": "good", "source": "simulate"}}
```

支持 8 个测量项：`PK2PK`(V) · `AMPLITUDE`(V) · `MEAN`(V) · `RMS`(V) · `FREQUENCY`(Hz) · `PERIOD`(s) · `RISE`(s) · `FALL`(s)；`scope.supported_measurements()` 可查。`source` 为 `simulate` 时说明数据是仿真生成的——**报告里要如实标注**。

### 4.4 自检

```json
{"ok": true, "model": "MSO54", "vendor": "Tektronix", "endpoint": "", "simulated": true,
 "checks": [{"name": "会话已建立", "ok": true}, {"name": "可用于采集", "ok": true},
            {"name": "可测量", "ok": true}, {"name": "型号已核对", "ok": true},
            {"name": "连接方式可用", "ok": true}, {"name": "无遗留错误", "ok": true}],
 "identity": {"vendor": "TEKTRONIX", "model": "MSO54", "serial": "SIM0001", "firmware": "1.2.3"}}
```

## 5. 错误处理

```python
from ate_drivers import open_scope
from ate_drivers.errors import ConfigurationError, DriverTimeout, ProtocolError

try:
    with open_scope(device=row) as scope:
        scope.measure(["PK2PK"])
except ConfigurationError as e:          # E_CONFIG：改档案 / 查接线，别重试
    print("配置问题", e.code, e)
except DriverTimeout as e:               # E_TIMEOUT：可重试
    print("超时", e.code, e)
except ProtocolError as e:               # E_PROTOCOL：方言或固件问题
    print("应答解析失败", e.code, e)
```

| 码 | 意思 | 处置 |
| --- | --- | --- |
| `E_CONFIG` | 连接参数不完整/非法、链路歧义、型号不符 | 改档案或接线，**不重试** |
| `E_NOT_FOUND` | 型号未登记 | 补登记 / 装驱动包 |
| `E_TRANSPORT` | 连不上、写失败 | 查线查电源，可重试 |
| `E_TIMEOUT` | 读写超时 | 可重试；仍失败查链路 |
| `E_PROTOCOL` | 应答无法解析 | 查方言 / 固件版本 |
| `E_PARAM` | 参数越界或枚举未登记 | 改参数（消息里带已登记取值） |
| `E_UNSUPPORTED` | 能力未声明 | 先 `supports()` 判断 |
| `E_CONTRACT` | 契约版本不兼容 | 升级驱动或平台 |

**只按 `code` 分支，不要解析中文消息文本。**

## 6. 命令行工具

```bash
python -m ate_drivers.kit.cli new <key> [--dir DIR] [--cls CLS] [--label LABEL]
                                    [--vendor VENDOR] [--model MODEL]
                                    [--interface LAN,SERIAL] [--force]
python -m ate_drivers.kit.cli check <path> [--json]      # 19 项门禁
python -m ate_drivers.kit.cli list [dir]                 # 列出已装驱动包
python -m ate_drivers.kit.cli models [--dir DIR]         # 列出可派发型号
python -m ate_drivers.kit.cli contract                   # 打印契约说明
```

`models` 的真实输出：

```
注册表自检：通过（型号 5 · 别名 1）
厂商 Rohde & Schwarz · 驱动 rohde-schwarz-mxo4 v1.5.0 · 接口 LAN / SERIAL · 仿真 支持
```

`check` 的输出形如：

```
ate_drivers/vendors/tektronix_mso | exit=0 | 结果 19/19 通过 | fail=0
```

## 7. 写自己的驱动包（三步）

```bash
# 1. 生成骨架（型号必填：它是派发的唯一依据）
python -m ate_drivers.kit.cli new acme-scope-3000 --dir drivers --vendor ACME \
       --label "数字示波器 · ACME 3000" --model ACME-3000 --interface LAN,SERIAL

# 2. 填五个差异面：COMMANDS / PREAMBLE_FIELDS / 量纲缩放 / 枚举映射 / _sim_signal()

# 3. 上架门禁必须 19/19
python -m ate_drivers.kit.cli check drivers/acme_scope_3000
```

细节与完整示例见《新设备家族接入指南》；写代码时守《设计准则》。

## 8. 故障排查

| 现象 | 多半是 | 怎么办 |
| --- | --- | --- |
| `E_CONFIG：网口端点不完整：host 与 port 必须成对给` | 档案只填了 IP 没填端口（或反过来） | 补齐；端口通常是 4000（泰克）/ 5025（R&S） |
| `E_CONFIG：连接方式不明确：网口与串口参数都填了` | 档案两种参数都有、`interface` 空 | 档案里写明 `interface`，或调用时显式 `interface="LAN"` |
| `E_CONFIG：串口端点不完整：缺少串口名` | 只给了波特率 | 补 `serial_port`（如 `COM6`） |
| `E_CONFIG：设备回读型号与档案不符：档案 MSO54，实测 MXO44` | 档案型号写错 / 接错机 | 改档案；确认不是接错线 |
| `E_NOT_FOUND：未登记型号 ... 可用：MSO54, MSO56, ...` | 型号不在注册表 | 写驱动包并登记，或改用已登记型号 |
| `E_CONFIG：XXX 不支持 SERIAL 连接` | 该型号只声明了 LAN | 换网口，或给驱动加 `INTERFACES` 后重新过门禁 |
| `E_CONFIG：未安装 pyserial，串口链路不可用` | 串口链路缺依赖 | `pip install pyserial`（离线机用本地 wheel） |
| `E_CONFIG：VISA 后端需要 pyvisa（当前环境未安装）` | 写了 `backend="visa"` 但没装 pyvisa | `pip install pyvisa pyvisa-py`（有 NI-VISA 时只装 pyvisa） |
| `E_CONFIG：MSO54 不支持 visa 后端` | 型号的 `BACKENDS` 没声明 `visa` | 给驱动补 `BACKENDS = ("native", "visa")` 并同步清单后重过门禁 |
| `E_CONFIG：传输后端不在支持范围：'usb-tmc'` | 后端写法不在 `{native, visa, auto}` | 改档案 / 改参数；别名（`pyvisa` / `ni` / `stdlib` …）会自动归一 |
| `auto` 之后 `backend` 是 `native` | 机器没装 pyvisa | 正常行为（`auto` 按可用性挑）；要强制走 VISA 就写死 `backend="visa"` |
| VISA 打不开 `ASRL6::INSTR` | 串口名与 VISA 端口号不一致，或设备被别的进程占着 | 按实际 VISA 实现核对串口名；`ASRL::/dev/ttyUSB0::INSTR` 是 POSIX 路径写法 |
| `E_TIMEOUT` | 地址/端口错、线没插好、仪器忙 | 先 `ping`、再 `scope.self_test()` |
| `E_PROTOCOL` | 方言不匹配（固件版本或型号串写错） | 用 `verify_model=True` 暴露型号问题；核对命令表 |
| `capture` 出来的波形是方波台阶 | 正常现象 | 仿真链路带垂直量化；真机看是否触发了 `auto_setup` |

## 9. 注意事项

- **先 `self_test()` 再上产线**：它会核对型号、链路、错误队列；
- **超时默认 2 秒**：长采集/长触发场景显式传 `timeout=`；
- **点数与窗口**：`capture(points=...)` 的点数受当前时基窗口约束，点数越多采样间隔越小；
- **报告里要标数据来源**：`source == "simulate"` 说明是仿真数据，不能当实测结果；
- **不要并发操作同一个 `Scope` 实例**：一台设备一个实例（实例间互不干扰，但同一实例不保证线程安全）；
- **实例用完要 `close()`**：`with` 语句会自动关；异常路径同样建议 `with`。
- **换后端不动用例**：`backend` 只影响「用哪套栈收发」，命令、参数、返回结构都不变；要全现场统一走 NI-VISA，把 `backend` 写进设备档案（`extra.backend`）比改用例更稳；
- **别在报告里省略后端**：同一批数据是 socket 还是 VISA 读回来的，排障时要能追；`scope.backend` 与 `scope.visa_resource` 会进 `status()` / `metadata()`，落报告即可；
- **VISA 也要看资源名对不对**：网口是 `TCPIP0::host::port::SOCKET`，串口是 `ASRL6::INSTR`；波特率/帧格式不在资源名里，改档案 `baudrate` 即生效。
