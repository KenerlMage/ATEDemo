# 《Python 三方依赖管理（Controller）》需求设计

## 一、范围与决策

本轮覆盖 Controller 侧的 Python 三方依赖治理：制品仓、签名备案目录、包与版本目录、用例依赖识别、锁生成、发布校验、撤销隔离与按哈希分发。不覆盖 Runner 的环境构建与执行，不实现从 PyPI 直接拉包，不在 Controller 保存 venv。

现状对账：Controller 现有 `/api/packages` 只服务 TPS 与装备软件的 `package.zip`，`software_archive` 的 `kind` 只有 assistant/driver/tps/thirdparty，均不适用于按 sha256 管理 whl；本轮新增独立模块与表，不复用旧归档通道。

## 二、需求清单

### SR01 Python 三方依赖治理（Controller）

【需求价值】现场无外网，三方包来源、版本与准入必须由平台统一收口，避免版本漂移与未备案包进场。
【设计目标】Controller 成为制造版本服务器的受控镜像，只存备案 whl，按 TPS 生成 python.lock，并按哈希向 Runner 分发。
【需求描述】覆盖制品仓、签名备案目录、包与版本目录、用例依赖识别、备案内选版、锁生成、发布完整性校验、撤销隔离、按哈希分发与审计；不负责构建 venv。

#### SR01-AR01 制品仓与内容寻址

【需求价值】同一 whl 只存一份，重复上传不占空间，文件损坏能被发现。
【设计目标】以 sha256 为主键保存原装 whl，支持去重、断点下载与完整性校验。
【需求描述】上传或同步的 whl 先落临时区，校验文件名标签、METADATA 与 sha256 后原子入库；同一 sha 只保留一份；按前四位分片存储；隔离文件不对外提供。

#### SR01-AR02 签名备案目录导入与激活

【需求价值】三方库准入由制造侧统一审批，Controller 不能自行放宽标准。
【设计目标】仅接受制造版本服务器签名的备案目录，按 rev 原子激活，旧 rev 可回滚。
【需求描述】导入时验签、校验 rev 单调与 python/platform 匹配；条目含包名、版本、默认标记、状态、license 与审批单号；激活为原子切换，失败保留旧目录；未签名或校验失败整包拒绝。

#### SR01-AR03 包与版本目录

【需求价值】工程师能查到某库有哪些备案版本、各平台文件与被哪些 TPS 使用。
【设计目标】按包名聚合展示版本、默认版本、wheel 标签、sha256、状态与引用关系。
【需求描述】支持按名称、版本与 sha 搜索；展开显示每个平台 wheel 的文件名、大小、标签、license 与审批单号；显示默认版本与其他备案版本；普通用户不可修改版本。

#### SR01-AR04 用例依赖识别

【需求价值】依赖来自真实 import，避免作者漏写依赖或写错发行包名。
【设计目标】扫描 TPS 内全部 Python 文件，排除标准库、平台 SDK 与包内模块，得到三方模块集合。
【需求描述】递归解析 .py 的 import 与 from；标准库按 3.9 清单排除；平台 SDK 记为隐式依赖；包内相对导入排除；动态导入记告警并支持 python.extra.txt 补模块名。

#### SR01-AR05 备案内选版与闭包

【需求价值】版本只在备案范围内选择，既不漂移也不越权引入未审批版本。
【设计目标】按默认版本选版并补齐传递依赖，冲突在发布前暴露。
【需求描述】模块名经 top_level 反查发行包；每个包取备案目录 default；已有锁的版本仍有效则保留；读 Requires-Dist 补闭包；同包多版本、Python 或平台不符即失败并列出依赖链。

#### SR01-AR06 锁文件生成与指纹

【需求价值】TPS 每次运行装的是同一组文件，测试结果可复现。
【设计目标】生成 python.lock，记录精确版本、sha256、运行时与备案 rev，作为环境指纹。
【需求描述】锁含 schema、runtime、catalog、sdk 与 packages；每个包写 filename、sha256、size、tags；记录 source_digest 防代码与锁不一致；生成后只读，重新发布才更新。

