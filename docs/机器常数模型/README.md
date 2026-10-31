# 机器常数模型

机器常数是整机标定后产生、供 TPS 执行时填充的一组数值。本模块把它拆成两类对象管理：

| 对象 | 含义 | 何时产生 |
| --- | --- | --- |
| 常数模型（结构） | 字段清单：字段标识、名称、类型、单位、上下限、默认值 | 标定前定义，随版本发布 |
| 常数（值） | 每个字段的实测标定值 | 标定过程中生成，入库保存 |

结构由版本管理，值挂在结构之下。二者都不写进 TPS：TPS 只保存「关联到哪个版本、哪个 PN、哪些模型」，
执行时由平台按绑定取值，测试结束后输出 XML。

## 组织维度

```
产品（装备树第一层）
└── 子系统（装备树第二层）
    └── PN 号（一台整机 / 一个部件的物料号）
        └── 常数模型（可多个）
```

PN 的归属优先取导入包中显式声明的 `product` / `subsystem`；未声明时按 PN 前缀匹配子系统编号
（如 `SPM-3200-A1` 归到 `SPM`），仍匹配不到则进入「未归类（未挂接装备树）」节点。

## 版本与覆盖

一次全量导入即一个版本（`version` 字段，如 `V2.1.0`）：

* 新版本导入后自动成为生效版本，旧版本保留可查、可切回；
* 同版本再次导入按所选策略处理：
  * **覆盖导入**（默认）——替换该版本的结构；若某字段在新结构中仍存在，其标定值保留，多余值丢弃；
  * **拒绝重复**——直接报错，需改版本号后再导。
* 覆盖只影响该版本，不波及其它版本的记录。

## 导入格式

### 形态一：分组（推荐）

```json
{
  "version": "V2.1.0",
  "label": "2026Q4 整机标定",
  "source": "TB-03 标定工位 / 2026-10-28",
  "groups": [
    {
      "product": "1000-A",
      "subsystem": "SPM",
      "pn": "SPM-3200-A1",
      "models": [
        {
          "model_key": "SPM_PWR_TRIM",
          "name": "电源纹波补偿系数",
          "category": "电源",
          "fields": [
            { "key": "k_gain", "label": "增益系数", "type": "float",
              "min": 0.8, "max": 1.2, "default": 1.0, "calibrated_by": "频率响应标定" }
          ]
        }
      ]
    }
  ]
}
```

### 形态二：扁平列表

```json
{ "version": "V2.1.0", "models": [ { "pn": "SPM-3200-A1", "model_key": "SPM_PWR_TRIM", "fields": [ ... ] } ] }
```

### 形态三：ZIP

包内放 `constants.json`（或 `manifest.json` / `machine_constants.json`）作为入口；
没有入口文件时，合并包内全部 `*.json`（可多个分片，`groups` 会拼接）。

### 字段定义约束

| 键 | 必填 | 说明 |
| --- | --- | --- |
| `key` | 是 | 字段标识，字母/数字/下划线/点/短横线，同一模型内唯一 |
| `label` | 否 | 中文名，缺省取 `key` |
| `type` | 否 | `float` / `int` / `str` / `bool` / `enum`，缺省 `float` |
| `unit` | 否 | 单位，随值一并落库并写入 XML |
| `min` / `max` | 否 | 数值型上下限，仅作结构与导入校验参考 |
| `default` | 否 | 未标定时的兜底值；类型须与 `type` 一致 |
| `options` | 否 | `enum` 的候选值 |
| `calibrated_by` | 否 | 由哪道标定工步产生，便于追溯 |
| `desc` | 否 | 说明 |

`model_key` 为模型标识（如 `SPM_PWR_TRIM`），同一 PN 同一版本内唯一。
导入校验采取**严格失败**：未知类型、重复字段、非法标识一律整体拒绝，不做静默丢弃。

## 与 TPS 的关联

TPS 清单中新增可选字段：

```json
"constant_model": {
  "version": "V2.1.0",
  "pn": "SPM-3200-A1",
  "models": ["SPM_PWR_TRIM", "SPM_THERM"],
  "updated_at": "2026-10-31 18:20:00"
}
```

