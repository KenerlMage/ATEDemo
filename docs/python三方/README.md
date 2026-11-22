# Python 三方依赖推荐方案（详细）

> 适用：MockController + MockRunner 部署在**无外网**的测试台小网环境。
> 对应需求：`docs/需求设计/controller/软件部署功能.md` 的 SR03（Controller 供给）与 SR04（Runner 执行）。
> 版本：v1.0　2026-11-08

## 1. 结论

采用**四层**结构，而不是把三方包塞进 Runner 安装包，也不是"每个 TPS 用完即删"：

```
① Runner 自带 Python 解释器与自身依赖   （不装 TPS 的三方包）
② Controller 离线 wheel 仓              （所有三方包的唯一来源）
③ 子系统只读基础环境                     （numpy/scipy 等重包，站点注册时预取）
④ TPS 锁哈希叠加环境                     （只装差异包，只读、可多版本并存）
```

三条不可妥协的规则：

1. **环境键 = TPS 编号 + 锁文件哈希**。锁不变不重建；同一 TPS 的多个哈希并存，回滚就是切换键。
2. **不"随用随清"**。保留最近 2 个环境 + 全局磁盘上限 + LRU；正在运行的环境不回收。清空等于放弃复现与回滚。
3. **Runner SDK 不装进环境**。`ate_dm` / `ate_drivers` / `ate_rt` 由 Runner 以路径注入，锁文件只声明兼容版本范围；Runner 升级不会破坏三方环境。

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
    └── <tpsId>/<lockHash>.json  # 发布时固化的锁副本（便于追溯）
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

放在 TPS 包根目录，与 `tps.json` 同级。JSON 便于 Controller 校验与 diff。

```json
{
  "schema": "ate.python.lock/v1",
  "python": "3.13",
  "platform": "win_amd64",
  "base": "1000-A/SPM/cp313-win_amd64",
  "sdk": { "name": "ate-sdk", "range": ">=2.3,<3.0" },
  "packages": [
    {
      "name": "numpy",
      "version": "2.1.3",
      "sha256": "9f2c...",
      "wheel": "numpy/2.1.3/cp313-win_amd64/numpy-2.1.3-cp313-cp313-win_amd64.whl",
      "source": "mcs-registry"
    },
    {
      "name": "opencv-python",
      "version": "4.10.0.84",
      "sha256": "1a7b...",
      "wheel": "opencv-python/4.10.0.84/cp313-win_amd64/opencv_python-4.10.0.84-cp313-cp313-win_amd64.whl",
      "source": "mcs-registry"
    }
  ],
  "notes": "视觉检测 TPS 专用；numpy 固定在 2.x"
}
```

字段规则：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `schema` | 是 | 固定 `ate.python.lock/v1`，后续不兼容变更升 v2 |
| `python` | 是 | 只写 `major.minor`；小版本由 Runner 解释器决定 |
| `platform` | 是 | `win_amd64` / `linux_x86_64`；与 wheel 标签一致 |
| `base` | 是 | 使用的子系统基础环境键；没有基础环境时填 `none` |
| `sdk` | 是 | Runner SDK 兼容范围，启动时校验 |
| `packages[]` | 是 | 允许为空数组（纯标准库 TPS） |
| `packages[].sha256` | 是 | 与 Controller 仓内文件一致，安装前逐个校验 |
| `packages[].wheel` | 是 | 相对 `wheels/` 的路径，避免同名文件歧义 |

**哈希怎么来**：归档时由 Controller 计算并回填，作者不要手填。`python.lock` 生成命令见第 8 节。

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

TPS 发布时对 `python.lock` 逐项检查：

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

```powershell
# 1) 联网机上准备 wheel
pip download numpy==2.1.3 --only-binary=:all: --platform win_amd64 --python-version 3.13 -d wheels\numpy
# 2) 上传 Controller（或用归档助手的 python 子命令）
# 3) TPS 里更新 python.lock，重新发布；Runner 首次执行自动构建叠加环境
```

### 回滚到上一版环境

```powershell
# 锁文件改回旧内容（或从 git 取回），得到旧 lockHash
# Runner 发现该环境已存在，直接复用，无需重新安装
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
| P0 | Controller wheel 登记 + 锁文件校验；Runner 手工命令建环境 | 断网机器能凭 wheel 装出一个可运行环境 |
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

## 13. 未决事项

- 是否引入 `uv` 替代 pip 以缩短构建时间（需评估离线与哈希校验兼容性）。
- 基础环境是否需要签名（当前只做 sha256 完整性，不做来源签名）。
- 跨架构（win_amd64 / linux_x86_64）是否共用一份锁文件（当前按平台各一份）。
