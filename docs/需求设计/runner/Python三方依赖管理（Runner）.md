# 《Python 三方依赖管理（Runner）》需求设计

## 一、范围与决策

本轮覆盖 Runner 侧的 Python 环境落地：读取 python.lock、按 sha256 从 Controller 取包、本地缓存、预取、创建每 TPS 独立 venv、离线安装、安装校验、失败处理与磁盘回收。不覆盖 Controller 的备案目录、锁生成与包归档，不在 TPS 包内保存 venv，不做版本选择。

现状对账：Runner 现有 `backend\.venv` 与系统 Python 只服务 Runner 自身；`deploy_extra.py` 负责装备软件与助手的部署，不处理 Python 三方依赖；TPS 包内 `wheel/` 目录目前也没有消费逻辑。本轮新增独立的环境构建模块，不改现有部署链路。

## 二、需求清单

### SR02 Python 运行环境构建（Runner）

【需求价值】同一 TPS 在任何测试台装出同一环境，现场不依赖外网与人工装包。
【设计目标】按 python.lock 建独立环境，按 sha 从 Controller 取包，环境可复用、可回滚、可诊断。
【需求描述】覆盖锁校验、环境键、按哈希取包与缓存、预取、离线构建、安装校验、失败处理、磁盘回收与 SDK 注入；不做版本选择。

#### SR02-AR01 锁读取与运行时校验

【需求价值】锁与平台不匹配时立即停止，避免装到一半才失败。
【设计目标】读取 python.lock，校验 schema、Python 3.9.11、cp39 与目标平台后再进入构建。
【需求描述】解析锁文件，检查必填字段、runtime 与本地解释器一致、每个包有 sha256 与 filename；锁缺失或损坏时拒绝执行并提示重新发布；不猜测、不降级。

#### SR02-AR02 环境键与复用

【需求价值】同一锁只建一次环境，复跑不重复安装。
【设计目标】以 TPS 编号加锁哈希定位环境，命中即复用，锁变即新建。
【需求描述】按规范化 runtime 与 packages 计算 lockHash；环境目录为 envs/tps/<tpsId>/<lockHash>；ready.json 的锁哈希与文件哈希一致才复用；旧环境保留供回滚。

#### SR02-AR03 按哈希取包与缓存

【需求价值】按内容取包，缓存不会被同名不同版本污染。
【设计目标】按 sha256 从 Controller 下载单个 whl，落本地缓存并校验。
【需求描述】缓存路径为 cache/wheels/<sha256>/<filename>；命中且哈希一致即跳过；缺失时请求 /api/v1/python/artifacts/{sha256}；临时文件校验后原子改名；404、410 与哈希不符分别报错。

#### SR02-AR04 预取与并发下载

【需求价值】TPS 到位时环境已准备好，首件测试不用等待下载。
【设计目标】TPS 下载后后台预取全部 whl，并发受控，失败不阻断 TPS 入库。
【需求描述】最多 4 路并发；网络错误重试 3 次并退避；先批量检查 sha 可用性；预取失败标记环境未就绪并保留缺失清单；执行时再次校验，不就绪则拒绝。

#### SR02-AR05 离线环境构建

【需求价值】现场无外网也能装出与锁完全一致的环境。
【设计目标】用固定 Python 3.9.11 运行时创建 venv，仅从本地 wheelhouse 安装。
【需求描述】从 runtime/python39 创建环境；把缓存 whl 硬链接或复制到 wheelhouse；pip 使用 --no-index、--only-binary、--require-hashes；禁止访问任何索引源与 sdist。

#### SR02-AR06 安装校验与就绪标记

【需求价值】装完能证明环境就是锁描述的那一套，不靠人工确认。
【设计目标】比对已安装包清单与锁，全部一致才写 ready.json。
【需求描述】读取 site-packages 的发行包名与版本，与锁逐项比对；多包、少包或版本不符即失败；成功后写 lockHash、锁文件哈希、包数与构建时间；失败保留日志。

#### SR02-AR07 失败处理与诊断

【需求价值】现场故障能定位到包、sha 或网络，不产生一半可用的环境。
【设计目标】下载、构建、校验失败均有明确错误码与缺失清单，不留下可执行半成品。
【需求描述】区分 Controller 不可达、包缺失、哈希不符、磁盘不足、pip 失败与清单不一致；环境先在临时目录构建，校验通过再改名；失败保留临时目录与日志供排查。

#### SR02-AR08 磁盘回收与版本保留

【需求价值】环境占磁盘但可回收，正在使用与需要回滚的版本不被误删。
【设计目标】按 TPS 保留最近两个环境，全局磁盘上限可配，使用中的环境不回收。
【需求描述】默认每 TPS 保留 2 个 lockHash；LRU 回收并记录释放空间；运行中的环境加锁；回收前检查无任务引用；缓存 whl 与环境的回收策略分开。

#### SR02-AR09 SDK 注入与依赖一致

