# Python 三方依赖推荐方案（详细）

> 适用：MockController + MockRunner 部署在**无外网**的测试台小网环境。
> 对应需求：`docs/需求设计/controller/软件部署功能.md` 的 SR03（Controller 供给）与 SR04（Runner 执行）。
> 版本：v1.2　2026-11-24（锁文件升级为 v2：Python 3.9.11、备案目录、单 whl）

## 1. 结论

采用**四层**结构，而不是把三方包塞进 Runner 安装包，也不是"每个 TPS 用完即删"：

```
① Runner 自带 Python 解释器与自身依赖   （不装 TPS 的三方包）
② Controller 离线 wheel 仓              （所有三方包的唯一来源）
③ 子系统只读基础环境                     （numpy/scipy 等重包，站点注册时预取）
④ TPS 锁哈希叠加环境                     （只装差异包，只读、可多版本并存）
⑤ Controller 自动生成锁文件               （扫描用例 import，作者不写依赖清单）
```

四条不可妥协的规则：

1. **环境键 = TPS 编号 + 锁文件哈希**。锁不变不重建；同一 TPS 的多个哈希并存，回滚就是切换键。
2. **不"随用随清"**。保留最近 2 个环境 + 全局磁盘上限 + LRU；正在运行的环境不回收。清空等于放弃复现与回滚。
3. **Runner SDK 不装进环境**。`ate_dm` / `ate_drivers` / `ate_rt` 由 Runner 以路径注入，锁文件只声明兼容版本范围；Runner 升级不会破坏三方环境。
4. **锁文件不许手写**。Controller 扫描 TPS 内 Python 文件的 import 自动生成；作者只写业务代码，不看依赖清单。

## 2. 为什么否决另外三种做法

| 做法 | 表面好处 | 致命问题 |
| --- | --- | --- |
| 打包进 Runner 安装包，一次装完 | 最简单、最彻底离线 | 全体 TPS 共用一个 `site-packages`，正是要避免的版本冲突；每加一个包都要重发整包，产线不接受 |
| 每个子系统一个 venv | 环境数少、磁盘省 | 子系统不是隔离边界。A 台跑 TPS1 升级了包，B 台跑 TPS2 就无法复现上次结果；共享环境被任一次部署改写，无法按 TPS 回滚 |
| 每个 TPS 一个 venv，随用随清 | 隔离最好、不占磁盘 | 每次重建都要重新装包，首件等待长；清掉后日志里的环境无法复原；出问题不能回看 |

推荐方案保留"每 TPS 隔离"的优点，用**只读 + 锁哈希 + 缓存**换掉"每次重建"和"清空"的代价。

## 3. 目录与命名

### Controller

```
software/python/
├── wheels/
│   └── numpy/2.1.3/cp313-win_amd64/numpy-2.1.3-cp313-cp313-win_amd64.whl
├── base/
│   └── 1000-A/SPM/cp313-win_amd64/
│       ├── env.zip              # 只读基础环境（venv 目录打包）
│       └── manifest.json        # 包清单、哈希、Python/平台标签
└── locks/
    └── <tpsId>/<lockHash>.json  # Controller 自动生成后固化的副本（便于追溯）
```

- 目录层级固定为 `wheels/<name>/<version>/<pythonTag>-<platformTag>/`，同名多版本自然并存。
- `base` 的键是 `产品/子系统/Python标签-平台标签`；不同子系统可各有一份。
- `locks` 下的副本只读，锁文件改动即产生新文件。

### Runner

```
envs/
├── base/<product>-<subsystem>-<pyTag>-<plat>/
│   ├── venv/                    # 解压自 base/env.zip，只读
│   └── ready.json               # 指纹：manifest 哈希 + 解压时间
└── tps/<tpsId>/<lockHash>/
    ├── venv/                    # 复制 base 后只装差异包，只读
    ├── ready.json               # 指纹：lockHash + 包清单哈希
    └── build.log                # 构建日志（失败时保留）
```

## 4. 锁文件 `python.lock`

放在 TPS 包根目录，与 `tps.json` 同级。**由 Controller 在发布流程内自动生成，作者不创建、不编辑**；人工改过的文件在下次发布时会被重算覆盖。