#### SR01-AR07 发布完整性校验

【需求价值】锁里每个包在 Controller 都有文件，Runner 不会运行到一半才发现缺包。
【设计目标】发布前逐个校验 sha 是否在本地 blob，缺失即拒绝发布。
【需求描述】解析锁后检查每个 sha 的存在性与状态；缺失时从制造服务器拉取，仍缺则拒绝；同时校验平台标签、拒绝 sdist、锁与 TPS 代码摘要一致；通过后才归档 TPS。

#### SR01-AR08 撤销与隔离

【需求价值】发现问题版本能立即止血，同时保留已归档证据。
【设计目标】条目撤销或制品隔离后，新锁不再选中，下载返回明确状态码。
【需求描述】支持按包版本撤销、按 sha 隔离；撤销后新解析排除该条目；隔离制品下载返回 410；已有锁标记风险并按策略阻断执行；操作记录原因、操作人与时间。

#### SR01-AR09 按哈希分发

【需求价值】Runner 不需要解析版本，直接按锁取文件，减少现场歧义。
【设计目标】提供按 sha256 取单文件的接口，支持 HEAD、Range 与批量预检。
【需求描述】GET /artifacts/{sha256} 返回原装 whl 或 404/410；响应带 ETag、文件名与 immutable 缓存头；HEAD 返回大小；批量接口只返回可用性；运行时接口不按包名直接取文件。

#### SR01-AR10 审计与同步记录

【需求价值】每次准入、发布与下载异常都能追溯，审计不靠人回忆。
【设计目标】记录目录导入、激活、锁生成、撤销、隔离与下载失败，可按 TPS、包或 sha 查询。
【需求描述】记录动作、对象、操作人、时间、结果与原因；同步记录含 rev、签名、缺失与耗时；下载失败按 sha 聚合；审计日志只增不改，保留期可配置。

## 三、方案设计

### 3.1 模块与存储（SR01-AR01、AR02）

新增 `python_packages` 模块，按现有 `register_routes(app, base_dir, db_path)` 模式挂载，不改 `software.py`：

```
python_packages/
├── api.py
├── artifacts.py
├── catalog.py
├── resolver.py
├── lifecycle.py
├── models.py
└── manufacturing.py
```

存储只保留原装文件，不做二次打包：

```
repo/python/
├── blobs/sha256/9f/2c/9f2c...        # 唯一物理存储
├── catalog/rev-42.json                # 原样保存的备案目录
├── catalog/rev-42.sig
├── tmp/
└── quarantine/
```

写文件顺序为临时区校验、`os.replace` 入库、事务写库；任何一步失败都不留下半成品。

### 3.2 数据模型（SR01-AR01~AR03、AR06、AR08、AR10）

| 表 | 关键字段 | 作用 |
| --- | --- | --- |
| `py_artifact` | sha256 主键、filename、size、tags_json、status | 一个 whl 一行 |
| `py_catalog_rev` | rev、python、platform、status、signature | 备案目录版本 |
| `py_catalog_entry` | rev、name、version、is_default、status、ticket | 备案条目 |
| `py_catalog_wheel` | rev、name、version、sha256、tags_json | 条目与文件映射 |
| `py_lock` | tps_id、tps_version、lock_hash、lock_sha256、catalog_rev | 锁与审计 |
| `py_module_alias` | module、name | import 名反查发行包 |
| `py_request` | tps_id、module、distribution、status | 未备案申请 |
| `py_audit` | action、actor、target、detail_json | 审计流水 |

`py_artifact` 不存 BLOB，文件只在 `blobs`；`sha256` 同时作为去重键与下载路径。

### 3.3 备案目录同步（SR01-AR02、AR09）

制造版本服务器输出签名目录：

