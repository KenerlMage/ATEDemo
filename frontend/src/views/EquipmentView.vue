<script setup>
/**
 * 装备属性配置页
 *  - 设备清单来自「测试台注册」时该类型 BOM 清单中的设备 (按测试台分组)
 *  - 可编程设备: 右键 → 修改设备属性 (IP / 端口 / 串口 / 波特率 / 资源地址 / 通道)
 *  - 非可编程设备: 属性只读
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

const FT = { LAN: '网口', SERIAL: '串口', USB: 'USB', GPIB: 'GPIB', NONE: '无接口', MANUAL: '人工' }
const NET = ['LAN', 'ETHERNET', 'TCP', 'IP']
const SER = ['SERIAL', 'RS232', 'RS485', 'UART', 'COM']
const PASSIVE = ['USB', 'GPIB']
const BAUD_PRESETS = [4800, 9600, 19200, 38400, 57600, 115200, 230400]

const loading = ref(true)
const error = ref('')
const benches = ref([])
const devices = ref([])
const filter = ref('all')          // all | programmable | unconfigured
const activeBench = ref('all')     // all | bench id
const keyword = ref('')

// 视图分栏：三块独立控件（属性配置 / 装备自检 / 报告中心）
const TABS = [
  { id: 'equipment', icon: '⚙', label: '装备属性配置', desc: '设备清单来自各测试台注册时该类型 BOM 清单；右键可编程设备即可修改 IP / 端口 / 串口等连接属性，并在此导出待注册测试台。' },
  { id: 'selfcheck', icon: '✓', label: '测试台装备自检', desc: '对所选测试台执行装备初始化 / 终止 / 硬件自检；执行完直接弹出完整报告，支持离线模拟与真实探测两种模式。' },
  { id: 'reports', icon: '▤', label: '装备报告中心', desc: '报告落盘目录、占用空间与历史报告清单；可直接打开本地资源管理器定位到具体文件。' }
]

/** 从 hash 查询串读回当前分栏（刷新后仍在同一栏） */
function readTab() {
  try {
    const m = (window.location.hash || '').match(/[?&]tab=([\w-]+)/)
    return m && TABS.some((t) => t.id === m[1]) ? m[1] : 'equipment'
  } catch (e) { return 'equipment' }
}

const activeTab = ref(readTab())
const tabMeta = computed(() => TABS.find((t) => t.id === activeTab.value) || TABS[0])

/** 切换分栏：同步地址栏、收起浮动菜单、顺手刷新该栏数据 */
function setTab(id) {
  activeTab.value = TABS.some((t) => t.id === id) ? id : 'equipment'
  closeMenu()
  try {
    const hash = (window.location.hash || '#/equipment').replace(/[?&]tab=[\w-]+/, '')
    const next = activeTab.value === 'equipment'
      ? hash
      : `${hash}${hash.includes('?') ? '&' : '?'}tab=${activeTab.value}`
    window.history.replaceState(null, '', next)
  } catch (e) { /* 地址栏回填失败不影响切换 */ }
  if (activeTab.value === 'reports') loadReports()
  if (activeTab.value === 'selfcheck') loadPending()
}

// 右键菜单
const menu = ref({ open: false, x: 0, y: 0, device: null })

// 编辑弹窗
const dialog = ref({ open: false, device: null, form: {}, saving: false, errors: [] })
// 历史弹窗
const history = ref({ open: false, device: null })
const toast = ref({ text: '', kind: 'ok', timer: null })

function flash(text, kind = 'ok') {
  toast.value = { text, kind, timer: Date.now() }
  setTimeout(() => { if (Date.now() - toast.value.timer >= 2600) toast.value = { ...toast.value, text: '' } }, 2700)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.getEquipmentDevices()
    if (!res || res.success === false) {
      error.value = (res && res.message) || '装备属性加载失败'
      return
    }
    benches.value = res.benches || []
    devices.value = res.devices || []
    // 已注册测试台可能被删除/重新注册，清理失效的勾选
    { const ids = benches.value.map((b) => b.id)
      regSel.value = regSel.value.filter((id) => ids.includes(id)) }
    if (!ops.value.benchId || !benches.value.some((b) => b.id === ops.value.benchId)) {
      ops.value = { ...ops.value, benchId: benches.value.length ? benches.value[0].id : '' }
    }
  } catch (e) {
    error.value = `无法连接后端: ${e.message}`
  } finally {
    loading.value = false
  }
}

const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return devices.value.filter((d) => {
    if (activeBench.value !== 'all' && d.bench_id !== activeBench.value) return false
    if (filter.value === 'programmable' && !d.programmable) return false
    if (filter.value === 'unconfigured' && d.configured) return false
    if (!kw) return true
    return [d.name, d.model, d.vendor, d.category, d.host, d.serial_port, d.address, d.role]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(kw))
  })
})

const stats = computed(() => ({
  bench: benches.value.length,
  device: devices.value.length,
  prog: devices.value.filter((d) => d.programmable).length,
  conf: devices.value.filter((d) => d.configured).length,
  nok: devices.value.filter((d) => d.programmable && !d.configured).length
}))

const OPS_KINDS = [
  { id: 'all', label: '全部' },
  { id: 'selfcheck', label: '硬件自检' },
  { id: 'init', label: '装备初始化' },
  { id: 'teardown', label: '装备终止' },
  { id: 'export', label: '测试台导出' }
]

// 测试台装备运维状态
const ops = ref({ benchId: '', mode: 'simulate', busy: '', last: null })
const pending = ref({ count: 0, registered: 0, list: [] })
const reports = ref({ list: [], dir: '', fileCount: 0, sizeBytes: 0, kind: 'all' })
const exportDlg = ref({ open: false, row: null })
const reportView = ref({ open: false, title: '', url: '', meta: null })

const opsBench = computed(() => benches.value.find((b) => b.id === ops.value.benchId) || null)

/* ---------------- 已注册测试台导出 ---------------- */
const regSel = ref([])
const registeredBenches = computed(() => benches.value.filter((b) => b.status === 'registered'))

function toggleRegSel(id) {
  regSel.value = regSel.value.includes(id) ? regSel.value.filter((x) => x !== id) : [...regSel.value, id]
}
function toggleAllReg() {
  regSel.value = regSel.value.length >= registeredBenches.value.length
    ? []
    : registeredBenches.value.map((b) => b.id)
}
/** 导出已注册测试台：onlySelected=true 只导勾选的，否则导全部已注册 */
async function exportRegistered(onlySelected) {
  const ids = onlySelected ? [...regSel.value] : []
  if (onlySelected && !ids.length) { flash('请先勾选要导出的测试台', 'warn'); return }
  ops.value = { ...ops.value, busy: 'exportReg' }
  try {
    const res = await api.exportRegisteredTestbenches(ids)
    if (!res.success) throw new Error(res.message || '导出失败')
    exportDlg.value = { open: true, row: res }
    downloadJson(res.content, res.filename)
    flash(res.message, res.count ? 'ok' : 'warn')
    await loadReports()
  } catch (e) {
    flash(`导出失败: ${e.message}`, 'err')
  } finally {
    ops.value = { ...ops.value, busy: '' }
  }
}

function ifaceText(v) { return FT[String(v || '').toUpperCase()] || v || '—' }
function isNet(d) { return NET.includes(String(d.interface || '').toUpperCase()) }
function isSer(d) { return SER.includes(String(d.interface || '').toUpperCase()) }
function isPassive(d) { return PASSIVE.includes(String(d.interface || '').toUpperCase()) }

/** 设备当前连接描述: 网口 IP:端口 / 串口 COM@波特率 / 资源地址 */
function resourceText(d) {
  if (isNet(d)) return d.host && d.port ? `${d.host}:${d.port}` : (d.host || '未配置')
  if (isSer(d)) return d.serial_port ? `${d.serial_port} @ ${d.baudrate || '—'}` : '未配置'
  return d.address || '未配置'
}
function benchOf(d) {
  return benches.value.find((b) => b.id === d.bench_id) || {}
}