```json
{
  "schema": "ate.python.lock/v2",
  "tps_id": "spm_rh_dyn",
  "tps_version": "3.1.0",
  "runtime": {
    "implementation": "cpython",
    "python": "3.9.11",
    "abi": "cp39",
    "platform": "win_amd64"
  },
  "catalog": {
    "rev": 42,
    "source": "manufacturing-version-server"
  },
  "sdk": {
    "name": "atefr",
    "version": "1.0.0",
    "range": ">=1.0,<2.0"
  },
  "packages": [
    {
      "name": "numpy",
      "version": "1.26.4",
      "filename": "numpy-1.26.4-cp39-cp39-win_amd64.whl",
      "sha256": "9f2c...",
      "size": 15873221,
      "tags": ["cp39", "cp39", "win_amd64"],
      "top_level": ["numpy"],
      "implicit": false,
      "reason": "default"
    }
  ],
  "generated_at": "2026-11-24T10:15:30+08:00",
  "generated_by": "controller",
  "generator_version": "1.0.0"
}
```

可直接复制的空白模板见 [python.lock.template.json](python.lock.template.json)。模板只用于阅读和联调，TPS 内的锁仍由 Controller 生成。

字段规则：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `schema` | 是 | 固定 `ate.python.lock/v2` |
| `runtime.python` | 是 | 精确到补丁版本，当前基线 `3.9.11` |
| `runtime.abi` / `platform` | 是 | `cp39` 与 `win_amd64` / `linux_x86_64`，必须与 wheel 标签一致 |
| `catalog.rev` | 是 | 生成锁时依据的备案目录版本，用于审计 |
| `sdk` | 是 | 平台公共代码名称、版本与兼容范围，启动时校验 |
| `packages[]` | 是 | 允许为空数组（纯标准库 TPS） |
| `packages[].sha256` | 是 | 单个 whl 的内容哈希，下载与安装前逐个校验 |
| `packages[].filename` | 是 | 仅文件名；Controller 按 sha256 定位 blob |
| `packages[].tags` | 是 | `[python_tag, abi_tag, platform_tag]` |
| `packages[].implicit` | 是 | `true` 表示由平台 SDK 带入的隐式依赖 |

### 4.1 生成流程

```
上传 TPS zip
  → 解包，定位包内全部 .py
  → AST 解析 import / from ... import
  → 分类：标准库（3.9）/ Runner SDK / 包内模块 / 三方模块
  → 三方模块反查发行包（备案目录 top_level.txt、RECORD）
  → 选版（备案目录 default 优先；已有锁版本仍有效则保留）
  → 传递闭包（wheel METADATA 的 Requires-Dist，过滤环境标记）
  → 冲突、缺包、平台不符则发布失败
  → 写 python.lock 并封存进 TPS
```

生成在发布动作内完成，没有单独的"生成锁文件"按钮；用户看到的只是"发布成功"。

### 4.2 模块 → 发行包反查

import 名与发行包名经常不一致（`cv2` → `opencv-python`、`yaml` → `PyYAML`、`sklearn` → `scikit-learn`），不能靠猜。Controller 在登记 wheel 时解析：

1. `.dist-info/top_level.txt`（首选）；
2. `.dist-info/RECORD` 中的顶层目录；
3. `.dist-info/METADATA` 的 `Name` 作为发行包名。

据此建立 `import 名 → (发行包, 版本)` 索引；同一 import 名被多个发行包占用时，发布直接报歧义，要求上传方确认。

### 4.3 选版规则

| 优先级 | 规则 |
| --- | --- |
| 1 | 已有锁中的版本仍为 `approved` → 保留，不因目录新增版本而升级 |
| 2 | 新出现的包 → 取备案目录中 `is_default=1` 的版本 |
| 3 | 传递依赖按同样规则逐个选版，已选定的版本不再被后续依赖改写 |
| 4 | 两个直接依赖对同一包提出互斥范围 → 发布失败，列出两条依赖链 |

不做"自动升降级兜底"：解不出来就失败，把问题留在发布阶段。

### 4.4 动态导入与人工补充

`__import__("cv2")`、`importlib.import_module(name)` 无法静态确定，扫描只能记告警。此时允许在 TPS 包根放一个可选的 `python.extra.txt`：

