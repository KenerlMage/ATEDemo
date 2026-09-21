async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export const api = {
  login: (username) => request('/api/login', { method: 'POST', body: JSON.stringify({ username }) }),
  // ---- License ----
  getLicenseStatus: () => request('/api/license/status'),
  importLicense: (content) => request('/api/license/import', { method: 'POST', body: JSON.stringify({ content }) }),
  getTestCases: () => request('/api/testcases'),
  execute: (testcaseId) => request('/api/execute', { method: 'POST', body: JSON.stringify({ testcase_id: testcaseId }) }),
  getTask: (taskId) => request(`/api/tasks/${taskId}`),
  // ---- TPS ----
  getTpsList: () => request('/api/tps'),
  getTpsDetail: (tpsId) => request(`/api/tps/${tpsId}`),
  getTpsByUut: (uut) => request(`/api/tps/by-uut?uut=${encodeURIComponent(uut)}`),
  importTps: (content, name) => request('/api/tps/import', { method: 'POST', body: JSON.stringify({ content, name }) }),
  aiAnalyze: (apiUrl, apiKey, taskId) => request('/api/ai/analyze', { method: 'POST', body: JSON.stringify({ api_url: apiUrl, api_key: apiKey, task_id: taskId }) }),
  runTps: (tpsId, uut) => request(`/api/tps/${tpsId}/run`, { method: 'POST', body: JSON.stringify({ uut }) }),
  getTpsTask: (taskId) => request(`/api/tps-tasks/${taskId}`),
  // ---- 装备树 + 记录库 ----
  getTree: () => request('/api/tree'),
  // 历史测试记录: 支持 uut / 日期 / 项目 / 批次 组合过滤
  getRecords: (filters = {}) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v) q.set(k, v)
    }
    const s = q.toString()
    return request(s ? `/api/records?${s}` : '/api/records')
  },
  // ---- 测试装备清单 ----
  getEquipment: () => request('/api/equipment'),
  // ---- UUT 属性 (SN -> 批次等, 远端获取/本地预置) ----
  getUutProfile: (sn) => request(`/api/uut/${encodeURIComponent(sn)}`),
  // ---- 测试台注册 / 测试台导航 ----
  // 预设测试台类型 (含标准 BOM 清单)
  getTestbenchPresets: () => request('/api/testbench/presets'),
  // 预设类型级联树: 产品 -> 子系统 -> 预设测试台类型
  getTestbenchTree: () => request('/api/testbench/tree'),
  getTestbenchPreset: (presetId) => request(`/api/testbench/presets/${encodeURIComponent(presetId)}`),
  // 导航页指标看板
  getTestbenchOverview: () => request('/api/testbench/overview'),
  // 已注册测试台 (标签列表 / 详情)
  getTestbenches: () => request('/api/testbenches'),
  getTestbench: (tbId) => request(`/api/testbenches/${encodeURIComponent(tbId)}`),
  // 三步注册: 保存草稿 / 正式注册 (传 id 为更新)
  saveTestbench: (payload) => request('/api/testbenches', { method: 'POST', body: JSON.stringify(payload) }),
  deleteTestbench: (tbId) => request(`/api/testbenches/${encodeURIComponent(tbId)}`, { method: 'DELETE' }),
  // 第三步: 注册设备连通性自检 (mode: real 真实探测 / simulate 离线模拟)
  verifyTestbench: (tbId, mode = 'real', timeout = 2) =>
    request(`/api/testbenches/${encodeURIComponent(tbId)}/verify`, {
      method: 'POST',
      body: JSON.stringify({ mode, timeout })
    }),
  // 重建默认测试台 (删除后恢复出厂演示数据; 已存在时不重复创建)
  restoreDefaultTestbenches: () =>
    request('/api/testbenches/restore-defaults', { method: 'POST', body: JSON.stringify({}) }),

  // ---- 装备属性配置 (设备来自测试台注册时的 BOM 清单) ----
  // 拍平所有测试台的设备 (bench_id / 设备属性 / 所属测试台上下文)
  getEquipmentDevices: (benchId = '') =>
    request(benchId ? `/api/testbench/devices?bench_id=${encodeURIComponent(benchId)}` : '/api/testbench/devices'),
  // 修改可编程设备的连接属性 (右键 → 修改设备属性); patch: {host,port,serial_port,baudrate,address,channel,protocol,note} 或 {reset_defaults:true}
  updateDeviceConfig: (benchId, deviceId, patch) =>
    request(`/api/testbenches/${encodeURIComponent(benchId)}/devices/${encodeURIComponent(deviceId)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch || {})
    }),

  // ---- 装备助手 (仪器控制小工具) ----
  getTools: () => request('/api/tools'),
  // 可绑定的示波器设备 (来自测试台注册 BOM)
  getOscilloscopeBindings: (scopeOnly = false) =>
    request(`/api/tools/oscilloscope/bindings${scopeOnly ? '?scope_only=true' : ''}`),
  getOscilloscopeState: (benchId, deviceId) =>
    request(`/api/tools/oscilloscope/state?bench_id=${encodeURIComponent(benchId)}&device_id=${encodeURIComponent(deviceId)}`),
  // 建立会话: mode=real(真实仪器) / simulate(离线模拟); allow_fallback 连不上时自动降级
  connectOscilloscope: (payload) =>
    request('/api/tools/oscilloscope/connect', { method: 'POST', body: JSON.stringify(payload || {}) }),
  // 示波器操作: identify/run/stop/single/autoset/timebase/channel/acquire/measure/frame/close
  oscilloscopeAction: (payload) =>
    request('/api/tools/oscilloscope/action', { method: 'POST', body: JSON.stringify(payload || {}) }),

  // ---- 测试台装备运维 (待注册导出 / 初始化 / 终止 / 自检 / 报告区) ----
  // 待注册(草稿)测试台清单
  getPendingTestbenches: () => request('/api/testbench/pending'),
  // 导出待注册测试台: {} = 全部待注册; {bench_ids:[...]} 指定台; {include_registered:true} 连带已注册
  exportTestbenches: (payload = {}) =>
    request('/api/testbenches/export', { method: 'POST', body: JSON.stringify(payload) }),
  // 导出已注册测试台: 传 [] = 全部已注册; 传 ids = 只导选中的那几台（带 status=registered 明确范围）
  exportRegisteredTestbenches: (benchIds = []) =>
    request('/api/testbenches/export', {
      method: 'POST',
      body: JSON.stringify(benchIds && benchIds.length
        ? { bench_ids: benchIds, status: 'registered' }
        : { status: 'registered' })
    }),
  // 装备初始化 (复位 / 远程 / 安全态)
  initTestbench: (tbId, payload = {}) =>
    request(`/api/testbenches/${encodeURIComponent(tbId)}/init`, { method: 'POST', body: JSON.stringify(payload) }),
  // 装备终止 (输出关闭 / 转速归零 / 退回本地)
  teardownTestbench: (tbId, payload = {}) =>
    request(`/api/testbenches/${encodeURIComponent(tbId)}/teardown`, { method: 'POST', body: JSON.stringify(payload) }),
  // 硬件自检 (结果写回注册库 + 落盘报告)
  selfcheckTestbench: (tbId, payload = {}) =>
    request(`/api/testbenches/${encodeURIComponent(tbId)}/selfcheck`, { method: 'POST', body: JSON.stringify(payload) }),
  // 历史报告清单 (filters: {kind, bench_id, limit})
  getTestbenchReports: (filters = {}) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v && v !== 'all') q.set(k, v)
    }
    const s = q.toString()
    return request(s ? `/api/testbench/reports?${s}` : '/api/testbench/reports')
  },
  // 报告目录信息 (路径 / 文件数 / 占用空间)
  getTestbenchReportDir: () => request('/api/testbench/reports/dir'),
  // 调系统资源管理器: {} = 打开报告目录; {filename} = 定位到该报告
  openTestbenchReports: (payload = {}) =>
    request('/api/testbench/reports/open', { method: 'POST', body: JSON.stringify(payload) }),
  // ---- TPS 运行环境 (workspace + 公共 conftest + 设备 driver 配置) ----
  // workspace 信息：路径 / 公共 conftest 版本 / 运行目录总览
  getRuntimeWorkspace: () => request('/api/runtime/workspace'),
  // 初始化 workspace（不存在则创建）+ 部署/更新公共 conftest
  initRuntimeWorkspace: (force = false) =>
    request('/api/runtime/workspace/init', { method: 'POST', body: JSON.stringify({ force }) }),
  // 驱动规格清单（工厂可解析的驱动类型与可用动作）
  getRuntimeDrivers: () => request('/api/runtime/drivers'),
  // SQLite 里的 driver 配置投影（测试台 / 设备数量）
  getRuntimeDb: () => request('/api/runtime/db'),
  // 注册库 -> SQLite 手动重新同步
  syncRuntimeDb: () => request('/api/runtime/db/sync', { method: 'POST', body: JSON.stringify({}) }),
  // TPS v2 清单：testconfig / device_config / cmd_suit 三字段
  getTpsManifest: (tpsId) => request(`/api/tps/${encodeURIComponent(tpsId)}/manifest`),
  // 准备运行环境（把 TPS 复制成临时副本到 workspace）
  stageTps: (tpsId, payload = {}) =>
    request(`/api/tps/${encodeURIComponent(tpsId)}/stage`, { method: 'POST', body: JSON.stringify(payload) }),
  // 用例收集（pytest --collect-only，不产生运行记录）
  collectTpsCases: (tpsId, payload = {}) =>
    request(`/api/tps/${encodeURIComponent(tpsId)}/collect`, { method: 'POST', body: JSON.stringify(payload) }),
  // 历史运行目录（临时副本）
  getTpsRuns: (tpsId) => request(`/api/tps/${encodeURIComponent(tpsId)}/runs`),
  // 清理旧运行目录（当前运行目录始终保护）
  cleanupTpsRuns: (tpsId, keep = 5) =>
    request(`/api/tps/${encodeURIComponent(tpsId)}/runs/cleanup`, { method: 'POST', body: JSON.stringify({ keep }) }),
  // TPS v2 清单校验
  validateTps: (manifest) => request('/api/tps/validate', { method: 'POST', body: JSON.stringify({ manifest }) }),
  // 报告原始地址 (界面内 iframe / 新窗口查看)
  testbenchReportUrl: (file) => `/api/testbench/reports/${encodeURIComponent(file || '')}`
}