```json
{
  "schema": "ate.python.catalog/v1",
  "rev": 42,
  "python": "3.9",
  "platform": "win_amd64",
  "entries": [
    {
      "name": "numpy",
      "version": "1.26.4",
      "default": true,
      "status": "approved",
      "requires_python": ">=3.9,<3.13",
      "license": "BSD-3-Clause",
      "ticket": "SEC-2026-0187",
      "wheels": [
        {
          "sha256": "9f2c...",
          "filename": "numpy-1.26.4-cp39-cp39-win_amd64.whl",
          "tags": ["cp39", "cp39", "win_amd64"]
        }
      ]
    }
  ]
}
```

Controller 的导入流程：验签 → 校验 rev 单调 → 写 staging → 对比本地 blob 列缺失 → 按 sha 从制造服务器补齐 → 全部就绪后原子激活。导入失败保留旧 rev，不出现半激活状态。

### 3.4 依赖解析与锁生成（SR01-AR04~AR06）

解析只在当前 active 备案目录内进行：

1. AST 扫描 TPS 内全部 `.py`，按 3.9 标准库清单、平台 SDK、包内模块排除；
2. `py_module_alias` 把 `cv2`、`yaml`、`sklearn` 等映射到发行包；
3. 每个发行包取 `is_default=1` 的版本；已有锁中仍 `approved` 的版本优先保留；
4. 读 `Requires-Dist` 补传递依赖，遇到同包多版本即失败；
5. 校验 `cp39` / `abi3` / `py3-none-any` 与目标平台；
6. 生成 `python.lock`，写 `catalog_rev` 与 `source_digest`。

锁的核心字段：

```json
{
  "schema": "ate.python.lock/v2",
  "runtime": {"implementation": "cpython", "python": "3.9.11", "abi": "cp39", "platform": "win_amd64"},
  "catalog": {"rev": 42, "source": "manufacturing-version-server"},
  "packages": [
    {
      "name": "numpy",
      "version": "1.26.4",
      "filename": "numpy-1.26.4-cp39-cp39-win_amd64.whl",
      "sha256": "9f2c...",
      "tags": ["cp39", "cp39", "win_amd64"]
    }
  ]
}
```

锁哈希只对 `runtime` 与 `packages` 的包名、版本、sha256、filename、tags 做规范化计算；`generated_at`、`catalog_rev`、`source_digest` 不参与，避免改注释就重建环境。

### 3.5 发布流程（SR01-AR07）

TPS 发布链路新增四步：

1. 扫描依赖并生成锁预览，返回将安装的包、版本、来源与冲突；
2. 工程师确认后写入 `python.lock`；
3. 逐个检查锁内 sha 是否在本地 blob；缺失则从制造服务器拉取；
4. 任一项缺失或不合法即拒绝发布，错误信息列出包名、sha 与原因。

不允许“先发布、运行时再拉”。发布通过的 TPS 一定能在 Controller 内取齐全部 whl。

### 3.6 分发接口（SR01-AR09）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/python/artifacts/{sha256}` | 返回单个 whl，支持 Range |
| HEAD | `/api/v1/python/artifacts/{sha256}` | 返回大小与 ETag |
| POST | `/api/v1/python/artifacts/check` | 批量检查 sha 是否存在 |
| GET | `/api/v1/python/catalog/current` | 当前备案目录摘要 |
| GET | `/api/v1/python/packages/{name}/{version}` | 列出该版本所有 wheel 与 sha |
| GET | `/api/v1/python/wheels/{name}/{version}/{filename}` | 人工下载入口，302 到 sha 地址 |

响应统一带 `ETag: sha256`、`X-Artifact-Sha256`、`Content-Disposition` 与 `Cache-Control: immutable`。运行时只走 sha 地址，包名路由只用于浏览与排障。

### 3.7 撤销、隔离与审计（SR01-AR08、AR10）