```
# 一行一个 import 名，不写版本
cv2
serial
```

Controller 把它并入扫描结果后照常选版；**仍然不接受版本号**，版本一律由 Controller 决定。

### 4.5 生成记录

每次发布保存一份 `generation.json`（不进 TPS 包，存在 Controller 侧）：

```json
{
  "scanned_files": 14,
  "imports": ["numpy", "cv2", "serial"],
  "resolved": [
    { "module": "cv2", "package": "opencv-python", "version": "4.10.0.84", "reason": "catalog-default" },
    { "module": "numpy", "package": "numpy", "version": "2.1.3", "reason": "base-env" }
  ],
  "warnings": ["dynamic import in testcase/impl.py:42"],
  "generated_at": "2026-11-08T10:00:00+08:00"
}
```

用于回答"为什么装了这个版本"，也用于对比两次发布的环境差异。

### 4.6 模板文件

本目录的 [python.lock.template.json](python.lock.template.json) 是 v2 空白模板，字段与本节一致。

使用方式：

```powershell
# 仅用于本地阅读、联调或 Mock；正式 TPS 的锁由 Controller 发布时生成
Copy-Item docs\python三方\python.lock.template.json <TPS目录>\python.lock
```

模板中的 `REPLACE_*`、`0`、`REPLACE_ISO8601` 必须替换为真实值；`packages` 至少保留一项，纯标准库 TPS 改为空数组。

## 5. Controller 侧职责

### 5.1 依赖登记

```
POST /api/v1/python/wheels
{ "file_base64": "...", "filename": "numpy-2.1.3-cp313-cp313-win_amd64.whl", "uploaded_by": "engineer" }
```

服务端做四件事：解析 wheel 文件名得到 `name/version/pyTag/platTag`；校验 zip 内 `*.dist-info/METADATA`；算 sha256；落盘到 `wheels/...` 并写库。**平台标签缺失或与文件名冲突时拒绝登记**，并提示应上传哪个平台的 wheel。

### 5.2 基础环境制品

由一台"制作者"机器（可联网）执行：

```powershell
python -m venv build/base-1000-A-SPM
build\base-1000-A-SPM\Scripts\pip install numpy==2.1.3 scipy==1.14.1 pandas==2.2.3
build\base-1000-A-SPM\Scripts\pip freeze > build\base-1000-A-SPM\requirements.lock
# 打包（用相对路径解压，不要带绝对路径前缀）
Compress-Archive -Path build\base-1000-A-SPM\* -DestinationPath dist\base-cp313-win_amd64.zip
```

上传后 Controller 计算哈希并登记 `base` 键。**基础环境只读**：要升级就出新的版本键（如 `cp313-win_amd64@2026.11`），不要原地改。

### 5.3 发布校验

TPS 发布时先**自动重算** `python.lock`（忽略包内旧文件），再逐项检查：

1. `python` / `platform` 与目标站点一致；
2. 每个 `packages[].wheel` 在仓内存在且 sha256 相同；
3. 同包多版本 → 拒绝并列出两个来源；
4. `sdk.range` 能被当前 Runner SDK 版本满足；
5. `base` 存在（或为 `none`）。

任一项失败即**拒绝发布**，不允许"先发下去现场再说"。

## 6. Runner 侧流程

### 6.1 站点注册：预取基础环境

```
POST /api/runner/env/prefetch { "product": "1000-A", "subsystem": "SPM" }
→ Runner 下载 base/env.zip → 解压到 envs/base/... → 校验 manifest 哈希 → 写 ready.json
```

- 已存在同版本直接跳过；哈希不符删除重下。
- 预取失败不阻断注册，但标记 `env_ready=false`，执行时会再拦一次。

### 6.2 执行前：就绪检查

```
lockHash = sha256(python.lock 规范化后内容)[:12]
若 envs/tps/<tpsId>/<lockHash>/ready.json 存在且指纹一致 → 直接运行
否则：
  1) 确保 base 就绪（缺失则先预取）
  2) 复制 base/venv → tps/<tpsId>/<lockHash>/venv
  3) 计算差异包（lock.packages - base 清单）
  4) pip install --no-index --find-links <本地 wheels> -r delta.txt
  5) 校验实际包清单 == lock；写 ready.json
```

