# Runner TPS 导入能力（zip / JSON）

日期：2026-11-22　工程：MockRunner（后端 + 前端）　依赖：MockController `/api/v1/tps/import*`

## 一、能力与来源

Runner 现在可以自行导入 TPS 包（`.zip` 或单文件 `.json`），导入来源分两种：

| 场景 | 行为 | 返回 `source` |
| --- | --- | --- |
| Controller 可达 | 转发给 Controller 导入（全局可见）→ 刷新本地缓存 → 同步运行副本到 `testresource/<id>/` | `controller` |
| Controller 不可达 | 本地解包、校验、落盘（仅本机可见） | `local` |
| Controller 明确拒绝（4xx） | 原样返回 Controller 的错误，**不在本地落盘**，避免两套校验规则打架 | `controller` + `success=false` |

## 二、接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/tps/import/zip` | `{name, content_base64, overwrite}`，zip 内含 `tps.json` + `testcase/` |
| POST | `/api/tps/import` | `{name, content}`，单文件 JSON 清单 |
| GET | `/api/tps/import/capability` | `{online, mode, controller_url, limits}`，前端据此提示导入去向 |

统一返回：

```json
{
  "success": true,
  "source": "controller",
  "tps": {"id": "xxx", "name": "...", "schema": "tps.v2", "preset_id": "..."},
  "message": "已导入 ...",
  "files_synced": 2,
  "warnings": []
}
```

`limits`：`max_files=4000`、`max_bytes=200 MB`（后端），前端另设 64 MB 上传拦截以免浏览器卡死。

## 三、前端交互

执行页「导入 TPS」按钮接受 `.json` 与 `.zip`：

1. 先查 `capability`：
   - 在线 → 提示「将导入到 MockController（全局可见）」；
   - 离线 → 警示色提示「Controller 不可达，将导入到本机（仅本机可见）」；
   - 接口 404 时忽略提示，不阻断导入。
2. `.json` 读文本、`.zip` 转 base64，分别调用对应接口；
3. 成功 → toast 显示后端 message，刷新 TPS 列表并选中新导入项；
4. 失败 → 原样显示后端 message（含 Controller 的校验 detail）；
5. `source=local` 时额外提示「仅本机可见，联网后建议在 Controller 重新导入以共享」。

## 四、实测记录

| 用例 | 结果 |
| --- | --- |
| capability（Controller 在线） | `online=True, mode=controller` |
| zip 在线导入 | `success=True, source=controller, files_synced=2`，列表可见 |
| JSON 在线导入 | `success=True, source=controller`，列表可见 |
| 坏 zip（无 `tps.json`） | `success=False`，`压缩包内缺少 tps.json` |
| Controller 拒绝（缺 `testconfig`） | `success=False, source=controller`，本地目录未创建 |
| capability（停机后） | `online=False, mode=local` |
| zip 离线导入 | `success=True, source=local`，`testresource/<id>/tps.json` 实际存在，列表可见 |
| pytest | `tests/test_tps_import.py` **6 passed**；与冒烟用例合计 **13 passed** |
| 前端构建 | 通过，`index.js 273.70 kB` / `index.css 114.84 kB` |

## 五、已知限制（明确未覆盖）

1. **前端未做浏览器实测**：导入弹层、capability 提示、成功后选中新 TPS 这些交互只做了构建与代码检视，没有在浏览器里点过一次（本机浏览器面板视口为 0，无法截图回归）。
2. **大文件路径未测**：>64 MB 的前端拦截、>200 MB 的后端拒绝都未构造真实样本验证。
3. **并发/重复导入未测**：同一 id 连续导入两次（`overwrite=true`）的覆盖行为、并发导入的竞态未验证。
4. **离线导入不会自动同步**：Controller 恢复后，本地导入的 TPS 不会自动上传；需到 Controller 控制台重新导入才能全局可见（界面已提示，但没有一键上传）。
5. **两条校验路径不完全等价**：离线用 Runner 的 `tps_runtime` 校验，在线用 Controller 校验（额外校验 `parser/process/preset` 引用），因此同一个包在线被拒、离线可能通过。
6. **不支持的包形态**：zip 内必须是 `tps.json`（允许一层根目录），不支持多 TPS 打包、不支持 `.tar/.7z`。