【需求价值】平台公共代码只注入不复制，避免与 TPS 依赖互相污染。
【设计目标】SDK 以只读路径注入，其依赖作为隐式包进入锁，版本与 TPS 一致。
【需求描述】执行时把 sdk 目录加入 PYTHONPATH，不安装进 venv；锁内 implicit 包必须已安装；SDK 版本范围不满足时拒绝执行；不修改系统 PATH 与用户 site-packages。

## 三、方案设计

### 3.1 本地目录（SR02-AR01、AR02、AR03、AR05）

```
C:\ATE\
├── runtime\python39\                  # 只读，CPython 3.9.11 + pip
├── cache\wheels\<sha256>\<whl>        # 全站共享，按内容寻址
├── cache\wheels\<sha256>\.ok          # 校验通过标记
└── envs\tps\<tpsId>\
    ├── 9f2c1a2b3c4d\                  # lockHash
    │   ├── Scripts\python.exe
    │   ├── Lib\site-packages\
    │   ├── wheelhouse\                # 硬链接的 whl
    │   ├── lock.txt                   # pip 用，临时
    │   ├── build.log
    │   └── ready.json
    └── 1a7b8c9d0e1f\                  # 旧锁，保留供回滚
```

TPS 包内只有 `python.lock`，没有 venv、site-packages 或 `__pycache__`。

### 3.2 取包流程（SR02-AR01~AR04）

```
读 python.lock
  → 校验 schema / runtime / 必填字段
  → 算 lockHash，查 ready.json
  → 命中：直接返回环境
  → 未命中：批量检查 sha 可用性
  → 缺失包逐个 GET /api/v1/python/artifacts/{sha256}
  → 校验 sha256，落 cache/wheels
  → 建 venv、装包、校验、写 ready.json
```

单包下载使用临时文件：

```python
tmp = dest.with_suffix(".part")
with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as fh:
    shutil.copyfileobj(resp, fh, length=1024 * 1024)
if sha256_file(tmp) != expected:
    tmp.unlink()
    raise EnvError("hash_mismatch", expected, actual)
os.replace(tmp, dest)
```

并发上限 4；网络错误重试 3 次，退避 1/2/4 秒；404 与 410 不重试，直接进入缺失清单。

### 3.3 环境构建（SR02-AR05）

```powershell
C:\ATE\runtime\python39\python.exe -m venv C:\ATE\envs\tps\spm_rh_dyn\9f2c1a2b3c4d.tmp

C:\ATE\envs\tps\spm_rh_dyn\9f2c1a2b3c4d.tmp\Scripts\python.exe -m pip install `
  --isolated --disable-pip-version-check --no-cache-dir `
  --no-index --find-links .\wheelhouse --only-binary=:all: --require-hashes `
  -r .\lock.txt
```

`lock.txt` 由锁生成：

```
numpy==1.26.4 --hash=sha256:9f2c...
opencv-python==4.10.0.84 --hash=sha256:1a7b...
pyserial==3.5 --hash=sha256:3c7e...
```

`--isolated` 屏蔽用户 pip 配置；`--no-index` 禁止访问任何源；`--only-binary` 拒绝 sdist；`--require-hashes` 强制逐包校验。

### 3.4 安装校验与就绪（SR02-AR06）

安装完成后读取 `site-packages` 的发行包清单：

```python
from importlib.metadata import distributions
actual = {normalize(d.metadata["Name"]): d.version for d in distributions(path=[site])}
expected = {normalize(p["name"]): p["version"] for p in lock["packages"]}
if actual != expected:
    raise EnvError("manifest_mismatch", extra=actual.keys()-expected.keys(), missing=expected.keys()-actual.keys())