关键命令（Windows，Runner 内实际执行）：

```bat
set PIP_NO_INDEX=1
set PIP_FIND_LINKS=%ENV_WHEELS%
"%ENV_PY%" -m pip install --no-index --find-links "%ENV_WHEELS%" --require-hashes -r delta.txt
```

- `delta.txt` 由 Runner 生成，每行 `name==version --hash=sha256:...`。
- 安装失败：**保留整个环境目录与 build.log**，把 stderr 末尾 2 KB 回传界面；不要自动删目录，否则无法诊断。
- 成功：`ready.json` 记录 `lockHash`、包清单哈希、构建时间、Runner 版本。

### 6.3 运行：隔离与 SDK 注入

```bat
set "PYTHONPATH=%ENV_ROOT%\venv\Lib\site-packages;%RUNNER_SDK%"
"%ENV_ROOT%\venv\Scripts\python.exe" -m pytest ...
```

- **不要**给 venv 加 `--system-site-packages`，否则 Runner 的 fastapi/uvicorn 会漏进 TPS，版本冲突又回来了。
- 不修改系统 `PATH`、不写注册表；环境变量只在子进程内生效。
- SDK 版本越界时启动即失败，提示需要的范围与实际版本，不尝试自动降级。

## 7. 冲突与平台规则

| 场景 | 处理 |
| --- | --- |
| 同环境同包两个版本 | 发布即拒绝，列出两个来源与依赖链 |
| wheel 平台与 `platform` 不符 | 拒绝登记/发布，提示应上传的平台标签 |
| Python 小版本不符（3.12 lock 跑在 3.13） | 拒绝启动，提示重建锁文件；不做跨小版本复用 |
| 只有 sdist、无 wheel | 默认拒绝。需要时由制作者在联网机编译出 wheel 再登记 |
| 两个 TPS 依赖冲突 | 各自环境隔离，互不影响（这是本方案的核心收益） |
| 基础环境与 TPS 差异包版本冲突 | 叠加环境里显式安装的版本优先；`pip` 会替换 base 中的同名包，属预期行为，需在锁文件注释说明 |

## 8. 日常操作

### 新增一个依赖

用例里直接 `import numpy`，**不需要改任何依赖文件**：

```powershell
# 1) 联网机上准备 wheel（仅当仓里没有该包时）
pip download numpy==2.1.3 --only-binary=:all: --platform win_amd64 --python-version 3.13 -d wheels\numpy
# 2) 上传 Controller（或用归档助手的 python 子命令）
# 3) 重新发布 TPS：Controller 扫描 import、自动选版、生成锁文件
# 4) Runner 首次执行按新锁构建叠加环境
```

若包已登记，第 1、2 步可跳过——作者只提交代码。

### 回滚到上一版环境

```powershell
# 不用改锁文件：在 Controller 重新发布上一个版本的 TPS 包
# 旧锁会随旧包一起重算，得到旧 lockHash；Runner 发现该环境已存在，直接复用
```

### 诊断

```powershell
envs\tps\<id>\<hash>\venv\Scripts\python.exe -m pip list --format=freeze > actual.txt
# 与 python.lock 比对；Runner 界面「环境自检」等价于做这件事
```

### 回收

- 默认每 TPS 保留最近 2 个 `lockHash`；
- 全局上限默认 20 GB，超限按 LRU 删最久未用；
- 正在运行的环境加锁，不参与回收；
- 回收动作写日志（时间、环境键、释放空间），便于审计。

## 9. 体量估算

| 项 | 估算 | 说明 |
| --- | --- | --- |
| 基础环境（numpy/scipy/pandas/opencv 的一部分） | 300–500 MB / 子系统 | 压缩后约 1/3 |
| 单 TPS 叠加层 | 20–80 MB | 仅差异包 |
| 每站点常驻 | 1–2 个基础环境 + 每 TPS 最近 2 个叠加层 | 10 个 TPS 约 2–4 GB |
| Controller wheel 仓 | 每个包 5–60 MB | 按子系统常用集合估算 2–5 GB |