- 条目撤销：`py_catalog_entry.status='revoked'`，新解析排除；已有锁标记风险；
- 制品隔离：`py_artifact.status='quarantined'`，下载返回 410，文件移入 `quarantine/`；
- 所有操作写 `py_audit`，记录对象、操作人、时间、原因与结果；
- 审计日志只增不改，默认保留 2 年。

### 3.8 前端（SR01-AR03、AR08、AR10）

「装备软件」下新增「三方包」二级菜单，四个页签：

| 页签 | 内容 |
| --- | --- |
| 概览 | 当前 rev、包数、artifact 数、占用空间、缺失数、最近同步 |
| 备案目录 | 按包名分组，显示默认版本、其他备案版本、license、审批单号、状态 |
| 包与版本 | 搜索包名，展开看各平台 wheel、sha、被哪些 TPS 引用 |
| 同步记录 | rev、签名、导入时间、缺失与失败明细 |

TPS 详情页增加只读折叠区「Python 环境」，显示运行时、备案 rev、包清单与锁摘要；普通工程师看不到版本选择入口，只在未备案时看到申请编号。

## 四、验收与实测

| 验收点 | 对应 AR | 通过标准 | 状态 |
| --- | --- | --- | --- |
| 相同 whl 重复导入只占一份 | AR01 | `blobs` 文件数不增加，sha 行只有一条 | 待实现 |
| 未签名目录整包拒绝 | AR02 | 返回签名错误，旧 rev 不变 | 待实现 |
| 目录 rev 原子切换 | AR02 | 切换失败时仍指向旧 rev | 待实现 |
| 按包名查询返回多平台 wheel | AR03 | 列表含五个标签与 sha | 待实现 |
| 只写 import 能识别依赖 | AR04 | 锁内包与 import 映射一致 | 待实现 |
| 默认版本优先、旧锁保留 | AR05 | 新包取 default，旧包不升级 | 待实现 |
| 同包多版本发布失败 | AR05 | 错误列出两条依赖链 | 待实现 |
| 锁内 sha 全部可下载 | AR07 | 发布前逐个 HEAD 成功 | 待实现 |
| 缺失包拒绝发布 | AR07 | 错误列出包名与 sha | 待实现 |
| 撤销后新锁不再选中 | AR08 | 重新解析结果不含该版本 | 待实现 |
| 隔离后下载返回 410 | AR08、AR09 | HTTP 410，文件不可取 | 待实现 |
| 按 sha 下载可断点续传 | AR09 | Range 请求返回 206 | 待实现 |
| 审计可按 TPS 与 sha 查询 | AR10 | 返回完整动作链 | 待实现 |

当前只有设计与现状对账，未进入编码，故全部标记待实现。

## 五、未覆盖范围

- Runner 的环境构建、取包缓存与执行校验，见《Python 三方依赖管理（Runner）》。
- 制造版本服务器的接口实现与签名密钥管理，仅约定目录契约。
- PEP 440 版本排序不在 Controller 实现，由制造服务器写入 `version_sort`。
- 动态导入无法完全静态识别，只能告警并要求补 `python.extra.txt`。
- Python 3.9.11 已停止官方维护，安全补丁与基线升级不在本轮范围。
- 站点镜像、Nginx 加速与多 Controller 高可用不在本轮。
- 现有 `software_archive` 不改造，Python 包走独立表与独立路由。

## 六、AR 覆盖核对

| AR | 方案落点 | 状态 |
| --- | --- | --- |
| SR01-AR01 | 3.1、3.2 | 已设计 |
| SR01-AR02 | 3.1、3.3 | 已设计 |
| SR01-AR03 | 3.2、3.8 | 已设计 |
| SR01-AR04 | 3.4 | 已设计 |
| SR01-AR05 | 3.4 | 已设计 |
| SR01-AR06 | 3.2、3.4 | 已设计 |
| SR01-AR07 | 3.5 | 已设计 |
| SR01-AR08 | 3.7、3.8 | 已设计 |
| SR01-AR09 | 3.6 | 已设计 |
| SR01-AR10 | 3.2、3.7、3.8 | 已设计 |