* 关联在「TPS 创建 → 机器常数关联」中选择版本 / PN / 模型，写入清单文件本身，随 TPS 一起流转；
* 未填 `models` 时表示该 PN 下全部模型；
* 执行开始前平台按绑定解析出值与缺失项（缺值用 `default` 兜底，并在 XML 中标记 `calibrated="false"`）；
* 执行结束后生成本轮常数 XML（`reports/constants/constants_<task_id>.xml`），路径写回任务对象的 `constant_xml` 字段。

### XML 结构

```xml
<?xml version="1.0" encoding="UTF-8"?>
<MachineConstants schema="ate.constants.v1" version="V2.1.0" pn="SPM-3200-A1" generatedAt="2026-10-31 18:31:02">
  <Header>
    <Item name="tpsId" value="spm_rh_dyn" />
    <Item name="uut" value="ABC123DEF456" />
    <Item name="result" value="OK" />
  </Header>
  <ConstantSets count="2">
    <ConstantSet modelKey="SPM_PWR_TRIM" name="电源纹波补偿系数" unit="" filled="2/3">
      <Field key="k_gain" label="增益系数" type="float" unit="" source="calib" calibrated="true">1.0123</Field>
      <Field key="k_offset" label="偏置修正" type="float" unit="mV" source="calib" calibrated="true">-0.35</Field>
      <Field key="mode" label="补偿模式" type="enum" unit="" source="default" calibrated="false">linear</Field>
    </ConstantSet>
  </ConstantSets>
  <Summary models="2" fields="5" filled="3" missing="2">
    <Missing modelKey="SPM_THERM" key="t_coef" label="温度系数" />
  </Summary>
</MachineConstants>
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/constants/overview?version=` | PN 维度的装备树 + 统计 + 版本列表 + 生效版本 |
| GET | `/api/constants/versions` | 版本列表（含模型数 / 字段数 / 值数量 / 是否生效） |
| POST | `/api/constants/versions/active` | 切换生效版本 `{version}` |
| GET | `/api/constants/pn?pn=&version=` | 某 PN 下的模型结构 + 已存标定值 |
| POST | `/api/constants/preview` | 导入前解析预览（不落库），返回冲突信息 |
| POST | `/api/constants/import` | 全量导入 `{content \| content_base64, filename, mode, keep_values}` |
| POST | `/api/constants/values` | 写入 / 更新标定值 `{version, pn, model_key, values, source}` |
| POST | `/api/constants/resolve` | 按绑定解析填充值（含缺失项） |
| POST | `/api/constants/xml` | 生成 XML 预览（不落盘） |
| GET | `/api/tps/{id}/constants` | 读取 TPS 的常数关联与解析结果 |
| PUT | `/api/tps/{id}/constants` | 写入 / 解除常数关联 `{binding}` |

## 数据落库

`backend/ate.db`（可用 `ATE_DB_PATH` 覆盖）三张表：

| 表 | 作用 | 关键约束 |
| --- | --- | --- |
| `constant_version` | 版本头：标签、来源、统计、导入时间、是否生效、结构校验和 | `version` 主键 |
| `constant_model` | 模型结构：PN、归属、`fields_json`、字段数、结构校验和 | `(version, pn, model_key)` 唯一 |
| `constant_value` | 标定值：按字段存字符串 + 单位 + 来源 + 时间 | `(version, pn, model_key, field_key)` 主键 |

值以文本存储并在读取时原样返回：浮点精度由写出方（标定程序）决定，平台不做二次舍入，
避免「标定值 1.0123 被存成 1.01」这类精度损失。

## 使用路径

1. **标定前**：按 PN 准备结构包（可先只给 `fields`，不含值），导入得到版本 V1；
2. **标定中**：标定程序按字段回填值（`POST /api/constants/values`），或在结构包内直接带 `values`；
3. **编 TPS**：在「TPS 创建」里关联版本 / PN / 模型；
4. **执行**：平台按绑定解析并填充；
5. **归档**：每轮结束生成常数 XML，随报告一起留存。

示例包见同目录 `示例-常数模型-全量包.json`（可直接粘贴到导入向导，或改扩展名为 `.json` 上传）。