## 10. 落地顺序

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P0 | Controller wheel 登记 + 扫描 import 自动生成锁文件；Runner 手工命令建环境 | 断网机器能凭 wheel 装出可运行环境，锁文件无需手写 |
| P1 | 基础环境制品与站点注册预取；环境指纹与就绪检查 | 新站点注册后首件 TPS 无需等待下载 |
| P2 | 叠加环境自动构建、LRU 回收、环境自检界面 | 同 TPS 两版锁文件可并存并切换回滚 |

P0 不依赖任何新界面，可以先用一份 `wheelhouse/` 目录 + 一条 `pip --no-index` 命令验证整条思路。

## 11. 常见问题

**Q：能不能直接把开发机的 `.venv` 拷到测试台？**
不建议。venv 里记录的是创建时的绝对路径，换盘符或换用户名就可能失效；且体积大、含无关包。用 wheel 仓 + 锁文件重建，几秒钟到几分钟，结果可复现。

**Q：基础环境要不要带 pip？**
要。叠加环境需要 pip 安装差异包；但它只在构建阶段用，运行阶段不暴露给 TPS 代码。

**Q：为什么不把 base 直接做成多个 wheel 的集合，每次全量装？**
全量装 numpy/scipy 每次几十秒到几分钟，首件等待不可接受；只读 base + 差异包是折中。

**Q：三方包需要编译（C 扩展）怎么办？**
在联网的制作者机器上编译成 wheel 再登记。测试台不装编译器，也不允许联网拉源码。

**Q：作者需要写 `python.lock` 或 requirements 吗？**
不需要。锁文件由 Controller 扫描 import 自动生成，作者只写业务代码。`python.extra.txt` 只在动态导入无法识别时补充模块名，也不写版本。

**Q：自动选版会不会把版本悄悄换掉？**
不会。锁文件在发布时固化，未重新发布就不会变；重新发布时如果仓里出现新版，选版结果会变化，差异可在 `generation.json` 与两次锁文件对比中看到。需要固定版本时，把该版本放进子系统基础环境即可。

**Q：扫描漏了包怎么办？**
优先检查是否动态导入（告警会指出文件与行号）；用 `python.extra.txt` 补模块名后重发。若包本身没登记，发布会直接说缺哪个包与平台。

**Q：Runner 自己升级了，TPS 环境要重建吗？**
不需要。SDK 走路径注入，锁文件只约束范围；只有 SDK 不兼容时才需要改锁并重建。

**Q：磁盘不够怎么办？**
先调低"每 TPS 保留数"到 1，再按 LRU 清历史；不要删 base，否则所有 TPS 都要重装。

## 12. 验收清单

- [ ] 断网机器上，凭 Controller 的 wheel 仓能装出一个可运行环境
- [ ] 改一个字节的 wheel 被哈希校验拦截
- [ ] 同包两版本在发布阶段被拒，错误信息含两个来源
- [ ] 同一 TPS 两个锁哈希并存，切换后结果可复现
- [ ] 环境未就绪时执行被拒，提示缺失的基础环境或包
- [ ] TPS 代码看不到 Runner 的 site-packages，但能 import `ate_dm`
- [ ] 回收后保留最近 2 个环境，正在运行的不被删
- [ ] 环境构建失败保留目录与日志，可直接定位
- [ ] 只有 import、没有依赖文件时也能生成正确锁文件
- [ ] `cv2` / `yaml` / `sklearn` 能反查到正确发行包
- [ ] 发布成功的界面不出现"编辑依赖 / 锁文件"入口
- [ ] 动态导入给出文件与行号告警

## 13. 未决事项

- 是否引入 `uv` 替代 pip 以缩短构建时间（需评估离线与哈希校验兼容性）。
- 基础环境是否需要签名（当前只做 sha256 完整性，不做来源签名）。
- 跨架构（win_amd64 / linux_x86_64）是否共用一份锁文件（当前按平台各一份）。
- 动态导入（插件加载、配置驱动）无法完全静态识别，是否需要运行期探测补齐。
- 只有 sdist 的包无法自动选版，当前要求先由制作者编译上传 wheel。
- 用例条件导入（按平台分支）会同时收进锁文件，是否按目标平台裁剪待定。
