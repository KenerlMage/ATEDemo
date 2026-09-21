# backend/tree — 元数据管理数据目录

「元数据管理」页面（装备树管理 / 测试台BOM管理）的全部数据落在这个文件夹，是**预设测试台类型与测试台 BOM 的唯一数据源**。

```
backend/tree/
├── tree.json                    装备树 + 测试台类型属性（产品 / 子系统 / 类型，不含 BOM）
├── bom/<类型编号>.json           每个测试台类型一张 BOM 清单（设备元信息 + 默认连接参数）
├── preset_types/<类型编号>.json   类型编号变更时的迁移中转文件（正常情况为空）
└── README.md                    本文件
```

## 首次使用（把既有预设纳入管理）

后端启动时若 `tree.json` 不存在，会自动从只读预设文件
`backend/testresource/testbench_presets.json` 导入产品 / 子系统 / 测试台类型属性 / BOM，
导入只发生一次，之后以本目录为准。

需要重新导入时，在「装备树管理」页点：

- `从预设文件补齐`：只补缺失的产品 / 子系统 / 类型与 BOM 文件（不动已改过的节点，`POST /api/metadata/reseed?mode=merge`）
- `全量重建`：以预设文件为准覆盖本目录（手工新增的节点会丢失，`POST /api/metadata/reseed?mode=reset`）

## tree.json 结构

```jsonc
{
  "version": 1,
  "created_at": "2026-09-21 23:15:39",
  "updated_at": "2026-09-21 23:16:02",
  "seed_source": "testresource\\testbench_presets.json",
  "products":   [ { "id": "1000-A", "name": "1000-A 机载电子系统", "note": "…" } ],
  "subsystems": [ { "id": "SPM", "name": "SPM 电源模块", "products": ["1000-A", "2000-B"], "note": "" } ],
  "preset_types": [
    {
      "id": "PB-PWR-01",              // 测试台类型编号（BOM 文件名 = 编号 + .json）
      "name": "电源模块标准测试台",
      "category": "电源类",
      "description": "面向 DC-DC / LDO 电源模块的通用电性能测试台…",
      "typical_dut": "DC-DC 模块、LDO 板、PD 适配器板",
      "recommended_cycle": "38 s / 件",
      "product": "1000-A",            // 归属产品（第一级）
      "subsystem": "SPM",             // 归属子系统（第二级）
      "bom_count": 8,
      "updated_at": "2026-09-21 23:16:02"
    }
  ]
}
```

产品 / 子系统的显示名只在 `products` / `subsystems` 里维护一次，类型节点只存归属编号，
避免两处改名不一致。

## bom/<类型编号>.json 结构

```jsonc
{
  "version": 1,
  "preset_id": "PB-PWR-01",
  "updated_at": "2026-09-21 23:16:02",
  "count": 8,
  "items": [
    {
      "id": "tek-mso54",              // 设备编号（同一张 BOM 内唯一）
      "name": "数字示波器",
      "model": "Tektronix MSO54",
      "vendor": "Tektronix",
      "category": "示波器",
      "role": "上电时序 / 纹波测量",
      "programmable": true,
      "interface": "LAN",             // LAN/ETHERNET/TCP/IP | SERIAL/RS232/RS485/UART/COM | USB/GPIB/NONE
      "protocol": "VXI-11 (SCPI)",
      "default_host": "192.168.10.21",   // 网络设备默认 IP
      "default_port": 4000,              // 网络设备默认端口
      "default_serial_port": "",         // 串口设备默认串口号（如 COM6）
      "default_baudrate": null,          // 串口设备默认波特率
      "default_address": "TCPIP0::192.168.10.21::inst0::INSTR",  // 资源地址（USB/GPIB 必填）
      "default_channel": "CH1",          // 通道（可选）
      "required": true,                  // 是否必需设备（自检清单会标注）
      "note": "8 通道；建议固定 IP，端口 4000 为 VXI-11 默认端口"
    }
  ]
}
```

`default_*` 是**该类型的标准连接值**：新建测试台时用它预填第二步的设备配置，
现场可按实际改址（改址只影响该台测试台自身记录，不回写 BOM）。

## 写入规则

- 所有改动都走「元数据管理」页面（`/api/metadata/*`），保存用「先写 .tmp 再替换」的原子写，写入中断不会损坏文件。
- 手工编辑本目录的 JSON 也可以：后端每次请求都重新读取文件，无需重启服务（保持 JSON 合法、编号唯一即可）。
- 删除保护：仍被已注册 / 草稿测试台引用的测试台类型不能删除；产品 / 子系统下还有子节点时不能删除。
- 已注册测试台在 `testresource/testbenches.json` 中各自保存一份设备配置副本，改 BOM **不会**自动改写已注册测试台的配置，只影响之后新建的测试台。

## 相关接口

见 `docs/metadata-management.md`（或同名 .html）的接口清单。