```

`ready.json`：

```json
{
  "lock_hash": "9f2c1a2b3c4d",
  "lock_sha256": "完整锁文件哈希",
  "catalog_rev": 42,
  "python": "3.9.11",
  "package_count": 3,
  "built_at": "2026-11-24T10:20:00+08:00",
  "status": "ready"
}
```

环境先建在 `.tmp`，校验通过后 `os.replace` 为正式目录；失败保留 `.tmp` 与 `build.log`，不写 `ready.json`。

### 3.5 预取与调度（SR02-AR04、AR07）

- TPS 下载完成即触发预取，不等到第一次执行；
- 预取失败只标记 `env_ready=false`，TPS 仍可入库；
- 执行前再次校验；未就绪则拒绝执行并列出缺失包与 sha；
- 同一环境并发构建用 `envs/tps/<id>/<hash>.lock` 文件互斥，后到者等待；
- 预取与执行共用下载队列，避免同时打满 Controller。

### 3.6 错误码（SR02-AR07）

| 错误码 | 触发 | 处理 |
| --- | --- | --- |
| `E_LOCK_MISSING` | TPS 内没有 python.lock | 拒绝执行，提示重新发布 |
| `E_RUNTIME_MISMATCH` | Python 非 3.9.11 / cp39 / 平台不符 | 不下载，直接失败 |
| `E_ARTIFACT_MISSING` | Controller 返回 404 | 列出包名与 sha，不继续 |
| `E_ARTIFACT_REVOKED` | Controller 返回 410 | 列出被隔离的包，阻断执行 |
| `E_HASH_MISMATCH` | 下载文件 sha 不符 | 删除重下一次，仍失败则报损坏 |
| `E_NO_SPACE` | 磁盘不足 | 下载前预估，拒绝并提示清理 |
| `E_PIP_FAILED` | pip 返回非零 | 保留 build.log，回传末 2 KB |
| `E_MANIFEST_MISMATCH` | 安装清单与锁不一致 | 保留环境，标记失败，不执行 |

### 3.7 回收（SR02-AR08）

| 对象 | 策略 |
| --- | --- |
| 环境 | 每 TPS 保留最近 2 个 lockHash，LRU 删除；运行中加锁 |
| whl 缓存 | 全局上限默认 20 GB，按最近使用回收；被现有环境引用的不删 |
| 构建临时目录 | 失败保留 7 天，成功后删除 |
| 日志 | `build.log` 随环境保留，环境删除时一并清理 |

### 3.8 前端状态（SR02-AR02、AR04、AR07）

执行页 TPS 详情增加只读状态：

```
Python 环境：就绪 / 构建中 / 未就绪 / 失败
锁哈希：9f2c1a2b3c4d
备案目录：rev 42
包：3 个
[查看缺失] [重新构建]
```

普通操作员只看到状态与“重新构建”；缺失明细、错误码与日志给工程师查看。隐藏部署清单的偏好不影响该区域。

## 四、验收与实测

| 验收点 | 对应 AR | 通过标准 | 状态 |
| --- | --- | --- | --- |
| 锁损坏拒绝执行 | AR01 | 返回 `E_LOCK_MISSING` 或 schema 错误 | 待实现 |
| 运行时不符不下载 | AR01 | 返回 `E_RUNTIME_MISMATCH`，无网络请求 | 待实现 |
| 同一锁复用环境 | AR02 | 第二次执行耗时小于 1 s | 待实现 |
| 锁变更新建环境 | AR02 | 出现新 lockHash 目录，旧目录保留 | 待实现 |
| 缓存命中不访问 Controller | AR03 | 断网仍能建环境 | 待实现 |
| 哈希不符不落缓存 | AR03 | 缓存目录无 `.part`，返回 `E_HASH_MISMATCH` | 待实现 |
| 四路并发预取 | AR04 | 预取完成时间明显低于串行 | 待实现 |
| 预取失败不阻断入库 | AR04 | TPS 状态正常，环境标记未就绪 | 待实现 |
| 断网安装成功 | AR05 | `pip` 无索引请求，安装完成 | 待实现 |
| sdist 被拒绝 | AR05 | 返回 `--only-binary` 错误 | 待实现 |
| 安装清单与锁一致 | AR06 | `ready.json` 写入，状态 ready | 待实现 |
| 多包或少包失败 | AR06 | 返回 `E_MANIFEST_MISMATCH` | 待实现 |
| 失败不留正式环境 | AR07 | 只有 `.tmp`，无正式目录 | 待实现 |
| 保留两个环境 | AR08 | 第三个旧环境被 LRU 删除 | 待实现 |
| 运行中环境不回收 | AR08 | 回收跳过并记录 | 待实现 |
| SDK 不进入 site-packages | AR09 | venv 内无 atefr，但可 import | 待实现 |

当前只有设计与现状对账，未进入编码，故全部标记待实现。

## 五、未覆盖范围

- Controller 的备案目录、锁生成与分发接口，见《Python 三方依赖管理（Controller）》。
- 真实 Controller 网络联调、鉴权与限流未做。
- Linux 下位机的 Python 环境未覆盖，本轮只覆盖 Runner 所在上位机。
- 预取队列的优先级、带宽限速与多站点调度未做。
- Python 3.9.11 已停止官方维护，基线升级不在本轮。
- 现有 `deploy_extra.py` 的装备软件部署链路不改造，Python 环境走独立模块。

## 六、AR 覆盖核对

| AR | 方案落点 | 状态 |
| --- | --- | --- |
| SR02-AR01 | 3.1、3.2 | 已设计 |
| SR02-AR02 | 3.1、3.2、3.8 | 已设计 |
| SR02-AR03 | 3.1、3.2 | 已设计 |
| SR02-AR04 | 3.2、3.5、3.8 | 已设计 |
| SR02-AR05 | 3.1、3.3 | 已设计 |
| SR02-AR06 | 3.4 | 已设计 |
| SR02-AR07 | 3.5、3.6、3.8 | 已设计 |
| SR02-AR08 | 3.1、3.7 | 已设计 |
| SR02-AR09 | 3.3、3.8 | 已设计 |