/* ---------------- 右键菜单 ---------------- */
function onContextMenu(evt, dev) {
  evt.preventDefault()
  const pad = 8
  const w = 214
  const h = dev.programmable ? 186 : 92
  menu.value = {
    open: true,
    x: Math.min(evt.clientX, window.innerWidth - w - pad),
    y: Math.min(evt.clientY, window.innerHeight - h - pad),
    device: dev
  }
}
function closeMenu() { if (menu.value.open) menu.value = { ...menu.value, open: false } }
function onDocClick() { closeMenu() }
function onEsc(e) {
  if (e.key !== 'Escape') return
  closeMenu()
  dialog.value = { ...dialog.value, open: false }
  history.value = { ...history.value, open: false }
  exportDlg.value = { ...exportDlg.value, open: false }
  reportView.value = { ...reportView.value, open: false }
}

/* ---------------- 编辑弹窗 ---------------- */
function openEditor(dev) {
  closeMenu()
  if (!dev) return
  if (!dev.programmable) {
    flash(`「${dev.name}」不是可编程设备，属性只读`, 'warn')
    return
  }
  dialog.value = {
    open: true,
    device: dev,
    saving: false,
    errors: [],
    form: {
      host: dev.host || '',
      port: dev.port ?? null,
      protocol: dev.protocol || '',
      serial_port: dev.serial_port || '',
      baudrate: dev.baudrate ?? null,
      address: dev.address || '',
      channel: dev.channel || '',
      note: dev.note || ''
    }
  }
}

function validate(form, dev) {
  const errs = []
  if (isNet(dev)) {
    if (!String(form.host || '').trim()) errs.push('网口设备的 IP / 主机名不能为空')
    else if (!/^((\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9][A-Za-z0-9._-]*)$/.test(String(form.host).trim())) errs.push('IP / 主机名格式非法')
    const p = Number(form.port)
    if (!form.port && form.port !== 0) errs.push('网口设备的端口不能为空')
    else if (!Number.isInteger(p) || p < 1 || p > 65535) errs.push('端口需为 1-65535 的整数')
  }
  if (isSer(dev)) {
    const sp = String(form.serial_port || '').trim()
    if (!sp) errs.push('串口设备的串口号不能为空')
    else if (!/^(COM\d{1,3}|\/dev\/tty[A-Za-z0-9._-]+)$/.test(sp)) errs.push('串口号格式非法（形如 COM3）')
    const br = Number(form.baudrate)
    if (form.baudrate && (!Number.isInteger(br) || br < 300 || br > 1000000)) errs.push('波特率需在 300-1000000 之间')
  }
  if (isPassive(dev) && dev.programmable && !String(form.address || '').trim()) errs.push(`${String(dev.interface).toUpperCase()} 设备需填写资源地址`)
  if (form.address && !/^[A-Za-z0-9:;._/\- ]{1,80}$/.test(String(form.address))) errs.push('资源地址含非法字符')
  if (form.channel && !/^[A-Za-z0-9._-]{0,16}$/.test(String(form.channel))) errs.push('通道号含非法字符')
  return errs
}

function payload(dev, form) {
  const p = { note: form.note || '' }
  if (isNet(dev)) {
    p.host = String(form.host || '').trim()
    p.port = form.port === '' || form.port === null ? null : Number(form.port)
    p.protocol = form.protocol || ''
  } else if (isSer(dev)) {
    p.serial_port = String(form.serial_port || '').trim()
    p.baudrate = form.baudrate === '' || form.baudrate === null ? null : Number(form.baudrate)
    p.protocol = form.protocol || ''
  } else {
    p.address = String(form.address || '').trim()
    p.channel = String(form.channel || '').trim()
    p.protocol = form.protocol || ''
  }
  return p
}

async function save(extra = {}) {
  const dev = dialog.value.device
  if (!dev) return
  const form = dialog.value.form
  if (!extra.reset_defaults) {
    const errs = validate(form, dev)
    if (errs.length) { dialog.value = { ...dialog.value, errors: errs }; return }
  }
  dialog.value = { ...dialog.value, saving: true, errors: [] }
  try {
    const res = await api.updateDeviceConfig(dev.bench_id, dev.id, { ...payload(dev, form), ...extra })
    if (!res || res.success === false) {
      dialog.value = { ...dialog.value, saving: false, errors: (res && res.errors) || [res && res.message || '保存失败'] }
      return
    }
    flash(res.message || '设备属性已更新')
    dialog.value = { ...dialog.value, open: false, saving: false }
    await load()
  } catch (e) {
    dialog.value = { ...dialog.value, saving: false, errors: [`保存失败: ${e.message}`] }
  }
}

async function resetDefaults() {
  const dev = dialog.value.device
  if (!dev) return
  if (!window.confirm(`将「${dev.name}」的连接属性恢复为该测试台类型的 BOM 默认值？`)) return
  await save({ reset_defaults: true })
}

/** 右键菜单里的恢复默认值（不经过表单校验） */
async function resetDefaultsFor(dev) {
  closeMenu()
  if (!dev) return
  if (!window.confirm(`将「${dev.name}」的连接属性恢复为该测试台类型的 BOM 默认值？`)) return
  try {
    const res = await api.updateDeviceConfig(dev.bench_id, dev.id, { reset_defaults: true })
    if (!res || res.success === false) {
      flash((res && res.message) || '恢复默认值失败', 'warn')
      return
    }
    flash(res.message || '已恢复 BOM 默认值')
    await load()
  } catch (e) {
    flash(`恢复默认值失败: ${e.message}`, 'warn')
  }
}

function openHistory(dev) {
  closeMenu()
  history.value = { open: true, device: dev, rows: dev.config_history || [] }
}

async function copyResource(dev) {
  closeMenu()
  const text = dev.resource || resourceText(dev)
  try {
    await navigator.clipboard.writeText(text)
    flash(`已复制资源地址: ${text}`)
  } catch {
    flash(`资源地址: ${text}`, 'warn')
  }
}

function entryText(changes) {
  return Object.entries(changes || {})
    .map(([k, v]) => `${FIELD_LABEL[k] || k}: ${v.from ?? '—'} → ${v.to ?? '—'}`)
    .join('；')
}
const FIELD_LABEL = {
  host: 'IP', port: '端口', protocol: '协议', serial_port: '串口',
  baudrate: '波特率', address: '资源地址', channel: '通道', note: '备注'
}

/* ---------------- 测试台装备运维 (初始化 / 终止 / 自检 / 报告区) ---------------- */
function sizeText(n) {
  const v = Number(n || 0)
  if (v < 1024) return `${v} B`
  if (v < 1048576) return `${(v / 1024).toFixed(1)} KB`
  return `${(v / 1048576).toFixed(2)} MB`
}
function statusText(s) { return { pass: '成功', fail: '失败', skip: '跳过', info: '无需程控' }[s] || s || '—' }
function statusTone(s) { return { pass: 'ok', fail: 'err', skip: 'warn', info: 'muted' }[s] || 'muted' }
function overallText(v) { return { pass: '通过', warn: '待人工确认', fail: '未通过' }[v] || '未自检' }

async function loadPending() {
  try {
    const res = await api.getPendingTestbenches()
    if (res && res.success !== false) {
      pending.value = { count: res.count || 0, registered: res.registered_count || 0, list: res.testbenches || [] }
    }
  } catch (e) { /* 面板不阻塞主流程，静默 */ }
}

async function loadReports() {
  try {
    const [list, dir] = await Promise.all([
      api.getTestbenchReports({ kind: reports.value.kind }),
      api.getTestbenchReportDir()
    ])
    if (list && list.success !== false) reports.value = { ...reports.value, list: list.reports || [] }
    if (dir && dir.success !== false) {
      reports.value = {
        ...reports.value,
        dir: dir.path || '',
        fileCount: dir.file_count || 0,
        sizeBytes: dir.size_bytes || 0
      }
    }
  } catch (e) { /* 静默 */ }
}

async function setReportKind(kind) {
  reports.value = { ...reports.value, kind }
  await loadReports()
}

function downloadJson(content, filename) {
  const blob = new Blob([JSON.stringify(content, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || 'testbenches_export.json'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1500)
}

/** 导出待注册测试台: 落盘到报告目录 + 浏览器下载 */
async function doExport() {
  ops.value = { ...ops.value, busy: 'export' }
  try {
    const res = await api.exportTestbenches({})
    if (!res.success) throw new Error(res.message || '导出失败')
    exportDlg.value = { open: true, row: res }
    downloadJson(res.content, res.filename)
    flash(res.message, res.count ? 'ok' : 'warn')
    await Promise.all([loadPending(), loadReports()])
  } catch (e) {
    flash(`导出失败: ${e.message}`, 'err')
  } finally {
    ops.value = { ...ops.value, busy: '' }
  }
}

function reDownload() {
  const row = exportDlg.value.row
  if (row) downloadJson(row.content, row.filename)
}

/** 装备初始化 / 终止 / 自检: 执行后直接弹出报告 */
async function runOps(phase) {
  const bench = opsBench.value
  if (!bench) { flash('请先选择目标测试台', 'warn'); return }
  const mode = ops.value.mode
  const label = { init: '装备初始化', teardown: '装备终止', selfcheck: '装备自检' }[phase]
  if (mode === 'real' && !window.confirm(`将对「${bench.title}」执行真实${label}：会真正建立连接并下发指令，确认继续？`)) return
  ops.value = { ...ops.value, busy: phase }
  try {
    const fn = phase === 'init' ? api.initTestbench : phase === 'teardown' ? api.teardownTestbench : api.selfcheckTestbench
    const res = await fn(bench.id, { mode })
    if (!res.success) throw new Error(res.message || `${label}失败`)
    const report = res.report || {}
    reportView.value = {
      open: true,
      title: `${label} · ${bench.title}`,
      url: report.url || api.testbenchReportUrl(report.file),
      meta: { ...report, result: res.result, bench_title: bench.title }
    }
    ops.value = { ...ops.value, last: res.result }
    flash(res.message, res.result && res.result.overall === 'fail' ? 'err' : 'ok')
    await Promise.all([load(), loadReports()])
  } catch (e) {
    flash(`${label}失败: ${e.message}`, 'err')
  } finally {
    ops.value = { ...ops.value, busy: '' }
  }
}

function viewReport(row) {
  reportView.value = {
    open: true,
    title: `${row.kind_label || '报告'} · ${row.bench_title || row.bench_serial || ''}`,
    url: row.url || api.testbenchReportUrl(row.file),
    meta: { ...row }
  }
}

/** 打开系统资源管理器: 无参 = 报告目录, 传 row = 定位到该文件 */
async function openInExplorer(row) {
  try {
    const res = await api.openTestbenchReports(row && row.file ? { filename: row.file } : {})
    flash(res.success ? res.message : (res.message || '打开失败'), res.success ? 'ok' : 'warn')
  } catch (e) {
    flash(`无法调起资源管理器: ${e.message}`, 'err')
  }
}

/* ---------------- TPS 运行环境 (workspace / 公共 conftest / driver 配置) ---------------- */
const rt = ref({
  workspace: '', conftest: '', version: 0, templateVersion: 0,
  dbPath: '', benchCount: 0, deviceCount: 0, syncedAt: '',
  drivers: [], tpsList: [], tpsId: '', runs: [], lastResult: null, busy: ''
})

const rtTps = computed(() => rt.value.tpsList.find((t) => t.id === rt.value.tpsId) || null)
function modeText(m) { return m === 'real' ? '真机' : '仿真' }
function runResultText(r) {
  const s = (r && r.summary) || null
  return s ? `${s.passed}/${s.total} 通过` : '未执行'
}
function runTone(r) {
  const s = (r && r.summary) || null
  if (!s) return 'muted'
  return s.failed ? 'err' : 'ok'
}

/** 运行环境 + driver 配置 + 可运行 TPS 包 */
async function loadRuntime() {
  try {
    const [ws, db, drv, list] = await Promise.all([
      api.getRuntimeWorkspace(),
      api.getRuntimeDb(),
      api.getRuntimeDrivers(),
      api.getTpsList()
    ])
    const tpsList = (list.tps || []).filter((t) => t.schema === 'tps.v2' || (t.cmd_suit_count || 0) > 0)
    const keepId = tpsList.some((t) => t.id === rt.value.tpsId) ? rt.value.tpsId : ((tpsList[0] || {}).id || '')
    rt.value = {
      ...rt.value,
      workspace: ws.workspace || '',
      conftest: ws.conftest || '',
      version: ws.conftest_version || 0,
      templateVersion: ws.template_version || 0,
      dbPath: db.db_path || '',
      benchCount: db.bench_count || 0,
      deviceCount: db.device_count || 0,
      syncedAt: db.synced_at || '',
      drivers: drv.specs || [],
      tpsList,
      tpsId: keepId
    }
    if (keepId) await loadRuns()
  } catch (e) { /* 面板不阻塞主流程，静默 */ }
}

/** 当前 TPS 的 workspace 运行目录（临时副本） */
async function loadRuns() {
  if (!rt.value.tpsId) { rt.value = { ...rt.value, runs: [], lastResult: null }; return }
  try {
    const res = await api.getTpsRuns(rt.value.tpsId)
    if (res && res.success !== false) {
      rt.value = { ...rt.value, runs: res.runs || [], lastResult: res.last_result || null }
    }
  } catch (e) { /* 静默 */ }
}

async function rtInit() {
  rt.value = { ...rt.value, busy: 'init' }
  try {
    const res = await api.initRuntimeWorkspace(true)
    flash(res.message || '运行环境已就绪', res.success === false ? 'warn' : 'ok')
    await loadRuntime()
  } catch (e) {
    flash(`初始化失败: ${e.message}`, 'err')
  } finally {
    rt.value = { ...rt.value, busy: '' }
  }
}

async function rtSync() {
  rt.value = { ...rt.value, busy: 'sync' }
  try {
    const res = await api.syncRuntimeDb()
    flash(res.message || '驱动配置已重新投影到 SQLite', res.success === false ? 'warn' : 'ok')
    await loadRuntime()
  } catch (e) {
    flash(`同步失败: ${e.message}`, 'err')
  } finally {
    rt.value = { ...rt.value, busy: '' }
  }
}

/** 清理旧运行目录（当前运行目录始终受保护） */
async function rtCleanup() {
  if (!rt.value.tpsId) return
  rt.value = { ...rt.value, busy: 'clean' }
  try {
    const res = await api.cleanupTpsRuns(rt.value.tpsId, 3)
    flash(res.message || '已清理旧运行目录', 'ok')
    await loadRuns()
  } catch (e) {
    flash(`清理失败: ${e.message}`, 'err')
  } finally {
    rt.value = { ...rt.value, busy: '' }
  }
}

onMounted(() => {
  load()
  loadPending()
  loadReports()
  loadRuntime()
  window.addEventListener('click', onDocClick)
  window.addEventListener('keydown', onEsc)
  window.addEventListener('scroll', closeMenu, true)
})
onUnmounted(() => {
  window.removeEventListener('click', onDocClick)
  window.removeEventListener('keydown', onEsc)
  window.removeEventListener('scroll', closeMenu, true)
})
</script>

<template>
  <div class="eq-page">
    <!-- 头部 -->
    <section class="eq-head">
      <div class="hd-left">
        <div class="hd-title">{{ tabMeta.label }}</div>
        <div class="hd-sub">{{ tabMeta.desc }}</div>
      </div>
      <div class="hd-metrics">
        <div class="metric"><b>{{ stats.bench }}</b><span>测试台</span></div>
        <div class="metric"><b>{{ stats.device }}</b><span>设备</span></div>
        <div class="metric"><b>{{ stats.prog }}</b><span>可编程</span></div>
        <div class="metric" :class="{ warn: stats.nok > 0 }"><b>{{ stats.conf }}/{{ stats.prog }}</b><span>已配置</span></div>
      </div>
    </section>

    <!-- 分栏切换：三块独立控件 -->
    <nav class="view-tabs">
      <button
        v-for="t in TABS"
        :key="t.id"
        class="view-tab"
        :class="{ on: activeTab === t.id }"
        @click="setTab(t.id)"
      >
        <span class="vt-ico">{{ t.icon }}</span>
        <span class="vt-label">{{ t.label }}</span>
        <span v-if="t.id === 'equipment' && pending.count" class="vt-badge warn">{{ pending.count }}</span>
        <span v-else-if="t.id === 'reports' && reports.fileCount" class="vt-badge">{{ reports.fileCount }}</span>
      </button>
    </nav>

    <!-- 工具条：测试台切换 + 过滤 + 搜索 -->
    <section class="bar" v-show="activeTab === 'equipment'">
      <div class="bench-tabs">
        <button class="tab" :class="{ on: activeBench === 'all' }" @click="activeBench = 'all'">
          全部测试台 <i>{{ benches.length }}</i>
        </button>
        <button
          v-for="b in benches"
          :key="b.id"
          class="tab"
          :class="{ on: activeBench === b.id }"
          :title="`${b.preset_name} · ${b.line}${b.station} · ${b.status === 'registered' ? '已注册' : '草稿'}`"
          @click="activeBench = b.id"
        >
          {{ b.title }} <i>{{ b.configured_count }}/{{ b.programmable_count }}</i>
          <span class="tab-dot" :class="b.status === 'registered' ? 'ok' : 'draft'"></span>
        </button>
      </div>
      <div class="bar-right">
        <div class="chips">
          <button class="chip" :class="{ on: filter === 'all' }" @click="filter = 'all'">全部</button>
          <button class="chip" :class="{ on: filter === 'programmable' }" @click="filter = 'programmable'">可编程</button>
          <button class="chip" :class="{ on: filter === 'unconfigured' }" @click="filter = 'unconfigured'">未配置</button>
        </div>
        <input v-model="keyword" class="input search" placeholder="搜索设备 / 型号 / IP" />
        <button class="btn ghost" @click="load">刷新</button>
      </div>
    </section>

    <!-- ============ 测试台装备运维 ============ -->
    <section class="ops">
      <!-- 待注册测试台导出 -->
      <div class="ops-col" v-show="activeTab === 'equipment'">
        <div class="ops-head">
          <span class="ops-title">待注册测试台</span>
          <span class="ops-badge" :class="{ warn: pending.count > 0 }">{{ pending.count }}</span>
          <span class="ops-sub">已注册 {{ pending.registered }}</span>
        </div>
        <div class="ops-body">
          <div v-if="!pending.count" class="ops-empty">
            暂无待注册（草稿）测试台；在「测试台导航 → 新增测试台」保存草稿后会出现在这里。
          </div>
          <ul v-else class="ops-list">
            <li v-for="p in pending.list" :key="p.id">
              <b>{{ p.title }}</b>
              <span class="mono">{{ p.serial }}</span>
              <em>{{ p.configured_count }}/{{ p.programmable_count }} 台已配置 · 第 {{ p.step }} 步</em>
            </li>
          </ul>
        </div>
        <div class="ops-foot">
          <button class="btn primary" :disabled="!!ops.busy" @click="doExport">
            {{ ops.busy === 'export' ? '导出中…' : '导出待注册测试台' }}
          </button>
          <button class="btn ghost" @click="loadPending">刷新</button>
        </div>
      </div>

      <!-- 已注册测试台导出 -->
      <div class="ops-col" v-show="activeTab === 'equipment'">
        <div class="ops-head">
          <span class="ops-title">已注册测试台</span>
          <span class="ops-badge ok">{{ registeredBenches.length }}</span>
          <span class="ops-sub">已选 {{ regSel.length }}</span>
        </div>
        <div class="ops-body">
          <div v-if="!registeredBenches.length" class="ops-empty">
            暂无已注册测试台；在「测试台导航」完成注册后会出现在这里，可一键导出其设备清单与连接参数。
          </div>
          <ul v-else class="ops-list pick">
            <li v-for="b in registeredBenches" :key="b.id">
              <input class="ops-check" type="checkbox" :checked="regSel.includes(b.id)" @change="toggleRegSel(b.id)" />
              <b>{{ b.title }}</b>
              <span class="mono">{{ b.serial }}</span>
              <em>{{ b.device_count }}/{{ b.programmable_count }} 台设备 · {{ overallText((b.verify_summary || {}).overall) }}</em>
            </li>
          </ul>
          <div class="ops-hint">
            导出内容为 <span class="mono">ate.testbench.export.v1</span> JSON：预设类型 / 工位 / 每台设备的型号·接口·IP·端口·串口·资源地址，
            可离线核对、备份现场配置或跨工控机迁移。
          </div>
        </div>
        <div class="ops-foot">
          <button class="btn primary" :disabled="!!ops.busy || !regSel.length" @click="exportRegistered(true)">
            {{ ops.busy === 'exportReg' ? '导出中…' : `导出选中（${regSel.length}）` }}
          </button>
          <button class="btn" :disabled="!!ops.busy || !registeredBenches.length" @click="exportRegistered(false)">
            {{ ops.busy === 'exportReg' ? '导出中…' : '导出全部已注册' }}
          </button>
          <button class="btn ghost" :disabled="!registeredBenches.length" @click="toggleAllReg">
            {{ regSel.length >= registeredBenches.length && registeredBenches.length ? '清空选择' : '全选' }}
          </button>
        </div>
      </div>

      <!-- TPS 运行环境 (workspace / 公共 conftest / driver 配置) -->
      <div class="ops-col" v-show="activeTab === 'equipment'">
        <div class="ops-head">
          <span class="ops-title">TPS 运行环境</span>
          <span class="ops-badge" :class="{ warn: rt.version !== rt.templateVersion }">v{{ rt.version }}</span>
          <span class="ops-sub">workspace + 公共 conftest</span>
        </div>
        <div class="ops-body">
          <div class="ops-hint">
            运行时把 TPS 复制一份临时副本到 workspace 下以 TPS 命名的目录里执行；公共 conftest.py 负责
            导入装备信息、测试阈值与所需设备 driver。
          </div>
          <ul class="ops-list">
            <li>
              <b>workspace 目录</b>
              <span class="mono">{{ rt.workspace || '未创建' }}</span>
            </li>
            <li>
              <b>公共 conftest</b>
              <span class="mono">conftest.py</span>
              <em>版本 v{{ rt.version }} / 模板 v{{ rt.templateVersion }}</em>
            </li>
            <li>
              <b>driver 配置 (SQLite)</b>
              <span class="mono">{{ rt.dbPath }}</span>
              <em>{{ rt.benchCount }} 台测试台 · {{ rt.deviceCount }} 台设备</em>
            </li>
          </ul>
          <div class="ops-sub">可解析的驱动规格（{{ rt.drivers.length }}）</div>
          <table class="drv-table">
            <thead>
              <tr><th>驱动规格</th><th>设备类别</th><th>动作</th></tr>
            </thead>
            <tbody>
              <tr v-for="d in rt.drivers" :key="d.key">
                <td class="mono">{{ d.key }}</td>
                <td>{{ d.label }}</td>
                <td>{{ (d.actions || []).length }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="ops-foot">
          <button class="btn" :disabled="!!rt.busy" @click="rtInit">
            {{ rt.busy === 'init' ? '初始化中…' : '初始化 / 校验 workspace' }}
          </button>
          <button class="btn ghost" :disabled="!!rt.busy" @click="rtSync">
            {{ rt.busy === 'sync' ? '同步中…' : '同步 driver 配置' }}
          </button>
        </div>
      </div>

      <!-- TPS 运行目录（workspace 临时副本） -->
      <div class="ops-col" v-show="activeTab === 'equipment'">
        <div class="ops-head">
          <span class="ops-title">TPS 运行目录</span>
          <span class="ops-badge">{{ rt.runs.length }}</span>
          <span class="ops-sub">保留最近 3 次</span>
        </div>
        <div class="ops-body">
          <label class="ops-field">
            <span>TPS 程序包</span>
            <select v-model="rt.tpsId" class="input" @change="loadRuns">
              <option v-for="t in rt.tpsList" :key="t.id" :value="t.id">{{ t.name }}（{{ t.id }}）</option>
            </select>
          </label>
          <div v-if="rtTps" class="ops-hint ok">
            {{ rtTps.schema }} · 阈值 {{ rtTps.testconfig_count }} 项 · 设备别名 {{ rtTps.device_alias_count }} 个 · 测试套 {{ rtTps.cmd_suit_count }} 条
          </div>
          <div v-if="!rt.runs.length" class="ops-empty">
            该 TPS 还没有运行记录；在「执行」页启动一次，就会在 workspace 下生成临时副本目录。
          </div>
          <ul v-else class="ops-list">
            <li v-for="r in rt.runs" :key="r.name">
              <b class="mono">{{ r.name }}</b>
              <span class="tag" :class="runTone(r)">{{ runResultText(r) }}</span>
              <em>{{ r.created_at }} · {{ sizeText(r.size_bytes) }}</em>
            </li>
          </ul>
          <div v-if="rt.lastResult" class="ops-hint ok">
            最近一次：{{ (rt.lastResult.summary || {}).passed }}/{{ (rt.lastResult.summary || {}).total }} 用例通过 ·
            测试台 {{ (rt.lastResult.bench || {}).serial || '—' }} · 模式 {{ modeText(rt.lastResult.mode) }}
          </div>
        </div>
        <div class="ops-foot">
          <button class="btn" :disabled="!rt.tpsId || !!rt.busy" @click="rtCleanup">
            {{ rt.busy === 'clean' ? '清理中…' : '清理旧运行目录' }}
          </button>
          <button class="btn ghost" @click="loadRuns">刷新</button>
        </div>
      </div>

      <!-- 初始化 / 终止 / 自检 -->
      <div class="ops-col" v-show="activeTab === 'selfcheck'">
        <div class="ops-head">
          <span class="ops-title">装备运维</span>
          <span class="ops-sub">作用于所选测试台</span>
        </div>
        <div class="ops-body">
          <label class="ops-field">
            <span>目标测试台</span>
            <select v-model="ops.benchId" class="input">
              <option v-for="b in benches" :key="b.id" :value="b.id">{{ b.title }}（{{ b.serial }}）</option>
            </select>
          </label>
          <label class="ops-field">
            <span>运行模式</span>
            <select v-model="ops.mode" class="input">
              <option value="simulate">离线模拟（不下发指令）</option>
              <option value="real">真实探测（现场连仪器）</option>
            </select>
          </label>
          <div class="ops-hint" :class="{ warn: ops.mode === 'real' }">
            {{ ops.mode === 'real'
              ? '真实模式会建链并下发 SCPI 指令，请先确认仪器上电、地址正确。'
              : '模拟模式只生成同构步骤记录，不会向仪器发送任何指令。' }}
          </div>
        </div>
        <div class="ops-foot">
          <button class="btn" :disabled="!ops.benchId || !!ops.busy" @click="runOps('init')">
            {{ ops.busy === 'init' ? '初始化中…' : '测试台装备初始化' }}
          </button>
          <button class="btn" :disabled="!ops.benchId || !!ops.busy" @click="runOps('teardown')">
            {{ ops.busy === 'teardown' ? '终止中…' : '测试台装备终止' }}
          </button>
          <button class="btn primary" :disabled="!ops.benchId || !!ops.busy" @click="runOps('selfcheck')">
            {{ ops.busy === 'selfcheck' ? '自检中…' : '测试台装备自检' }}
          </button>
        </div>
      </div>

      <!-- 报告区 -->
      <div class="ops-col" v-show="activeTab === 'reports'">
        <div class="ops-head">
          <span class="ops-title">报告区</span>
          <span class="ops-sub">历史报告落盘目录</span>
        </div>
        <div class="ops-body">
          <div class="ops-dir mono">{{ reports.dir || '—' }}</div>
          <div class="ops-hint">已保存 {{ reports.fileCount }} 个文件 · 占用 {{ sizeText(reports.sizeBytes) }}</div>
          <div class="ops-hint">报告文件同时保留在磁盘，可直接拿去归档或发给现场。</div>
        </div>
        <div class="ops-foot">
          <button class="btn" @click="openInExplorer(null)">打开本地资源管理器</button>
          <button class="btn ghost" @click="loadReports">刷新清单</button>
        </div>
      </div>
    </section>

    <!-- ============ 历史报告（属于「装备报告中心」栏） ============ -->
    <section class="reports" v-show="activeTab === 'reports'">
      <div class="rep-head">
        <div class="rep-title">历史硬件自检报告</div>
        <div class="chips">
          <button
            v-for="k in OPS_KINDS"
            :key="k.id"
            class="chip"
            :class="{ on: reports.kind === k.id }"
            @click="setReportKind(k.id)"
          >
            {{ k.label }}
          </button>
        </div>
      </div>
      <div v-if="!reports.list.length" class="tip">
        报告区暂无记录；执行一次「测试台装备自检」或「导出待注册测试台」后，记录会连带报告文件一起出现在这里。
      </div>
      <table v-else class="rep-table">
        <tr>
          <th>时间</th><th>类型</th><th>测试台</th><th>模式</th><th>结论</th><th>大小</th><th>操作</th>
        </tr>
        <tr v-for="r in reports.list" :key="r.file">
          <td class="mono">{{ r.created_at }}</td>
          <td><span class="badge" :class="{ 'b-prog': r.kind === 'selfcheck' }">{{ r.kind_label }}</span></td>
          <td>{{ r.bench_title || '—' }}<div class="rep-serial mono">{{ r.bench_serial }}</div></td>
          <td>{{ r.mode_label }}</td>
          <td><span class="rep-ov" :class="statusTone(r.overall)">{{ r.overall_label }}</span></td>
          <td class="mono">{{ sizeText(r.size) }}</td>
          <td class="rep-acts">
            <button class="lnk" @click="viewReport(r)">查看报告</button>
            <button class="lnk" @click="openInExplorer(r)">资源管理器</button>
          </td>
        </tr>
      </table>
    </section>

    <div v-show="activeTab === 'equipment'">
    <div v-if="loading" class="tip">正在加载装备属性…</div>
    <div v-else-if="error" class="tip err">{{ error }}</div>

    <!-- 空态 -->
    <div v-else-if="!devices.length" class="empty">
      <div class="empty-title">还没有可配置的设备</div>
      <div class="empty-text">
        设备来自测试台注册时的 BOM 清单。请先到「测试台导航 → 新增测试台」完成一台测试台的注册，这里会自动列出它的设备。
      </div>
    </div>

    <!-- 设备卡片 -->
    <div v-else class="grid">
      <article
        v-for="d in shown"
        :key="d.bench_id + ':' + d.id"
        class="card"
        :class="{ prog: d.programmable, nok: d.programmable && !d.configured }"
        @contextmenu="onContextMenu($event, d)"
        @dblclick="openEditor(d)"
      >
        <div class="card-top">
          <span class="dot" :class="d.configured ? 'ok' : (d.programmable ? 'warn' : 'gray')"></span>
          <span class="iface">{{ ifaceText(d.interface) }}</span>
          <span v-if="d.programmable" class="badge b-prog">可编程</span>
          <span v-else class="badge">只读</span>
          <span v-if="d.required" class="badge b-req">必备</span>
        </div>

        <div class="card-name">{{ d.name }}</div>
        <div class="card-model">{{ d.model || '—' }}<span v-if="d.vendor"> · {{ d.vendor }}</span></div>

        <div class="attrs">
          <div class="row">
            <span class="k">{{ isNet(d) ? 'IP : 端口' : isSer(d) ? '串口 @ 波特率' : '资源地址' }}</span>
            <span class="v mono">{{ resourceText(d) }}</span>
          </div>
          <div class="row">
            <span class="k">协议</span>
            <span class="v">{{ d.protocol || '—' }}</span>
          </div>
          <div class="row">
            <span class="k">用途</span>
            <span class="v">{{ d.role || d.category || '—' }}</span>
          </div>
          <div class="row">
            <span class="k">所属测试台</span>
            <span class="v">{{ d.bench_title }}</span>
          </div>
        </div>

        <div class="card-foot">
          <span class="foot-hint" :class="{ off: !d.programmable }">
            {{ d.programmable ? '右键 → 修改设备属性' : '非可编程设备 · 属性只读' }}
          </span>
          <span v-if="(d.config_history_count || 0) > 0" class="hist-chip" @click.stop="openHistory(d)">
            {{ d.config_history_count }} 次变更
          </span>
        </div>
      </article>
    </div>
    </div>

    <!-- 右键菜单 -->
    <div
      v-if="menu.open"
      class="ctx"
      :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
      @click.stop
    >
      <div class="ctx-head">
        <span class="dot" :class="menu.device && menu.device.configured ? 'ok' : 'warn'"></span>
        {{ menu.device && menu.device.name }}
      </div>
      <template v-if="menu.device && menu.device.programmable">
        <button class="ctx-item" @click="openEditor(menu.device)">
          <span class="ctx-i">✎</span>修改设备属性<span class="ctx-hint">IP / 端口</span>
        </button>
        <button class="ctx-item" @click="resetDefaultsFor(menu.device)">
          <span class="ctx-i">↺</span>恢复 BOM 默认值
        </button>
        <button class="ctx-item" @click="copyResource(menu.device)">
          <span class="ctx-i">⧉</span>复制资源地址
        </button>
        <button class="ctx-item" :disabled="!(menu.device.config_history_count > 0)" @click="openHistory(menu.device)">
          <span class="ctx-i">≡</span>查看变更历史
        </button>
      </template>
      <template v-else>
        <div class="ctx-ro">该设备不可程控，连接属性只读</div>
        <button class="ctx-item" @click="copyResource(menu.device)">
          <span class="ctx-i">⧉</span>复制设备信息
        </button>
      </template>
    </div>

    <!-- 编辑弹窗 -->
    <div v-if="dialog.open" class="mask" @click.self="dialog = { ...dialog, open: false }">
      <div class="modal">
        <div class="modal-head">
          <div>
            <div class="modal-title">修改设备属性 · {{ dialog.device.name }}</div>
            <div class="modal-sub">
              {{ dialog.device.model }} · {{ ifaceText(dialog.device.interface) }} · {{ benchOf(dialog.device).title }}
            </div>
          </div>
          <button class="x" @click="dialog = { ...dialog, open: false }">✕</button>
        </div>

        <div class="modal-body">
          <div v-if="dialog.errors.length" class="err-box">
            <div v-for="(e, i) in dialog.errors" :key="i">{{ e }}</div>
          </div>

          <!-- 只读的 BOM 元信息 -->
          <div class="ro-grid">
            <div class="ro"><span>类别</span><b>{{ dialog.device.category || '—' }}</b></div>
            <div class="ro"><span>用途</span><b>{{ dialog.device.role || '—' }}</b></div>
            <div class="ro"><span>接口</span><b>{{ ifaceText(dialog.device.interface) }}</b></div>
            <div class="ro"><span>是否必备</span><b>{{ dialog.device.required ? '必备' : '选配' }}</b></div>
          </div>
          <div class="ro-note">型号 / 厂商 / 类别等 BOM 元信息由预设测试台类型决定，不可在此修改。</div>

          <!-- 按接口类型渲染可编辑字段 -->
          <div class="form">
            <template v-if="isNet(dialog.device)">
              <label class="fld">
                <span>IP / 主机名</span>
                <input v-model="dialog.form.host" class="input mono" placeholder="192.168.10.21" />
              </label>
              <label class="fld">
                <span>端口</span>
                <input v-model="dialog.form.port" class="input mono" type="number" min="1" max="65535" placeholder="4000" />
              </label>
              <label class="fld">
                <span>协议</span>
                <input v-model="dialog.form.protocol" class="input" placeholder="SCPI / VXI-11 / Socket" />
              </label>
              <div class="fld-hint">常见端口：泰克示波器 4000、SCPI-RAW 5025、ITECH 电源 30000、Fluke 万用表 3490。</div>
            </template>

            <template v-else-if="isSer(dialog.device)">
              <label class="fld">
                <span>串口号</span>
                <input v-model="dialog.form.serial_port" class="input mono" placeholder="COM3" />
              </label>
              <label class="fld">
                <span>波特率</span>
                <select v-model="dialog.form.baudrate" class="input">
                  <option :value="null">— 未设置 —</option>
                  <option v-for="b in BAUD_PRESETS" :key="b" :value="b">{{ b }}</option>
                </select>
              </label>
              <label class="fld">
                <span>协议</span>
                <input v-model="dialog.form.protocol" class="input" placeholder="SCPI / Modbus-RTU / 自定义" />
              </label>
            </template>

            <template v-else>
              <label class="fld">
                <span>资源地址</span>
                <input v-model="dialog.form.address" class="input mono" placeholder="GPIB0::22::INSTR / USB0::0x0699::0x0401::..." />
              </label>
              <label class="fld">
                <span>通道号</span>
                <input v-model="dialog.form.channel" class="input mono" placeholder="如 1 / A" />
              </label>
              <label class="fld">
                <span>协议</span>
                <input v-model="dialog.form.protocol" class="input" placeholder="SCPI / 自定义" />
              </label>
            </template>

            <label class="fld wide">
              <span>备注</span>
              <input v-model="dialog.form.note" class="input" placeholder="现场标定信息 / 接线说明" />
            </label>
          </div>
        </div>

        <div class="modal-foot">
          <button class="btn ghost" @click="resetDefaults">恢复 BOM 默认值</button>
          <div class="spacer"></div>
          <button class="btn ghost" @click="dialog = { ...dialog, open: false }">取消</button>
          <button class="btn primary" :disabled="dialog.saving" @click="save()">
            {{ dialog.saving ? '保存中…' : '保存属性' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 变更历史 -->
    <div v-if="history.open" class="mask" @click.self="history = { ...history, open: false }">
      <div class="modal narrow">
        <div class="modal-head">
          <div class="modal-title">变更历史 · {{ history.device.name }}</div>
          <button class="x" @click="history = { ...history, open: false }">✕</button>
        </div>
        <div class="modal-body">
          <div v-if="!(history.rows || []).length" class="tip">暂无变更记录</div>
          <div v-for="(e, i) in history.rows" :key="i" class="hist-row">
            <div class="hist-at">{{ e.at }}<span v-if="e.reset_defaults" class="hist-tag">恢复默认</span></div>
            <div class="hist-txt">{{ entryText(e.changes) }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 报告查看 -->
    <div v-if="reportView.open" class="mask" @click.self="reportView = { ...reportView, open: false }">
      <div class="modal wide">
        <div class="modal-head">
          <div>
            <div class="modal-title">{{ reportView.title }}</div>
            <div class="modal-sub mono">{{ (reportView.meta && reportView.meta.path) || '' }}</div>
          </div>
          <button class="x" @click="reportView = { ...reportView, open: false }">✕</button>
        </div>
        <div class="modal-body rep-body">
          <div v-if="reportView.meta && reportView.meta.result" class="rep-verdict">
            <span class="rep-ov big" :class="statusTone(reportView.meta.result.overall)">
              {{ overallText(reportView.meta.result.overall) }}
            </span>
            <span class="rep-txt">{{ reportView.meta.result.conclusion }}</span>
            <span v-if="reportView.meta.mode === 'simulate'" class="badge">离线模拟 · 非真机实测</span>
          </div>
          <div v-else-if="reportView.meta" class="rep-verdict">
            <span class="rep-ov big" :class="statusTone(reportView.meta.overall)">{{ reportView.meta.overall_label }}</span>
            <span class="rep-txt">{{ reportView.meta.mode_label }} · {{ reportView.meta.kind_label }}</span>
          </div>
          <iframe class="rep-frame" :src="reportView.url" title="报告"></iframe>
        </div>
        <div class="modal-foot">
          <span class="rep-path mono">{{ (reportView.meta && reportView.meta.path) || '' }}</span>
          <div class="spacer"></div>
          <button class="btn ghost" @click="openInExplorer(reportView.meta)">在资源管理器中定位</button>
          <a class="btn ghost" :href="reportView.url" target="_blank" rel="noopener">新窗口打开</a>
          <button class="btn primary" @click="reportView = { ...reportView, open: false }">关闭</button>
        </div>
      </div>
    </div>

    <!-- 导出结果 -->
    <div v-if="exportDlg.open" class="mask" @click.self="exportDlg = { ...exportDlg, open: false }">
      <div class="modal">
        <div class="modal-head">
          <div class="modal-title">{{ ((exportDlg.row || {}).content || {}).scope_label || '待注册' }}测试台导出</div>
          <button class="x" @click="exportDlg = { ...exportDlg, open: false }">✕</button>
        </div>
        <div class="modal-body">
          <div class="tip">{{ exportDlg.row && exportDlg.row.message }}</div>
          <div v-if="exportDlg.row" class="kv-grid">
            <div class="ro"><span>导出条数</span><b>{{ exportDlg.row.count }} 台</b></div>
            <div class="ro"><span>作用范围</span><b>{{ (exportDlg.row.content || {}).scope_label || '待注册' }}（{{ (exportDlg.row.content || {}).scope }}）</b></div>
            <div class="ro"><span>状态构成</span><b>已注册 {{ ((exportDlg.row.content || {}).by_status || {}).registered || 0 }} 台 / 草稿 {{ ((exportDlg.row.content || {}).by_status || {}).draft || 0 }} 台</b></div>
            <div class="ro wide"><span>文件名</span><b class="mono">{{ exportDlg.row.filename }}</b></div>
            <div class="ro wide"><span>落盘路径</span><b class="mono">{{ exportDlg.row.path }}</b></div>
          </div>
          <div class="ro-note">
            导出文件已随本次操作保存到报告区目录，可用「在资源管理器中定位」查看；浏览器同时下载了一份 JSON，可线下核对设备清单与连接参数，或迁移到其他工控机后重新导入。
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" @click="openInExplorer(exportDlg.row && exportDlg.row.report)">在资源管理器中定位</button>
          <div class="spacer"></div>
          <button class="btn" @click="reDownload">重新下载</button>
          <button class="btn primary" @click="exportDlg = { ...exportDlg, open: false }">关闭</button>
        </div>
      </div>
    </div>

    <!-- 轻提示 -->
    <div v-if="toast.text" class="toast" :class="toast.kind">{{ toast.text }}</div>
  </div>
</template>

<style scoped>
.eq-page { flex: 1; padding: 18px 22px 26px; display: flex; flex-direction: column; gap: 14px; overflow-y: auto; }

/* ---- 头部 ---- */
.eq-head {
  display: flex; align-items: center; justify-content: space-between; gap: 18px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px;
  border-top: 3px solid var(--accent);
}
.hd-title { font-size: 19px; font-weight: 800; letter-spacing: 0.5px; }
.hd-sub { color: var(--muted); font-size: 12.5px; margin-top: 4px; }
.hd-sub b { color: var(--accent); }
.hd-metrics { display: flex; gap: 10px; }
.metric {
  min-width: 74px; text-align: center; padding: 8px 10px; border-radius: 10px;
  background: var(--panel-2); border: 1px solid var(--border);
}
.metric b { display: block; font-family: var(--mono); font-size: 17px; color: var(--accent); }
.metric span { font-size: 11px; color: var(--muted); }
.metric.warn b { color: var(--warn); }

/* ---- 工具条 ---- */
.bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.bench-tabs { display: flex; gap: 6px; flex-wrap: wrap; flex: 1; min-width: 0; }
.tab {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 11px; border-radius: 8px; font-size: 12.5px; cursor: pointer;
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
}
.tab i { font-family: var(--mono); font-size: 11px; color: var(--muted); font-style: normal; }
.tab:hover { border-color: rgba(var(--accent-rgb), 0.45); }
.tab.on {
  border-color: rgba(var(--accent-rgb), 0.55); background: rgba(var(--accent-rgb), 0.1);
  color: var(--accent); font-weight: 700;
}
.tab.on i { color: var(--accent); }
.tab-dot { width: 7px; height: 7px; border-radius: 50%; }
.tab-dot.ok { background: var(--ok); }
.tab-dot.draft { background: var(--warn); }
.bar-right { display: flex; align-items: center; gap: 8px; }
.chips { display: flex; gap: 4px; }
.chip {
  padding: 5px 10px; border-radius: 999px; font-size: 12px; cursor: pointer;
  background: var(--panel); border: 1px solid var(--border); color: var(--muted);
}
.chip.on { background: rgba(var(--accent-rgb), 0.12); border-color: rgba(var(--accent-rgb), 0.5); color: var(--accent); font-weight: 700; }
.search { width: 210px; }

/* ---- 卡片网格 ---- */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(298px, 1fr)); gap: 14px; }
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 13px 14px;
  display: flex; flex-direction: column; gap: 9px; cursor: default; position: relative;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}
.card:hover { border-color: rgba(var(--accent-rgb), 0.4); transform: translateY(-1px); }
.card.prog { border-left: 3px solid rgba(var(--accent-rgb), 0.55); }
.card.prog:hover { box-shadow: 0 6px 18px rgba(var(--accent-rgb), 0.1); }
.card.nok { border-left-color: var(--warn); }
.card-top { display: flex; align-items: center; gap: 7px; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }
.dot.ok { background: var(--ok); box-shadow: 0 0 0 3px rgba(21, 128, 61, 0.16); }
.dot.warn { background: var(--warn); box-shadow: 0 0 0 3px rgba(180, 83, 9, 0.16); }
.dot.gray { background: #a8a3bd; }
.iface {
  font-size: 11px; color: var(--muted); border: 1px solid var(--border); border-radius: 5px;
  padding: 1px 6px; background: var(--panel-2);
}
.badge {
  font-size: 11px; padding: 1px 7px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--panel-2); color: var(--muted);
}
.b-prog { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.35); background: rgba(var(--accent-rgb), 0.1); }
.b-req { color: var(--warn); border-color: rgba(180, 83, 9, 0.35); background: rgba(180, 83, 9, 0.09); }
.card-name { font-size: 15px; font-weight: 700; }
.card-model { font-size: 12px; color: var(--muted); margin-top: -6px; }
.attrs { display: flex; flex-direction: column; gap: 5px; border-top: 1px dashed var(--border); padding-top: 8px; }
.row { display: flex; justify-content: space-between; gap: 10px; font-size: 12.5px; }
.k { color: var(--muted); white-space: nowrap; }
.v { text-align: right; word-break: break-all; }
.mono { font-family: var(--mono); }
.card-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; border-top: 1px solid var(--border); padding-top: 7px; }
.foot-hint { font-size: 11.5px; color: var(--accent); }
.foot-hint.off { color: var(--muted); }
.hist-chip {
  font-size: 11px; font-family: var(--mono); color: var(--muted); cursor: pointer;
  border: 1px solid var(--border); border-radius: 999px; padding: 1px 7px; background: var(--panel-2);
}
.hist-chip:hover { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.4); }

/* ---- 右键菜单 ---- */
.ctx {
  position: fixed; z-index: 60; min-width: 208px; padding: 5px;
  background: #fff; border: 1px solid var(--border-strong); border-radius: 10px;
  box-shadow: 0 14px 34px rgba(29, 23, 51, 0.18);
}
.ctx-head {
  display: flex; align-items: center; gap: 7px; padding: 7px 9px 8px; margin-bottom: 3px;
  border-bottom: 1px solid var(--border); font-size: 12.5px; font-weight: 700; color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ctx-item {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
  padding: 7px 9px; border: none; background: transparent; border-radius: 7px;
  font-size: 13px; color: var(--text); cursor: pointer;
}
.ctx-item:hover:not(:disabled) { background: rgba(var(--accent-rgb), 0.1); color: var(--accent); }
.ctx-item:disabled { color: #b6b1c9; cursor: not-allowed; }
.ctx-i { width: 14px; text-align: center; color: var(--accent); }
.ctx-hint { margin-left: auto; font-size: 11px; color: var(--muted); }
.ctx-ro { padding: 8px 10px; font-size: 12px; color: var(--muted); }

/* ---- 弹窗 ---- */
.mask {
  position: fixed; inset: 0; z-index: 70; background: rgba(29, 23, 51, 0.34);
  display: flex; align-items: center; justify-content: center; padding: 24px; backdrop-filter: blur(2px);
}
.modal {
  width: 100%; max-width: 620px; max-height: 88vh; display: flex; flex-direction: column;
  background: #fff; border: 1px solid var(--border-strong); border-radius: 14px; overflow: hidden;
  box-shadow: 0 22px 60px rgba(29, 23, 51, 0.28);
}
.modal.narrow { max-width: 480px; }
.modal-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  padding: 15px 18px; border-bottom: 1px solid var(--border); border-top: 3px solid var(--accent);
}
.modal-title { font-size: 16px; font-weight: 800; }
.modal-sub { font-size: 12px; color: var(--muted); margin-top: 3px; }
.x { border: none; background: transparent; font-size: 15px; color: var(--muted); cursor: pointer; }
.x:hover { color: var(--err); }
.modal-body { padding: 16px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.modal-foot {
  display: flex; align-items: center; gap: 8px; padding: 12px 18px;
  border-top: 1px solid var(--border); background: var(--panel-2);
}
.spacer { flex: 1; }

.ro-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
.ro { background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; }
.ro span { display: block; font-size: 11px; color: var(--muted); }
.ro b { font-size: 13px; }
.ro-note { font-size: 11.5px; color: var(--muted); }

.form { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.fld { display: flex; flex-direction: column; gap: 5px; }
.fld.wide { grid-column: 1 / -1; }
.fld span { font-size: 12px; color: var(--muted); }
.fld-hint { grid-column: 1 / -1; font-size: 11.5px; color: var(--muted); }
.form .input { width: 100%; box-sizing: border-box; }

.err-box {
  background: rgba(220, 38, 38, 0.09); border: 1px solid rgba(220, 38, 38, 0.32);
  border-radius: 9px; padding: 9px 12px; color: var(--err); font-size: 12.5px;
}
.hist-row { border-bottom: 1px dashed var(--border); padding-bottom: 8px; margin-bottom: 8px; }
.hist-at { font-family: var(--mono); font-size: 11.5px; color: var(--muted); display: flex; gap: 8px; align-items: center; }
.hist-tag { border: 1px solid rgba(var(--accent-rgb), 0.35); background: rgba(var(--accent-rgb), 0.1); color: var(--accent); border-radius: 999px; padding: 0 6px; }
.hist-txt { font-size: 12.5px; margin-top: 3px; word-break: break-all; }

/* ---- 空态 / 提示 ---- */
.tip { color: var(--muted); background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; font-size: 13px; }
.tip.err { color: var(--err); background: rgba(220, 38, 38, 0.08); border-color: rgba(220, 38, 38, 0.3); }
.empty {
  background: var(--panel); border: 1px dashed var(--border-strong); border-radius: 12px;
  padding: 30px 24px; text-align: center;
}
.empty-title { font-size: 16px; font-weight: 700; margin-bottom: 6px; }
.empty-text { color: var(--muted); font-size: 13px; max-width: 560px; margin: 0 auto; }
.toast {
  position: fixed; right: 22px; bottom: 22px; z-index: 80; max-width: 420px;
  background: #fff; border: 1px solid rgba(var(--accent-rgb), 0.4); border-left: 4px solid var(--accent);
  border-radius: 10px; padding: 11px 14px; font-size: 13px; box-shadow: 0 12px 30px rgba(29, 23, 51, 0.16);
}
.toast.warn { border-color: rgba(180, 83, 9, 0.45); border-left-color: var(--warn); color: var(--warn); }

/* ---- 装备运维面板 ---- */
.ops { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
.ops-col {
  display: flex; flex-direction: column; gap: 10px;
  flex: 1 1 320px; max-width: 620px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px;
}
.ops-head { display: flex; align-items: center; gap: 8px; }
.ops-title { font-size: 13.5px; font-weight: 800; }
.ops-badge {
  font-family: var(--mono); font-size: 12px; padding: 0 8px; border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.12); color: var(--accent); border: 1px solid rgba(var(--accent-rgb), 0.4);
}
.ops-badge.warn { background: rgba(180, 83, 9, 0.1); color: var(--warn); border-color: rgba(180, 83, 9, 0.4); }
.ops-sub { margin-left: auto; font-size: 11.5px; color: var(--muted); }
.ops-body { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.ops-empty {
  font-size: 12.5px; color: var(--muted); background: var(--panel-2);
  border: 1px dashed var(--border); border-radius: 8px; padding: 10px 12px;
}
.ops-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.ops-list li {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px; font-size: 12.5px;
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px;
}
.ops-list em { margin-left: auto; font-style: normal; font-size: 11.5px; color: var(--muted); }
.ops-field { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.ops-field > span { width: 72px; color: var(--muted); white-space: nowrap; }
.ops-field .input { flex: 1; min-width: 0; }
.ops-hint { font-size: 11.5px; color: var(--muted); }
.ops-hint.warn { color: var(--warn); }
.ops-hint.ok { color: var(--ok); }
.ops-list.pick li { align-items: center; }
.ops-check { width: 14px; height: 14px; margin: 0; accent-color: var(--accent); flex: none; cursor: pointer; }
.ops-badge.ok { color: var(--ok); border-color: rgba(21, 128, 61, 0.4); background: rgba(21, 128, 61, 0.1); }

/* TPS 运行环境面板：驱动规格表 + 运行结果标签 */
.drv-table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
.drv-table th { text-align: left; color: var(--muted); font-weight: 600; padding: 3px 6px; border-bottom: 1px solid var(--border); }
.drv-table td { padding: 3px 6px; border-bottom: 1px dashed var(--border); }
.drv-table tr:last-child td { border-bottom: none; }
.tag {
  display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11px;
  border: 1px solid var(--border); background: var(--panel-2); color: var(--muted);
}
.tag.ok { color: var(--ok); border-color: rgba(21, 128, 61, 0.3); background: rgba(21, 128, 61, 0.08); }
.tag.err { color: var(--err); border-color: rgba(220, 38, 38, 0.3); background: rgba(220, 38, 38, 0.08); }
.tag.muted { color: var(--muted); }
@media (max-width: 720px) {
  .drv-table { font-size: 11px; }
  .ops-field > span { width: 62px; }
}
.ops-dir {
  font-size: 12px; word-break: break-all;
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
}
.ops-foot { display: flex; gap: 8px; flex-wrap: wrap; }

/* ---- 历史报告区 ---- */
.reports {
  display: flex; flex-direction: column; gap: 10px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px;
}
.rep-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.rep-title { font-size: 13.5px; font-weight: 800; }
.rep-head .chips { margin-left: auto; }
.rep-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.rep-table th { text-align: left; font-weight: 600; color: var(--muted); background: var(--panel-2); }
.rep-table th, .rep-table td { border-bottom: 1px solid var(--border); padding: 7px 8px; vertical-align: top; }
.rep-table tr:hover td { background: rgba(var(--accent-rgb), 0.05); }
.rep-serial { font-size: 11px; color: var(--muted); }
.rep-acts { white-space: nowrap; }
.lnk { border: none; background: transparent; color: var(--accent); font-size: 12.5px; cursor: pointer; padding: 0 4px; }
.lnk:hover { text-decoration: underline; }
.rep-ov { font-weight: 700; }
.rep-ov.ok { color: var(--ok); }
.rep-ov.warn { color: var(--warn); }
.rep-ov.err { color: var(--err); }
.rep-ov.muted { color: var(--muted); }
.rep-ov.big { font-size: 14px; padding: 4px 12px; border-radius: 999px; color: #fff; }
.rep-ov.big.ok { background: var(--ok); }
.rep-ov.big.warn { background: var(--warn); }
.rep-ov.big.err { background: var(--err); }
.rep-ov.big.muted { background: #a8a3bd; }
.rep-body { gap: 10px; }
.rep-verdict { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 12.5px; }
.rep-txt { color: var(--muted); }
.rep-frame { width: 100%; flex: 1; min-height: 52vh; border: 1px solid var(--border); border-radius: 10px; background: #fff; }
.modal.wide { max-width: 1020px; }
.modal.wide .modal-body { display: flex; flex-direction: column; }
.rep-path { font-size: 11px; color: var(--muted); word-break: break-all; max-width: 46%; }
.kv-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 8px; }
.kv-grid .ro.wide { grid-column: 1 / -1; }

/* ---- 分栏切换（装备属性配置 / 测试台装备自检 / 装备报告中心） ---- */
.view-tabs {
  display: flex; gap: 6px; flex-wrap: wrap;
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 6px;
}
.view-tab {
  display: inline-flex; align-items: center; gap: 8px;
  border: 1px solid transparent; background: transparent; color: var(--muted);
  font-size: 13px; font-weight: 600; padding: 8px 14px; border-radius: 9px; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.view-tab:hover { background: rgba(var(--accent-rgb), 0.07); color: var(--ink); }
.view-tab.on {
  background: var(--bg); color: var(--accent); border-color: var(--border-strong);
  box-shadow: 0 1px 3px rgba(36, 27, 63, 0.07);
}
.vt-ico { font-size: 14px; line-height: 1; }
.vt-badge {
  font-family: var(--mono); font-size: 11px; padding: 0 7px; border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.12); color: var(--accent); border: 1px solid rgba(var(--accent-rgb), 0.35);
}
.vt-badge.warn { background: rgba(180, 83, 9, 0.1); color: var(--warn); border-color: rgba(180, 83, 9, 0.4); }
@media (max-width: 720px) {
  .view-tab { flex: 1 1 100%; justify-content: flex-start; }
}
</style>
