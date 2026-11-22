<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { api } from '../api'
// 运行档位（全局共享）: 生产模式需 12 位工单号 + 真机 + 上报云端;
// 调试模式免工单, 可切「仿真模式」不连接真实设备。
import {
  runMode,
  isProduction,
  simulate,
  workOrder,
  workOrderValid,
  normalizedWorkOrder,
  deviceModeText,
  WORK_ORDER_LEN,
  RUN_MODE_META,
  setRunMode,
  toggleSimulate,
  runOptions
} from '../runmode'

// 切换仿真开关时的过渡态（保留给后续接后端设备自检用）
const simulateBusy = ref(false)

// ---- 状态 ----
const tree = ref([])
const loadingTree = ref(true)
const listError = ref('')
const expandedProjects = ref(new Set())
const expandedDuts = ref(new Set())
const selectedTpsId = ref('')
const tpsDetail = ref(null)
const loadingDetail = ref(false)

const running = ref(false)
const currentTask = ref(null)
const reportUrl = ref('')
const reportLoaded = ref(false)
const reportError = ref('')
// ---- 云端上报（生产模式）----
const cloud = ref(null)            // 当前任务的云端上报结果
const cloudError = ref('')

// ---- 装备软件部署 ----
const deploying = ref(false)
const deployBatch = ref('')
const deployItems = ref([])
const deployMsg = ref('')
const deployError = ref('')
let deployTimer = null
const deployInstalled = ref([])
// 部署清单是否隐藏：操作员可收起列表，只看按钮；偏好持久化在本机
const DEPLOY_LIST_KEY = 'ate_deploy_list_hidden'
const deployListHidden = ref(localStorage.getItem(DEPLOY_LIST_KEY) === '1')
const deployListCount = computed(() => deployItems.value.length + deployInstalled.value.length)
const toggleDeployList = () => {
  deployListHidden.value = !deployListHidden.value
  localStorage.setItem(DEPLOY_LIST_KEY, deployListHidden.value ? '1' : '0')
}
const cloudReuploading = ref(false)
const cloudStatus = ref(null)      // 云端（演示）连接状态与入库统计
const cloudRecords = ref([])       // 云端库已入库记录

async function loadCloudStatus() {
  try {
    const res = await api.getCloudStatus()
    cloudStatus.value = res && res.success !== false ? res : null
  } catch {
    cloudStatus.value = null
  }
}

async function loadCloudRecords() {
  try {
    const res = await api.getCloudRecords({ limit: 20 })
    cloudRecords.value = res && res.success ? res.records || [] : []
  } catch {
    cloudRecords.value = []
  }
}

// 云端不可达时记录留 pending, 这里手动重传（幂等, 可反复点）
async function reuploadCloud() {
  const id = currentTask.value?.id
  if (!id || cloudReuploading.value) return
  cloudReuploading.value = true
  cloudError.value = ''
  try {
    const res = await api.uploadToCloud(id)
    cloud.value = res
    if (!res.success) cloudError.value = res.message || '重传失败'
    else await Promise.all([loadCloudStatus(), loadCloudRecords()])
  } catch (e) {
    cloudError.value = '重传失败：' + e.message
  } finally {
    cloudReuploading.value = false
  }
}
// ---- AI 测试分析 ----
const aiConfigOpen = ref(false)
const aiApiUrl = ref(localStorage.getItem('ate_ai_api_url') || '')
const aiApiKey = ref(localStorage.getItem('ate_ai_api_key') || '')
const aiConfigMsg = ref('')
const aiAnalyzing = ref(false)
const aiAnalysis = ref('')
const aiError = ref('')
const logEl = ref(null)

// ---- 执行日志等级筛选 ----
// 后端把每条日志标注为 error / warning / info，并在行首加 [LEVEL] 前缀；
// 这里按等级筛选，便于在海量日志中只看报错。
const LOG_DISPLAY_MAX = 1500
const levelOn = ref({ error: true, warning: true, info: true })
const taskLogs = computed(() => currentTask.value?.logs || [])
const logCounts = computed(() => {
  const c = { error: 0, warning: 0, info: 0 }
  for (const l of taskLogs.value) {
    const lv = l?.level
    if (lv === 'error' || lv === 'warning' || lv === 'info') c[lv] += 1
    else c.info += 1
  }
  return c
})
const filteredLogs = computed(() => {
  const out = []
  for (const l of taskLogs.value) {
    if (levelOn.value[l?.level] !== false) out.push(l)
  }
  return out.length > LOG_DISPLAY_MAX ? out.slice(-LOG_DISPLAY_MAX) : out
})
const onlyErrorMode = computed(
  () => levelOn.value.error && !levelOn.value.warning && !levelOn.value.info
)
function toggleLevel(lv) {
  levelOn.value = { ...levelOn.value, [lv]: !levelOn.value[lv] }
}
function showAllLevels() {
  levelOn.value = { error: true, warning: true, info: true }
}
function onlyErrors() {
  levelOn.value = onlyErrorMode.value
    ? { error: true, warning: true, info: true }
    : { error: true, warning: false, info: false }
}

function toggleAiConfig() {
  aiConfigOpen.value = !aiConfigOpen.value
}

function saveAiConfig() {
  localStorage.setItem('ate_ai_api_url', aiApiUrl.value.trim())
  localStorage.setItem('ate_ai_api_key', aiApiKey.value.trim())
  aiConfigMsg.value = '✅ AI 配置已保存（仅存于本机浏览器）'
  setTimeout(() => (aiConfigMsg.value = ''), 2500)
}

async function analyzeWithAi() {
  const taskId = currentTask.value?.id
  if (!taskId) {
    aiError.value = '请先执行 TPS 产生测试任务'
    return
  }
  aiError.value = ''
  aiAnalysis.value = ''
  aiAnalyzing.value = true
  try {
    const res = await api.aiAnalyze(aiApiUrl.value.trim(), aiApiKey.value.trim(), taskId)
    if (res.success) {
      aiAnalysis.value = res.analysis || '（AI 未返回分析内容）'
    } else {
      aiError.value = res.message || 'AI 分析失败'
    }
  } catch (e) {
    aiError.value = 'AI 分析请求失败：' + e.message
  } finally {
    aiAnalyzing.value = false
  }
}

// ---- UUT ----
const uutInput = ref('')
const uutError = ref('')
const UUT_RE = /^[A-Za-z0-9]{12}$/

let pollTimer = null

// ---- 计算属性 ----
const selectedTps = computed(() => {
  for (const p of tree.value)
    for (const d of p.duts)
      for (const t of d.tps) if (t.id === selectedTpsId.value) return t
  return null
})
const environment = computed(() => tpsDetail.value?.environment || {})
const steps = computed(() => tpsDetail.value?.steps || [])
const stepState = computed(() => {
  const map = {}
  for (const s of currentTask.value?.steps || []) map[s.index] = s
  return map
})
// ---- 人工操作环节（TPS 清单 cmd_suit 里 type=manual 的条目）----
const manual = computed(() => currentTask.value?.manual || null)
const manualOpen = computed(() => manual.value?.status === 'waiting')
const manualSubmitting = ref(false)
const manualSubmitted = ref('')     // 已提交的判定：按钮禁用并显示「已提交」，避免重复点击
const manualNote = ref('')          // 判定失败的备注（写入该步结果与报告）
const manualNoteOpen = ref(false)   // 点「判定失败」后展开的备注输入框
const manualBusy = computed(() => manualSubmitting.value || !!manualSubmitted.value)
const manualError = ref('')
// 提示内容由后端从 TPS 包 resource/ 目录读出并随任务快照下发
const manualHtml = computed(() => renderMarkdown(manual.value?.markdown || '', selectedTpsId.value))
// ---- 单步执行（cmd_suit 条目 step=true）----
// 该条用例跑完后 Runner 暂停：界面弹出「下一步」按钮，放行后才执行清单里的下一条
const manualKind = computed(() => manual.value?.kind || 'manual')
const manualIsManual = computed(() => manualOpen.value && manualKind.value === 'manual')
const stepHoldOpen = computed(() => manualOpen.value && manualKind.value === 'step')
// 新的暂停点到达时清掉上一次的提交态（同一任务可能连续出现多条人工确认）
// requested_at 只到秒：单步暂停与紧随其后的人工确认可能落在同一秒，只盯时间戳会漏掉切换，
// 因此把 kind 与暂停点 id 一并作为 watch 键。
watch(
  () => [manual.value?.kind, manual.value?.id, manual.value?.requested_at].join('|'),
  () => {
    manualSubmitted.value = ''
    manualNote.value = ''
    manualNoteOpen.value = false
    manualError.value = ''
  }
)

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function inlineMd(s) {
  return escapeHtml(s)
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, '<img src="$2" alt="$1" />')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<span class="md-link">$1</span>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
}

// 极简 Markdown 渲染（标题 / 列表 / 引用 / 分隔线 / 粗体 / 行内代码 / 图片），先转义再套格式
function renderMarkdown(md, tpsId) {
  const lines = String(md || '').replace(/\r\n/g, '\n').split('\n')
  const out = []
  let list = null
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null } }
  for (const raw of lines) {
    const line = raw.trimEnd()
    if (!line.trim()) { closeList(); continue }
    let m
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      closeList()
      const lvl = Math.min(m[1].length + 1, 6)
      out.push(`<h${lvl}>${inlineMd(m[2])}</h${lvl}>`)
      continue
    }
    if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul' }
      out.push(`<li>${inlineMd(m[1])}</li>`)
      continue
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol' }
      out.push(`<li>${inlineMd(m[1])}</li>`)
      continue
    }
    if ((m = line.match(/^\s*>\s?(.*)$/))) {
      closeList(); out.push(`<blockquote>${inlineMd(m[1])}</blockquote>`); continue
    }
    if (/^\s*([-*_]\s*){3,}$/.test(line)) { closeList(); out.push('<hr />'); continue }
    closeList(); out.push(`<p>${inlineMd(line)}</p>`)
  }
  closeList()
  let html = out.join('\n')
  // 提示里的图片按 TPS 包 resource/ 取（相对路径 -> 后端资源接口）
  if (tpsId) {
    html = html.replace(/src="([^"]+)"/g, (whole, src) => {
      if (/^(https?:|data:|\/)/.test(src)) return whole
      return `src="/api/tps/${encodeURIComponent(tpsId)}/resource/${src}"`
    })
  }
  return html
}

// ---- 单用例 manual check：人工确认后才回传机器常数与测角结果（SR02-AR04）----
const checkState = ref({})       // { stepIndex: { decision, note, at } }
const checkSubmitting = ref(0)   // 正在提交的步骤序号，0 表示空闲
const checkError = ref('')

function stepNeedsCheck(st) {
  return st?.manual_check === true || (st?.manual_check && typeof st.manual_check === 'object')
}

async function loadChecks() {
  const id = currentTask.value?.id
  if (!id) return
  try {
    const res = await api.manualChecks(id)
    if (res && res.success) {
      // 待确认接口只回未决项：保留本地已确认/已驳回的记录，避免状态回退
      const map = { ...checkState.value }
      for (const item of res.items || []) map[item.step_index] = item
      checkState.value = map
    }
  } catch (e) {
    // 未挂载时静默：不阻断执行主链路
  }
}

async function submitCheck(stepIndex, decision) {
  const id = currentTask.value?.id
  if (!id || checkSubmitting.value) return
  let note = ''
  if (decision === 'reject') {
    note = window.prompt('驳回原因（必填，将写入记录）', '') || ''
    if (!note.trim()) return
  }
  checkSubmitting.value = stepIndex
  checkError.value = ''
  try {
    const res = await api.decideManualCheck(id, stepIndex, decision, note)
    if (!res.success) checkError.value = res.message || '提交失败'
    else {
      // 就地记录决策：任务结束后轮询已停，不能只依赖下一次 loadChecks
      checkState.value = {
        ...checkState.value,
        [stepIndex]: { step_index: stepIndex, decision, note, pushed: res.pushed !== false }
      }
      await loadChecks()
    }
  } catch (e) {
    checkError.value = '提交失败：' + e.message
  } finally {
    checkSubmitting.value = 0
  }
}

// 标定类步骤：Controller 侧标定 TPS 或步骤声明 type=calib（SR02-AR05）
const calibSteps = computed(() =>
  steps.value
    .map((st, i) => ({ st, index: i + 1 }))
    .filter(({ st }) => st?.type === 'calib' || st?.calibration === true || st?.v2?.calib === true)
)
const hasCalib = computed(() => calibSteps.value.length > 0)

async function answerManual(decision, note = '') {
  const id = currentTask.value?.id
  if (!id || manualBusy.value) return
  manualSubmitting.value = true
  manualError.value = ''
  try {
    const res = await api.decideTpsManual(id, decision, note)
    if (!res.success) manualError.value = res.message || '提交失败'
    else {
      // 提交成功后按钮置灰并显示「已提交」，等待下一次轮询关闭弹窗
      manualSubmitted.value = res.decision || decision
      manualNoteOpen.value = false
      await pollTask()
    }
  } catch (e) {
    manualError.value = '提交失败：' + e.message
  } finally {
    manualSubmitting.value = false
  }
}

// 取消本轮测试：等待确认期间与运行期间都可发起；取消后剩余测试条目跳过，收尾动作照常执行
async function cancelRun() {
  const id = currentTask.value?.id
  if (!id || manualSubmitting.value) return
  const ok = window.confirm('确定取消本轮测试？剩余测试条目将被跳过，收尾动作照常执行，本轮结论为 NOK。')
  if (!ok) return
  manualSubmitting.value = true
  manualError.value = ''
  try {
    const res = await api.cancelTpsTask(id)
    if (!res.success) manualError.value = res.message || '取消失败'
    else await pollTask()
  } catch (e) {
    manualError.value = '取消失败：' + e.message
  } finally {
    manualSubmitting.value = false
  }
}

const progress = computed(() => currentTask.value?.progress ?? 0)
const taskStatusText = computed(() => {
  const s = currentTask.value?.status
  if (!s) return '未执行'
  return { pending: '等待执行', running: '执行中', completed: '执行完成', failed: '执行失败' }[s] || s
})
const taskStatusCls = computed(() => {
  const s = currentTask.value?.status
  return { pending: 'warn', running: 'run', completed: 'ok', failed: 'err' }[s] || 'idle'
})
const finished = computed(() => {
  const s = currentTask.value?.status
  return s === 'completed' || s === 'failed'
})

function stepBadge(s) {
  const st = stepState.value[s]
  const status = st?.status || 'pending'
  const map = {
    pending: { text: '待执行', cls: 'idle' },
    running: { text: '执行中', cls: 'run' },
    passed: { text: '通过', cls: 'ok' },
    failed: { text: '失败', cls: 'err' },
    skipped: { text: '跳过', cls: 'warn' }
  }
  return map[status] || map.pending
}

// ---- 装备树 - ----
const tpsSource = ref('local')   // local | remote
const remoteError = ref('')
const uutQuery = ref('')
const uutQuerying = ref(false)
const uutQueryMsg = ref('')
const uutQueryErr = ref('')
const importingTps = ref(false)
const tpsFileInput = ref(null)
const tpsImportMsg = ref('')
const tpsImportErr = ref('')
const tpsImportHint = ref({ text: '', kind: 'ok' })   // 导入去向：Controller 全局可见 / 本机仅本机可见
const tpsImportToast = ref({ text: '', note: '', kind: 'ok' })
let tpsImportToastTimer = null
const MAX_ZIP_BYTES = 64 * 1024 * 1024   // 前端上限：后端更大，这里先拦，避免浏览器读大包卡死

async function loadTree() {
  loadingTree.value = true
  listError.value = ''
  try {
    const res = await api.getTpsList()
    tpsSource.value = res.source === 'remote' ? 'remote' : 'local'
    remoteError.value = res.remote_error || ''
    tree.value = buildTreeFromTps(res.tps || [])
    if (tree.value.length) {
      const firstTps = tree.value[0]?.duts?.[0]?.tps?.[0]
      if (firstTps) {
        expandedProjects.value.add(tree.value[0].name)
        expandedDuts.value.add(tree.value[0].duts[0].name)
        if (!selectedTpsId.value || !res.tps.some(t => t.id === selectedTpsId.value)) {
          selectedTpsId.value = firstTps.id
          await loadTpsDetail(firstTps.id)
        }
      }
    } else {
      listError.value = '未获取到 TPS 定义（远端离线且本地 testresource 为空）'
    }
  } catch (e) {
    listError.value = '获取装备树失败：' + e.message + '（请确认后端已启动）'
  } finally {
    loadingTree.value = false
  }
}

function buildTreeFromTps(tpsList) {
  const projects = new Map()
  for (const t of tpsList) {
    const pName = t.project || '未分组项目'
    const dName = t.dut || '未分组 DUT'
    if (!projects.has(pName)) projects.set(pName, new Map())
    const duts = projects.get(pName)
    if (!duts.has(dName)) duts.set(dName, [])
    duts.get(dName).push({
      id: t.id,
      name: t.name || t.id,
      description: t.description || '',
      version: t.version || ''
    })
  }
  return [...projects.entries()].map(([name, duts]) => ({
    name,
    duts: [...duts.entries()].map(([dname, tps]) => ({ name: dname, tps }))
  }))
}

function setImportHint(text, kind = 'ok') {
  tpsImportHint.value = { text, kind }
}

function hideImportToast() {
  if (tpsImportToastTimer) {
    clearTimeout(tpsImportToastTimer)
    tpsImportToastTimer = null
  }
  tpsImportToast.value = { text: '', note: '', kind: 'ok' }
}

function showImportToast(text, note = '', kind = 'ok') {
  hideImportToast()
  tpsImportToast.value = { text, note, kind }
  tpsImportToastTimer = setTimeout(hideImportToast, 7000)
}

/** 导入去向探测：Controller 可达 -> 全局可见；否则本机兜底。404 或探测失败不出提示，不影响导入 */
async function detectImportTarget() {
  try {
    const cap = await api.getTpsImportCapability()
    if (!cap || cap.success === false) {
      setImportHint('')
      return
    }
    const online = cap.online === true || cap.mode === 'controller'
    setImportHint(
      online ? '将导入到 MockController（全局可见）' : 'Controller 不可达，将导入到本机（仅本机可见）',
      online ? 'ok' : 'warn'
    )
  } catch (e) {
    setImportHint('')
  }
}

/** 失败提示优先用后端原文（message / detail），拿不到再退回 HTTP 错误文本 */
function importFailureText(err, fallback = '导入失败') {
  const payload = err && err.payload
  const detail = payload && (payload.message || payload.detail)
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail) {
    try {
      return JSON.stringify(detail)
    } catch {
      /* 无法序列化时退回 err.message */
    }
  }
  return (err && err.message) || fallback
}

/** 读 zip 为 base64（分块拼接，避免 64MB 包把 fromCharCode 参数撑爆） */
function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error || new Error('文件读取失败'))
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result || new ArrayBuffer(0))
      const chunk = 0x8000
      let bin = ''
      for (let i = 0; i < bytes.length; i += chunk) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk))
      }
      resolve(btoa(bin))
    }
    reader.readAsArrayBuffer(file)
  })
}

function formatFileSize(bytes) {
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  return Math.max(1, Math.round(bytes / 1024)) + ' KB'
}

/** 在装备树中定位新导入的 TPS：展开项目/DUT 并加载详情 */
async function focusTps(id) {
  for (const p of tree.value) {
    for (const d of p.duts) {
      if (d.tps.some((t) => t.id === id)) {
        expandedProjects.value.add(p.name)
        expandedDuts.value.add(d.name)
        selectedTpsId.value = id
        await loadTpsDetail(id)
        return
      }
    }
  }
  selectedTpsId.value = id
  await loadTpsDetail(id)
}

async function queryTpsByUut() {
  const uut = uutQuery.value.trim()
  if (!UUT_RE.test(uut)) {
    uutQueryErr.value = 'UUT 名称必须为 12 位英文与数字组合'
    uutQueryMsg.value = ''
    return
  }
  uutQueryErr.value = ''
  uutQueryMsg.value = ''
  uutQuerying.value = true
  try {
    const res = await api.getTpsByUut(uut)
    if (res.success) {
      uutQueryMsg.value = '✅ 找到 UUT ' + uut + ' 对应的 TPS：' + (res.tps.name || res.tps.id)
      // 定位到该 TPS: 展开所在项目/DUT 并选中
      for (const p of tree.value) {
        for (const d of p.duts) {
          const t = d.tps.find(t => t.id === res.tps.id)
          if (t) {
            expandedProjects.value.add(p.name)
            expandedDuts.value.add(d.name)
            selectedTpsId.value = res.tps.id
            await loadTpsDetail(res.tps.id)
            return
          }
        }
      }
      // TPS 不在当前树中(如远端返回), 直接加载详情
      selectedTpsId.value = res.tps.id
      await loadTpsDetail(res.tps.id)
    } else {
      uutQueryErr.value = res.message || '查询失败'
    }
  } catch (e) {
    uutQueryErr.value = '查询失败：' + e.message
  } finally {
    uutQuerying.value = false
  }
}

async function pickTpsFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  tpsImportMsg.value = ''
  tpsImportErr.value = ''
  tpsImportHint.value = { text: '', kind: 'ok' }
  hideImportToast()

  const name = file.name || ''
  const lower = name.toLowerCase()
  const isZip = lower.endsWith('.zip')
  if (!isZip && !lower.endsWith('.json')) {
    tpsImportErr.value = '❌ 仅支持 .json 清单或 .zip 压缩包'
    if (tpsFileInput.value) tpsFileInput.value.value = ''
    return
  }
  // 前端先拦大包：后端上限更大，但浏览器读 64MB 以上容易卡死
  if (isZip && file.size > MAX_ZIP_BYTES) {
    tpsImportErr.value = `❌ 压缩包 ${formatFileSize(file.size)} 超过 64MB 上限，请精简 testcase/ 后重试`
    if (tpsFileInput.value) tpsFileInput.value.value = ''
    return
  }

  importingTps.value = true
  try {
    await detectImportTarget()
    const res = isZip
      ? await api.importTpsZip({ name, content_base64: await readFileAsBase64(file), overwrite: true })
      : await api.importTps(await file.text(), name)
    if (!res || res.success === false) {
      const detail = res && (res.message || res.detail)
      const text = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : '导入失败'
      tpsImportErr.value = '❌ ' + text
      return
    }
    const message = res.message || 'TPS 导入成功'
    const localOnly = res.source === 'local'
    tpsImportMsg.value = '✅ ' + message
    if (localOnly) {
      showImportToast(message, '仅本机可见，联网后建议在 Controller 重新导入以共享', 'warn')
    } else {
      showImportToast(message)
    }
    await loadTree()
    if (res.tps && res.tps.id) await focusTps(res.tps.id)
  } catch (err) {
    tpsImportErr.value = '❌ 导入失败：' + importFailureText(err)
  } finally {
    importingTps.value = false
    if (tpsFileInput.value) tpsFileInput.value.value = ''
  }
}

function toggleProject(name) {
  const s = new Set(expandedProjects.value)
  s.has(name) ? s.delete(name) : s.add(name)
  expandedProjects.value = s
}
function toggleDut(name) {
  const s = new Set(expandedDuts.value)
  s.has(name) ? s.delete(name) : s.add(name)
  expandedDuts.value = s
}
function isProjExpanded(name) { return expandedProjects.value.has(name) }
function isDutExpanded(name) { return expandedDuts.value.has(name) }

async function selectTps(id) {
  if (running.value && id !== selectedTpsId.value) return
  selectedTpsId.value = id
  await loadTpsDetail(id)
}

async function loadTpsDetail(id) {
  if (!id) return
  loadingDetail.value = true
  reportUrl.value = ''
  reportLoaded.value = false
  try {
    const res = await api.getTpsDetail(id)
    tpsDetail.value = res.success ? res.tps : null
  } catch (e) {
    listError.value = '获取 TPS 详情失败：' + e.message
  } finally {
    loadingDetail.value = false
  }
}

// ---- 实时监视窗口 ----
/** 以独立窗口打开监视控件；带当前任务号，便于直接看本轮数据 */
function openMonitor() {
  const id = (currentTask.value && currentTask.value.id) || ''
  const url = id ? `/monitor?task=${encodeURIComponent(id)}` : '/monitor'
  const win = window.open(url, 'ate-monitor', 'width=1280,height=840,menubar=no,toolbar=no,location=no,status=no')
  if (win) win.focus()
}

/** 常用形态：拉起常驻置顶的监视小窗（Edge 独立窗，产线可一直看） */
async function openMonitorTop() {
  const id = (currentTask.value && currentTask.value.id) || ''
  try {
    const res = await api.hudStart({ task_id: id })
    if (res && res.success) {
      monitorHint.value = res.reused ? '置顶监视窗已在运行，已切回前台' : '已拉起常驻置顶监视窗'
    } else {
      monitorHint.value = (res && res.message) || '拉起置顶窗失败，可改用实时监视'
    }
  } catch (e) {
    monitorHint.value = `拉起置顶窗失败：${e.message}`
  }
  setTimeout(() => { monitorHint.value = '' }, 5000)
}
const monitorHint = ref('')

// ---- 执行 ----
async function startRun() {
  if (!selectedTpsId.value || running.value) return
  const uut = uutInput.value.trim()
  if (!UUT_RE.test(uut)) {
    uutError.value = 'UUT 名称必须为 12 位英文与数字组合'
    return
  }
  // 生产模式闸门: 工单号必须 12 位, 否则不予执行（后端同样会拦截）
  if (isProduction.value && !workOrderValid.value) {
    uutError.value = `生产模式必须先输入 ${WORK_ORDER_LEN} 位工单号（数字/字母）才能执行`
    return
  }
  uutError.value = ''
  cloudError.value = ''
  cloud.value = null
  running.value = true
  reportUrl.value = ''
  reportLoaded.value = false
  reportError.value = ''
  currentTask.value = {
    id: '',
    status: 'pending',
    progress: 0,
    steps: [],
    current_step: 0,
    report: null,
    uut,
    start_time: null,
    end_time: null,
    duration: null,
    logs: []
  }
  showAllLevels()
  try {
    const res = await api.runTps(selectedTpsId.value, uut, runOptions())
    if (!res.success) throw new Error(res.message || '启动失败')
    currentTask.value.id = res.task_id
    pollTimer = setInterval(pollTask, 600)
  } catch (e) {
    running.value = false
    currentTask.value = null
    listError.value = 'TPS 启动失败：' + e.message
  }
}

async function pollTask() {
  const id = currentTask.value?.id
  if (!id) return
  try {
    const res = await api.getTpsTask(id)
    if (!res.success) return
    currentTask.value = { ...currentTask.value, ...res }
    if (res.cloud) cloud.value = res.cloud
    // 步骤有新结果时同步 manual check 待确认项（接口未挂载时静默）
    await loadChecks()
    await nextTick()
    // 日志面板自动滚到底部（用户上翻查看历史时不打扰）
    if (logEl.value) {
      const el = logEl.value
      if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight
    }
    if (res.status === 'completed' || res.status === 'failed') {
      clearInterval(pollTimer)
      pollTimer = null
      running.value = false
      if (res.report) {
        reportUrl.value = '/api/reports/' + res.report.split('/').pop()
        reportLoaded.value = true
      } else {
        reportError.value = '任务结束但未生成报告文件'
      }
    }
  } catch {
    // 网络抖动: 继续轮询
  }
}

function onReportError() {
  reportError.value = '报告加载失败，请检查后端报告目录'
}

// ---- 装备软件部署 ----
const deployStatusText = (s) => ({ pending: '待部署', running: '部署中', success: '已部署', failed: '失败' }[s] || s || '—')
const deployStatusCls = (s) => ({ pending: 'wait', running: 'run', success: 'ok', failed: 'err' }[s] || '')

const deployBusy = computed(() =>
  deploying.value || deployItems.value.some((it) => ['pending', 'running'].includes(it.status))
)

async function loadInstalled() {
  try {
    const res = await api.deployInstalled()
    deployInstalled.value = res.records || []
  } catch (e) {
    /* 查询失败不影响主流程 */
  }
}

function stopDeployPoll() {
  if (deployTimer) { clearInterval(deployTimer); deployTimer = null }
}

async function refreshDeploy() {
  if (!deployBatch.value) return
  try {
    const res = await api.deployStatus(deployBatch.value)
    deployItems.value = res.items || []
    const status = res.batch ? res.batch.status : ''
    if (['success', 'failed', 'partial'].includes(status)) {
      stopDeployPoll()
      deployMsg.value = (res.batch && res.batch.message) || '部署批次已结束'
      await loadInstalled()
    }
  } catch (e) {
    deployError.value = String(e.message || e)
  }
}

async function startDeploy() {
  if (!selectedTpsId.value || deploying.value) return
  deploying.value = true; deployError.value = ''; deployMsg.value = ''
  try {
    const res = await api.deployRun(selectedTpsId.value, uutInput.value.trim())
    deployBatch.value = res.batch_id
    deployItems.value = (res.items || []).map((it, i) => ({ seq: i + 1, ...it, status: 'pending', message: '' }))
    deployMsg.value = `已提交部署批次 ${res.batch_id}`
    stopDeployPoll()
    deployTimer = setInterval(refreshDeploy, 1500)
  } catch (e) {
    deployError.value = String(e.message || e)
  } finally {
    deploying.value = false
  }
}

async function restoreDeploy() {
  try {
    const res = await api.deployRecords(3)
    const batches = res.batches || []
    if (!batches.length) return
    const running = batches.find((b) => b.status === 'running') || batches[0]
    deployBatch.value = running.batch_id
    deployItems.value = (running.items || []).map((it) => ({ ...it }))
    if (running.status === 'running') {
      deployMsg.value = `批次 ${running.batch_id} 执行中`
      stopDeployPoll()
      deployTimer = setInterval(refreshDeploy, 1500)
    }
  } catch (e) {
    /* 记录查询失败不阻断页面 */
  }
}

// ---- 生命周期 ----
onMounted(async () => {
  await loadTree()
  await loadCloudStatus()
  await loadCloudRecords()
  await restoreDeploy()
  await loadInstalled()
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  stopDeployPoll()
  hideImportToast()
})
</script>

<template>
  <div class="tps-wrap">
    <!-- 左列 -->
    <div class="left-col">
      <!-- 左上: 装备树 -->
      <section class="tree-panel" data-testid="equipment-tree">
        <div class="panel-head">
          <h2 class="panel-title">装备树</h2>
          <div class="head-actions">
            <span class="src-badge" :class="tpsSource">
              {{ tpsSource === 'remote' ? '● 在线' : '● 离线' }}
            </span>
            <button class="btn ghost small" :disabled="loadingTree || running" @click="loadTree">刷新</button>
          </div>
        </div>

        <!-- ===== 装备树上方: 运行档位区 ===== -->
        <!-- 生产模式: 工单号闸门（12 位才能执行, 结果上报云端） -->
        <div v-if="isProduction" class="wo-box">
          <div class="wo-head">
            <span class="wo-tag">🏭 生产模式</span>
            <span class="wo-note">需 {{ WORK_ORDER_LEN }} 位工单号</span>
          </div>
          <div class="wo-row">
            <input
              v-model="workOrder"
              class="input mono"
              :placeholder="`请输入 ${WORK_ORDER_LEN} 位工单号`"
              :maxlength="WORK_ORDER_LEN"
              :disabled="running"
              @keyup.enter="startRun"
            />
            <span class="wo-count" :class="{ ok: workOrderValid }">{{ workOrder.length }}/{{ WORK_ORDER_LEN }}</span>
          </div>
          <div class="wo-hint" :class="workOrderValid ? 'ok' : 'warn'">
            <template v-if="workOrderValid">✓ 工单号有效 · 设备连接：真机 · 结果上报云端数据库</template>
            <template v-else>○ 输入 {{ WORK_ORDER_LEN }} 位数字或字母工单号后才能执行 TPS</template>
          </div>
          <button class="btn ghost small block" @click="setRunMode('debug')">切换到调试模式</button>
        </div>

        <!-- 调试模式: 免工单 + 仿真模式开关（不连接真实设备） -->
        <div v-else class="dbg-box">
          <div class="dbg-head">
            <span class="dbg-tag">🛠 调试模式</span>
            <span class="dbg-note">免工单</span>
          </div>
          <button
            class="sim-btn"
            :class="{ on: simulate }"
            :disabled="running || simulateBusy"
            @click="toggleSimulate"
          >
            <span class="sim-dot"></span>
            <span class="sim-label">仿真模式</span>
            <span class="sim-state">{{ simulate ? '已开启 · 不连接真实设备' : '已关闭 · 将连接真实设备' }}</span>
          </button>
          <div class="dbg-hint">
            调试模式不校验工单号，数据只落本地记录库（不上传云端）；当前设备连接：{{ deviceModeText }}。
          </div>
          <button class="btn ghost small block" @click="setRunMode('production')">切换到生产模式</button>
        </div>

        <!-- UUT 查询 TPS -->
        <div class="uut-query-row">
          <input
            v-model="uutQuery"
            class="input mono"
            placeholder="输入 UUT 查询对应 TPS"
            maxlength="12"
            :disabled="uutQuerying"
            @keyup.enter="queryTpsByUut"
          />
          <button class="btn primary small" :disabled="uutQuerying" @click="queryTpsByUut">
            {{ uutQuerying ? '查询中…' : '查询' }}
          </button>
        </div>
        <div v-if="uutQueryMsg" class="tip ok">{{ uutQueryMsg }}</div>
        <div v-if="uutQueryErr" class="tip err">{{ uutQueryErr }}</div>

        <!-- 远端离线提示 -->
        <div v-if="tpsSource === 'local' && remoteError" class="tip warn" title="{{ remoteError }}">
          ⚠ 远端获取失败，已保持默认 TPS
        </div>

        <p v-if="loadingTree" class="panel-tip">正在加载装备树…</p>
        <div v-else-if="listError && !tree.length" class="panel-tip err">{{ listError }}</div>
        <div v-else class="tree">
          <!-- 项目 -->
          <div v-for="p in tree" :key="p.name" class="tree-node proj">
            <div class="tree-row" :class="{ open: isProjExpanded(p.name) }" @click="toggleProject(p.name)">
              <span class="twist">{{ isProjExpanded(p.name) ? '▾' : '▸' }}</span>
              <span class="node-icon proj-icon">📁</span>
              <span class="node-name">{{ p.name }}</span>
              <span class="node-count">{{ p.duts.length }}</span>
            </div>
            <div v-if="isProjExpanded(p.name)" class="tree-children">
              <!-- DUT -->
              <div v-for="d in p.duts" :key="d.name" class="tree-node dut">
                <div class="tree-row" :class="{ open: isDutExpanded(d.name) }" @click="toggleDut(d.name)">
                  <span class="twist">{{ isDutExpanded(d.name) ? '▾' : '▸' }}</span>
                  <span class="node-icon dut-icon">🔧</span>
                  <span class="node-name">{{ d.name }}</span>
                  <span class="node-count">{{ d.tps.length }}</span>
                </div>
                <div v-if="isDutExpanded(d.name)" class="tree-children">
                  <!-- TPS -->
                  <div
                    v-for="t in d.tps"
                    :key="t.id"
                    class="tree-node tps"
                    :class="{ active: t.id === selectedTpsId }"
                    @click="selectTps(t.id)"
                  >
                    <div class="tree-row">
                      <span class="twist dim">▪</span>
                      <span class="node-icon tps-icon">🧪</span>
                      <span
                        v-if="t.has_constants"
                        class="mc-flag"
                        :title="`具备常数标定（${t.constant_model_count} 个模型）`"
                      >C</span>
                      <span class="node-name">{{ t.name }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 导入 TPS -->
        <div class="import-row">
          <button
            class="btn ghost small block"
            :disabled="importingTps"
            title="支持 .json 清单与 .zip 压缩包（tps.json + testcase/）"
            @click="tpsFileInput?.click()"
          >
            {{ importingTps ? '导入中…' : '📂 导入 TPS' }}
          </button>
          <input ref="tpsFileInput" type="file" accept=".json,.zip" class="hidden-input" data-testid="tps-file-input" @change="pickTpsFile" />
          <div v-if="tpsImportHint.text" class="tip" :class="tpsImportHint.kind">{{ tpsImportHint.text }}</div>
          <div v-if="tpsImportMsg" class="tip ok">{{ tpsImportMsg }}</div>
          <div v-if="tpsImportErr" class="tip err">{{ tpsImportErr }}</div>
        </div>
      </section>
    </div>

    <!-- 右列 -->
    <div class="right-col">
      <!-- 右上: 测试环境 -->
      <section class="env-panel">
        <div class="panel-head">
          <h2 class="panel-title">测试运行环境</h2>
          <div v-if="selectedTps" class="tree-path">
            {{ selectedTps ? selectedTps.name : '' }}
          </div>
        </div>
        <p v-if="!selectedTps" class="panel-tip">请在左侧装备树选择 TPS</p>
        <p v-else-if="loadingDetail" class="panel-tip">正在加载环境信息…</p>
        <div v-else-if="Object.keys(environment).length" class="env-grid">
          <div class="env-item" v-for="(v, k) in environment" :key="k">
            <div class="env-key">{{ k }}</div>
            <div class="env-val">{{ v }}</div>
          </div>
        </div>
      </section>

      <!-- 右中: 步骤列表 -->
      <section class="steps-panel">
        <div class="panel-head">
          <h2 class="panel-title">测试步骤（{{ steps.length }}）</h2>
          <button class="btn ghost small monitor-btn" title="打开独立窗口查看用例实时数据" @click="openMonitor">
            📈 实时监视
          </button>
          <button class="btn ghost small monitor-btn" title="拉起常驻置顶的小监视窗（Ctrl+Alt+T 切换置顶）" @click="openMonitorTop">
            📌 常驻置顶
          </button>
          <span v-if="monitorHint" class="monitor-hint">{{ monitorHint }}</span>
        </div>

        <div class="uut-row">
          <label class="uut-label">UUT 名称</label>
          <input
            v-model="uutInput"
            class="input mono"
            placeholder="12 位英文与数字，如 ABC123DEF456"
            maxlength="12"
            :disabled="running"
          />
          <button
            class="btn primary"
            :disabled="running || !steps.length || (isProduction && !workOrderValid)"
            :title="isProduction && !workOrderValid ? `生产模式需先输入 ${WORK_ORDER_LEN} 位工单号` : ''"
            @click="startRun"
          >
            {{ running ? '执行中…' : `执行 TPS（${RUN_MODE_META[runMode].short}）` }}
          </button>
        </div>
        <div v-if="uutError" class="tip err">{{ uutError }}</div>
        <div v-if="cloudError" class="tip err">☁ {{ cloudError }}</div>
        <div class="deploy-row">
          <button class="btn ghost small" :disabled="!selectedTps || deployBusy || running" @click="startDeploy">
            {{ deploying ? '部署中…' : '📦 部署软件' }}
          </button>
          <button class="btn ghost small" :disabled="!deployBatch" @click="refreshDeploy">部署状态</button>
          <button
            class="btn ghost small"
            data-testid="deploy-list-toggle"
            :aria-pressed="deployListHidden"
            :title="deployListHidden ? '展开待部署与已部署软件列表' : '收起部署清单，仅保留部署按钮'"
            @click="toggleDeployList"
          >
            {{ deployListHidden ? `显示清单${deployListCount ? `（${deployListCount}）` : ''}` : '隐藏清单' }}
          </button>
          <span v-if="deployMsg" class="tip ok">{{ deployMsg }}</span>
          <span v-if="deployError" class="tip err">{{ deployError }}</span>
          <span v-if="deployBusy" class="tip">同一时间只执行一个部署批次</span>
        </div>
        <div v-if="!deployListHidden && deployItems.length" class="deploy-panel" data-testid="deploy-pending-panel">
          <div class="deploy-head">待部署软件（{{ deployItems.length }}）</div>
          <div v-for="it in deployItems" :key="it.seq" class="deploy-item">
            <span class="deploy-name">{{ it.name || it.object }}<span v-if="it.version" class="deploy-ver"> · {{ it.version }}</span></span>
            <span class="deploy-badge" :class="deployStatusCls(it.status)">{{ deployStatusText(it.status) }}</span>
            <span class="deploy-msg">{{ it.message }}</span>
            <span v-if="it.installed_version" class="deploy-badge ok">已装 {{ it.installed_version }}</span>
          </div>
        </div>
        <div v-if="!deployListHidden && deployInstalled.length" class="deploy-panel" data-testid="deploy-installed-panel">
          <div class="deploy-head">已部署软件（{{ deployInstalled.length }}）</div>
          <div v-for="r in deployInstalled" :key="r.target_id + r.object" class="deploy-item">
            <span class="deploy-name">{{ r.object }}<span class="deploy-ver"> · v{{ r.version }}</span></span>
            <span class="deploy-badge ok">{{ r.target_id === 'local' ? '本机' : r.target_id }}</span>
            <span class="deploy-msg">{{ r.updated_at }}</span>
          </div>
        </div>

        <div v-if="currentTask" class="task-status-row">
          <span class="status-label">任务状态</span>
          <span class="badge" :class="taskStatusCls">{{ taskStatusText }}</span>
          <span class="task-id">任务 {{ currentTask.id }}</span>
          <span v-if="currentTask.duration != null" class="task-id">耗时 {{ currentTask.duration.toFixed(2) }}s</span>
          <span v-if="isProduction && workOrderValid" class="task-id">工单 {{ normalizedWorkOrder }}</span>
          <span v-if="isProduction" class="cloud-chip" :class="cloud ? (cloud.ok ? 'ok' : 'err') : 'idle'">
            <template v-if="!cloud">☁ 云端待上报</template>
            <template v-else-if="cloud.ok">☁ 已上传云端 #{{ cloud.cloud_id }}</template>
            <template v-else>☁ 云端上报失败（待重传）</template>
            <button v-if="cloud && !cloud.ok" class="cloud-retry" :disabled="cloudReuploading" @click="reuploadCloud">
              {{ cloudReuploading ? '重传中…' : '重传' }}
            </button>
          </span>
          <button
            v-if="running"
            class="btn ghost small"
            :disabled="manualSubmitting"
            @click="cancelRun()"
          >取消运行</button>
        </div>

        <div class="progress-track">
          <div class="progress-fill" :class="taskStatusCls" :style="{ width: progress + '%' }"></div>
        </div>
        <div class="progress-meta">
          <span>总进度 {{ progress }}%</span>
          <span v-if="currentTask?.current_step">当前第 {{ currentTask.current_step }} / {{ steps.length }} 步</span>
        </div>

        <div v-if="stepHoldOpen" class="step-hold-banner" data-testid="step-hold-banner">
          <span class="step-hold-tag">单步暂停</span>
          <span class="step-hold-text">
            第 {{ currentTask.current_step }} 条「{{ manual.name }}」已执行完毕，点「下一步」放行后续用例。
          </span>
          <button class="btn ghost" :disabled="manualSubmitting" @click="answerManual('cancel')">取消本轮测试</button>
          <button class="btn ghost" :disabled="manualSubmitting" @click="answerManual('abort')">中止测试</button>
          <button class="btn primary step-next" :disabled="manualSubmitting" @click="answerManual('next')">
            下一步 next
          </button>
        </div>
        <div v-if="stepHoldOpen && manualError" class="tip err">{{ manualError }}</div>

        <!-- 标定模式条：本次执行含标定步骤时置顶提示（SR02-AR05） -->
        <div v-if="hasCalib" class="calib-bar" data-testid="calib-bar">
          <span class="calib-tag">标定</span>
          <span class="calib-text">本次执行含 {{ calibSteps.length }} 个标定步骤，结果为标定常数，不参与合格判定</span>
        </div>
        <div v-if="checkError" class="tip err">{{ checkError }}</div>

        <div v-if="loadingDetail && !steps.length" class="panel-tip">正在加载步骤…</div>
        <div v-else-if="!steps.length" class="panel-tip">该 TPS 没有定义步骤</div>
        <ol v-else class="step-list">
          <li v-for="(st, i) in steps" :key="i" class="step-item" :class="stepBadge(i + 1).cls">
            <span class="step-index">{{ i + 1 }}</span>
            <div class="step-body">
              <div class="step-name-row">
                <span class="step-name">{{ st.name }}</span>
                <span class="badge" :class="stepBadge(i + 1).cls">{{ stepBadge(i + 1).text }}</span>
              </div>
              <div class="step-desc">{{ st.description }}</div>
              <div class="step-meta">
                <span class="step-type" :class="{ 'step-type-manual': st.manual }">{{ st.manual ? '人工操作' : ({ init: '环境初始化', test: '测试用例', teardown: '环境终止' }[st.type] || st.type) }}</span>
                <span v-if="st.v2?.step" class="step-type step-type-step">单步暂停点</span>
                <span
                  v-if="st.type === 'calib' || st.calibration === true || st.v2?.calib === true"
                  class="step-type step-type-calib"
                  data-testid="step-calib-badge"
                >标定</span>
                <span v-if="stepState[i + 1]?.duration != null" class="step-dur">
                  耗时 {{ stepState[i + 1].duration.toFixed(2) }}s
                </span>
              </div>
              <pre v-if="stepState[i + 1]?.detail" class="step-detail">{{ stepState[i + 1].detail }}</pre>
              <!-- manual check：该步执行完后由人工确认，确认后机器常数与测角结果才回传 MES -->
              <div
                v-if="stepNeedsCheck(st) && stepState[i + 1] && stepState[i + 1].status !== 'running'"
                class="check-row"
                data-testid="manual-check-row"
              >
                <template v-if="checkState[i + 1]?.decision === 'confirm'">
                  <span class="check-done">✓ 已人工确认并回传</span>
                </template>
                <template v-else-if="checkState[i + 1]?.decision === 'reject'">
                  <span class="check-rejected">✗ 已驳回：{{ checkState[i + 1].note }}</span>
                  <button class="btn ghost small" :disabled="checkSubmitting === i + 1" @click="submitCheck(i + 1, 'confirm')">
                    改为确认回传
                  </button>
                </template>
                <template v-else>
                  <span class="check-hint">本步结果待人工确认，确认后才回传 MES</span>
                  <button
                    class="btn primary small"
                    data-testid="manual-check-confirm"
                    :disabled="checkSubmitting === i + 1"
                    @click="submitCheck(i + 1, 'confirm')"
                  >确认回传</button>
                  <button
                    class="btn ghost small"
                    :disabled="checkSubmitting === i + 1"
                    @click="submitCheck(i + 1, 'reject')"
                  >驳回</button>
                </template>
              </div>
              <div v-if="stepHoldOpen && manual.id && st.v2?.id === manual.id" class="step-hold-row">
                <span class="step-hold-hint">本条已执行完毕，等待放行</span>
                <button class="btn primary step-next" :disabled="manualSubmitting" @click="answerManual('next')">
                  下一步 next
                </button>
                <button class="btn ghost" :disabled="manualSubmitting" @click="answerManual('abort')">中止测试</button>
              </div>
            </div>
          </li>
        </ol>

        <div
          v-if="finished"
          class="summary-line"
          :class="currentTask.status"
        >
          {{ currentTask.status === 'completed' ? '✓ 全部步骤执行完成' : '✗ 执行中止：存在失败步骤，已生成报告' }}
        </div>
      </section>

      <!-- 右下: 执行日志（按等级 [ERROR]/[WARNING]/[INFO] 筛选） -->
      <section class="log-panel">
        <div class="panel-head">
          <h2 class="panel-title">
            执行日志
            <span class="log-count">（{{ filteredLogs.length }} / {{ taskLogs.length }} 条）</span>
          </h2>
          <div class="log-filters">
            <button
              class="log-chip error"
              data-testid="log-count-error"
              :class="{ off: !levelOn.error }"
              title="显示 / 隐藏 ERROR 级别日志"
              @click="toggleLevel('error')"
            >ERROR {{ logCounts.error }}</button>
            <button
              class="log-chip warning"
              data-testid="log-count-warning"
              :class="{ off: !levelOn.warning }"
              title="显示 / 隐藏 WARNING 级别日志"
              @click="toggleLevel('warning')"
            >WARNING {{ logCounts.warning }}</button>
            <button
              class="log-chip info"
              data-testid="log-count-info"
              :class="{ off: !levelOn.info }"
              title="显示 / 隐藏 INFO 级别日志"
              @click="toggleLevel('info')"
            >INFO {{ logCounts.info }}</button>
            <button
              class="log-chip plain"
              :class="{ active: onlyErrorMode }"
              title="只看报错 / 恢复全部"
              @click="onlyErrors"
            >{{ onlyErrorMode ? '显示全部' : '只看报错' }}</button>
          </div>
        </div>
        <div ref="logEl" class="log-body">
          <p v-if="!taskLogs.length" class="panel-tip">
            执行 TPS 后在此查看分级日志：每条日志以 [ERROR] / [WARNING] / [INFO] 开头，可按等级筛选
          </p>
          <p v-else-if="!filteredLogs.length" class="panel-tip">当前筛选条件下没有日志</p>
          <div
            v-for="(l, i) in filteredLogs"
            :key="i"
            class="log-line"
            :class="'lv-' + (l.level || 'info')"
          >
            <span class="log-badge">[{{ (l.level || 'info').toUpperCase() }}]</span>
            <span class="log-text">{{ l.text }}</span>
          </div>
        </div>
      </section>

      <!-- 右下: 测试报告 -->
      <section class="report-panel" data-testid="report-panel">
        <div class="panel-head">
          <h2 class="panel-title">测试报告</h2>
          <button class="btn ghost small" :disabled="!currentTask" @click="analyzeWithAi">
            {{ aiAnalyzing ? '分析中…' : '🤖 AI 分析' }}
          </button>
        </div>

        <!-- AI 配置区 -->
        <div class="ai-config">
          <button class="ai-config-toggle" @click="toggleAiConfig">
            {{ aiConfigOpen ? '▾ 收起 AI 配置' : '▸ AI 配置（API 地址 / Key）' }}
          </button>
          <div v-if="aiConfigOpen" class="ai-config-body">
            <label class="ai-field">
              <span class="ai-label">API 地址</span>
              <input v-model="aiApiUrl" class="input mono" placeholder="https://api.openai.com/v1" />
            </label>
            <label class="ai-field">
              <span class="ai-label">API Key</span>
              <input v-model="aiApiKey" class="input mono" type="password" placeholder="sk-..." />
            </label>
            <div class="ai-config-actions">
              <button class="btn primary small" @click="saveAiConfig">保存配置</button>
              <span v-if="aiConfigMsg" class="tip ok inline">{{ aiConfigMsg }}</span>
            </div>
            <p class="ai-hint">后端将以 OpenAI 兼容 /chat/completions 调用；需联网，Key 仅保存在本机浏览器。</p>
          </div>
        </div>

        <div v-if="aiError" class="tip err ai-err">❌ {{ aiError }}</div>
        <div v-if="aiAnalyzing" class="ai-progress">
          <span class="spinner"></span> AI 正在分析测试日志与报告…
        </div>
        <div v-if="aiAnalysis" class="ai-result">
          <div class="ai-result-title">🤖 AI 失败分析</div>
          <pre class="ai-result-body">{{ aiAnalysis }}</pre>
        </div>

        <div v-if="reportError" class="panel-tip err">{{ reportError }}</div>
        <div v-else-if="!reportLoaded && !reportUrl" class="panel-tip">
          {{ currentTask ? '任务结束后将在此显示生成的 HTML 测试报告' : '执行 TPS 后在此查看测试报告；历史报告请在页眉「测试记录」页查询' }}
        </div>
        <iframe
          v-else-if="reportUrl"
          :src="reportUrl"
          class="report-frame"
          title="测试报告"
          @error="onReportError"
        ></iframe>

        <!-- 云端数据库（演示）: 生产模式上报结果一览 -->
        <div class="cloud-panel">
          <div class="cloud-panel-head">
            <span class="cloud-panel-title">☁ 云端数据库（演示）</span>
            <span class="cloud-panel-state" :class="cloudStatus && cloudStatus.online ? 'ok' : 'err'">
              {{ cloudStatus ? (cloudStatus.online ? '● 在线' : '● 离线') : '● 未知' }}
            </span>
            <button class="btn ghost small" @click="loadCloudRecords">刷新记录</button>
          </div>
          <div class="cloud-panel-meta">
            接口：{{ (cloudStatus && cloudStatus.endpoint) || '—' }}<br />
            云端库：{{ (cloudStatus && cloudStatus.db_path) || '—' }} · 已入库 {{ (cloudStatus && cloudStatus.record_count) ?? 0 }} 条
          </div>
          <div v-if="cloudRecords.length" class="cloud-records">
            <div v-for="r in cloudRecords" :key="r.cloud_id" class="cloud-rec">
              <span class="cr-wo">{{ r.work_order || '—' }}</span>
              <span class="cr-uut">{{ r.uut }}</span>
              <span class="cr-result" :class="r.result === 'OK' ? 'ok' : 'err'">{{ r.result }}</span>
              <span class="cr-time">{{ r.uploaded_at }}</span>
            </div>
          </div>
          <div v-else class="cloud-empty">云端暂无记录（生产模式跑完一轮 TPS 后自动入库）</div>
        </div>
      </section>
    </div>

    <!-- 人工操作环节弹窗：内容取自 TPS 包 resource/ 下清单里标注的 Markdown -->
    <!-- 单步执行（step=true）不弹遮罩，改由步骤列表上方的横幅与暂停行内的「下一步」按钮放行 -->
    <div v-if="manualIsManual" class="manual-mask">
      <div class="manual-card" data-testid="manual-card">
        <div class="manual-head">
          <span class="manual-tag">人工确认</span>
          <h3 class="manual-title">{{ manual.name }}</h3>
          <span v-if="manual.resource" class="manual-res">resource/{{ manual.resource }}</span>
        </div>
        <div class="manual-body" v-html="manualHtml"></div>
        <div v-if="manualNoteOpen" class="manual-note">
          <label class="manual-note-label" for="manual-fail-note">判定失败备注（写入该步结果与测试报告，可留空）</label>
          <textarea
            id="manual-fail-note"
            v-model="manualNote"
            rows="3"
            maxlength="500"
            placeholder="例如：外观有划痕，判定本环节不通过"
          ></textarea>
          <div class="manual-note-actions">
            <button class="btn ghost" :disabled="manualSubmitting" @click="manualNoteOpen = false">返回</button>
            <button class="btn fail" :disabled="manualBusy" @click="answerManual('fail', manualNote)">
              {{ manualSubmitted === 'fail' ? '已提交' : '确认判定失败' }}
            </button>
          </div>
        </div>
        <div v-if="manualError" class="tip err manual-err">{{ manualError }}</div>
        <div class="manual-foot">
          <span class="manual-hint">
            「判定通过」该环节记通过并执行下一条；「判定失败」记失败（备注可选），后续用例跳过、直接进入收尾与报告。
            <template v-if="manual.timeout_s">等待超时设为 {{ manual.timeout_s }} 秒，超时按中止处理。「中止」结束本轮；「取消」主动放弃本轮（如装夹错误需重装）。</template>
          </span>
          <div class="manual-actions">
            <button class="btn ghost" :disabled="manualBusy" @click="answerManual('cancel')">{{ manualSubmitted === 'cancel' ? '已提交' : '取消本轮测试' }}</button>
            <button class="btn ghost" :disabled="manualBusy" @click="answerManual('abort')">{{ manualSubmitted === 'abort' ? '已提交' : '中止测试' }}</button>
            <button class="btn fail" :disabled="manualBusy" @click="manualNoteOpen = true; manualError = ''">{{ manualSubmitted === 'fail' ? '已提交' : '判定失败' }}</button>
            <button class="btn primary" :disabled="manualBusy" @click="answerManual('pass')">{{ manualSubmitted === 'pass' ? '已提交' : '判定通过' }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 导入结果提示：message 原文 + 本机导入时的回流建议 -->
    <div v-if="tpsImportToast.text" class="toast" :class="tpsImportToast.kind">
      <div>{{ tpsImportToast.text }}</div>
      <div v-if="tpsImportToast.note" class="toast-note">{{ tpsImportToast.note }}</div>
    </div>
  </div>
</template>

<style scoped>
.tps-wrap {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(280px, 3fr) minmax(480px, 7fr);
  gap: 16px;
  padding: 20px 24px;
  overflow: auto;
  align-content: start;
}

.left-col, .right-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.tree-panel, .env-panel, .steps-panel, .report-panel, .records-panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
}
.records-panel { max-height: 380px; display: flex; flex-direction: column; }
.record-list { overflow: auto; }

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 10px;
  flex-wrap: wrap;
}
.panel-title { margin: 0; font-size: 15px; }
.btn.small { padding: 4px 10px; font-size: 12px; border-radius: 6px; }
.panel-tip { color: var(--muted); text-align: center; padding: 24px 0; }
.panel-tip.err { color: var(--err); }
.tip { color: var(--muted); font-size: 12px; padding: 8px 0; }
.tip.err { color: var(--err); }
.tip.ok { color: var(--ok); }
.tip.warn { color: var(--warn); }
.toast {
  position: fixed; right: 22px; bottom: 22px; z-index: 80; max-width: 420px;
  background: #fff; border: 1px solid rgba(var(--accent-rgb), 0.4); border-left: 4px solid var(--accent);
  border-radius: 10px; padding: 11px 14px; font-size: 13px; box-shadow: 0 12px 30px rgba(29, 23, 51, 0.16);
}
.toast.warn { border-color: rgba(180, 83, 9, 0.45); border-left-color: var(--warn); color: var(--warn); }
.toast-note { margin-top: 6px; font-size: 12px; opacity: 0.9; }
.head-actions { display: flex; align-items: center; gap: 8px; }
.src-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.src-badge.remote { color: var(--ok); background: rgba(21, 128, 61, 0.1); border: 1px solid rgba(21, 128, 61, 0.35); }
.src-badge.local { color: var(--warn); background: rgba(180, 83, 9, 0.1); border: 1px solid rgba(180, 83, 9, 0.35); }
.uut-query-row { display: flex; gap: 6px; margin-bottom: 6px; }
.uut-query-row .input { flex: 1; min-width: 0; font-size: 12px; padding: 7px 10px; }
.btn.small { padding: 4px 10px; font-size: 12px; border-radius: 6px; }
.btn.block { width: 100%; justify-content: center; }
.import-row { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--border); }
.hidden-input { display: none; }
.tree-path { color: var(--muted); font-size: 12px; font-family: var(--mono); }

/* 装备树 */
.tree { display: flex; flex-direction: column; gap: 2px; }
.tree-node { user-select: none; }
.tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.15s;
}
.tree-row:hover { background: var(--panel-2); }
.tree-children { margin-left: 16px; }
.twist { width: 14px; flex: none; color: var(--muted); font-size: 11px; }
.twist.dim { color: transparent; }
.node-icon { flex: none; font-size: 13px; }
.node-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node-count {
  flex: none;
  font-size: 11px;
  color: var(--muted);
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0 7px;
  font-family: var(--mono);
}
.tree-node.proj > .tree-row { font-weight: 700; }
.tree-node.dut > .tree-row { font-weight: 600; }
.tree-node.tps > .tree-row { color: var(--muted); }
.tree-node.tps.active > .tree-row {
  color: var(--accent);
  background: rgba(var(--accent-rgb), 0.1);
  border: 1px solid rgba(var(--accent-rgb), 0.3);
}

/* 记录查询 */
.query-row { display: flex; gap: 8px; margin-bottom: 8px; }
.query-row .input { flex: 1; min-width: 0; }
.record-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.record-item {
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--panel-2);
  cursor: pointer;
  transition: border-color 0.15s;
}
.record-item:hover { border-color: var(--accent-2); }
.record-item.active { border-color: var(--accent); }
.rec-row1 { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rec-uut { font-family: var(--mono); font-weight: 700; font-size: 13px; }
.rec-row2 { color: var(--muted); font-size: 12px; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rec-row3 {
  display: flex;
  justify-content: space-between;
  color: var(--muted);
  font-size: 11px;
  font-family: var(--mono);
  margin-top: 4px;
}

/* UUT 输入 */
.uut-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.uut-label { color: var(--muted); font-size: 13px; }
.uut-row .input { flex: 1; min-width: 160px; }

/* 输入框 */
.input {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  padding: 8px 12px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s;
}
.input:focus { border-color: var(--accent); }
.input:disabled { opacity: 0.6; }
.input.mono { font-family: var(--mono); letter-spacing: 0.5px; }

/* 环境信息 */
.env-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}
.env-item {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
}
.env-key { color: var(--muted); font-size: 11px; margin-bottom: 4px; }
.env-val { font-size: 13px; font-weight: 600; word-break: break-all; }

/* 任务状态 */
.task-status-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.status-label { color: var(--muted); font-size: 13px; }
.task-id { color: var(--muted); font-size: 12px; font-family: var(--mono); }
.badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  background: var(--panel-2);
  border: 1px solid var(--border);
  color: var(--muted);
  white-space: nowrap;
}
.badge.ok { color: var(--ok); border-color: rgba(21, 128, 61, 0.4); background: rgba(21, 128, 61, 0.1); }
.badge.err { color: var(--err); border-color: rgba(220, 38, 38, 0.4); background: rgba(220, 38, 38, 0.1); }
.badge.run {
  color: var(--accent-2);
  border-color: rgba(var(--accent-2-rgb), 0.4);
  background: rgba(var(--accent-2-rgb), 0.1);
  animation: pulse 1.2s infinite;
}
.badge.warn { color: var(--warn); border-color: rgba(180, 83, 9, 0.4); background: rgba(180, 83, 9, 0.1); }
.badge.idle { background: var(--panel-2); border: 1px solid var(--border); color: var(--muted); }
@keyframes pulse { 50% { opacity: 0.55; } }

.progress-track { height: 8px; border-radius: 999px; background: var(--panel-2); overflow: hidden; }
.progress-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s;
}
.progress-fill.run { background: linear-gradient(90deg, #6d28d9, #8b5cf6); }
.progress-fill.ok { background: var(--ok); }
.progress-fill.err { background: var(--err); }
.progress-fill.idle { background: var(--muted); }
.progress-meta {
  display: flex;
  justify-content: space-between;
  color: var(--muted);
  font-size: 12px;
  margin: 6px 0 12px;
  gap: 8px;
  flex-wrap: wrap;
}

/* 步骤列表 */
.step-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow: auto;
}
.step-item {
  display: flex;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--panel-2);
  align-items: flex-start;
}
.step-item.run { border-color: rgba(var(--accent-2-rgb), 0.6); }
.step-item.passed { border-color: rgba(21, 128, 61, 0.5); }
.step-item.failed { border-color: rgba(220, 38, 38, 0.6); }
.step-index {
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 50%;
  background: var(--panel);
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
}
.step-item.passed .step-index { color: var(--ok); border-color: rgba(21, 128, 61, 0.5); }
.step-item.failed .step-index { color: var(--err); border-color: rgba(220, 38, 38, 0.6); }
.step-item.run .step-index { color: var(--accent-2); border-color: rgba(var(--accent-2-rgb), 0.6); animation: pulse 1.2s infinite; }
.step-body { flex: 1; min-width: 0; }
.step-name-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.step-name { font-weight: 600; font-size: 13px; }
.step-desc { color: var(--muted); font-size: 12px; margin-top: 2px; }
.step-meta {
  display: flex;
  gap: 12px;
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
  font-family: var(--mono);
}
.step-detail {
  margin: 8px 0 0;
  background: #f7f5ff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.5;
  color: #4b4468;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow: auto;
}

.summary-line {
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
}
.summary-line.completed { background: rgba(21, 128, 61, 0.1); color: var(--ok); border: 1px solid rgba(21, 128, 61, 0.3); }
.summary-line.failed { background: rgba(220, 38, 38, 0.1); color: var(--err); border: 1px solid rgba(220, 38, 38, 0.3); }

/* 报告 */
/* AI 分析 */
.ai-config { margin-bottom: 10px; }
.ai-config-toggle {
  width: 100%;
  text-align: left;
  background: transparent;
  border: 1px dashed var(--border);
  color: var(--muted);
  font-size: 12px;
  padding: 6px 10px;
  border-radius: 8px;
  cursor: pointer;
}
.ai-config-toggle:hover { color: var(--accent); border-color: var(--accent); }
.ai-config-body { padding: 10px; background: rgba(109, 40, 217, 0.04); border-radius: 8px; margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.ai-field { display: flex; align-items: center; gap: 8px; }
.ai-label { width: 72px; font-size: 12px; color: var(--muted); flex-shrink: 0; }
.ai-field .input { flex: 1; min-width: 0; font-size: 12px; padding: 6px 10px; }
.ai-config-actions { display: flex; align-items: center; gap: 10px; }
.ai-hint { font-size: 11px; color: var(--muted); margin: 0; }
.ai-err { margin: 6px 0; }
.ai-progress { display: flex; align-items: center; gap: 8px; color: var(--accent); font-size: 13px; padding: 8px 0; }
.ai-result { margin: 8px 0 12px; border: 1px solid rgba(var(--accent-2-rgb), 0.3); border-radius: 10px; overflow: hidden; }
.ai-result-title { background: rgba(var(--accent-2-rgb), 0.12); color: var(--accent); font-weight: 600; font-size: 13px; padding: 8px 12px; }
.ai-result-body {
  margin: 0;
  padding: 12px;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--mono, ui-monospace, 'Cascadia Code', Consolas, monospace);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text);
  background: rgba(var(--accent-rgb), 0.05);
}
.spinner {
  width: 14px; height: 14px;
  border: 2px solid rgba(var(--accent-2-rgb), 0.3);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }
.tip.inline { padding: 0; }
.report-frame {
  width: 100%;
  height: 400px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #f4f1fd;
}

/* ---- 执行日志（按等级筛选） ---- */
.log-panel { display: flex; flex-direction: column; }
.log-count { color: var(--muted); font-size: 12px; font-weight: 400; }
.log-filters { display: flex; gap: 6px; flex-wrap: wrap; }
.log-chip {
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: var(--muted);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--mono);
  cursor: pointer;
  transition: opacity 0.15s, border-color 0.15s;
}
.log-chip:hover { border-color: var(--accent); }
.log-chip.error { color: var(--err); border-color: rgba(220, 38, 38, 0.45); background: rgba(220, 38, 38, 0.08); }
.log-chip.warning { color: var(--warn); border-color: rgba(180, 83, 9, 0.45); background: rgba(180, 83, 9, 0.08); }
.log-chip.info { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.4); background: rgba(var(--accent-rgb), 0.07); }
.log-chip.plain.active { color: var(--text); border-color: var(--accent); background: rgba(var(--accent-rgb), 0.1); }
.log-chip.off { opacity: 0.35; filter: grayscale(0.6); }
.log-body {
  margin-top: 4px;
  background: #faf9ff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 8px 10px;
  max-height: 260px;
  overflow: auto;
  font-family: var(--mono);
  font-size: 11.5px;
  line-height: 1.55;
}
.log-line { display: flex; gap: 6px; align-items: flex-start; padding: 1px 0; }
.log-badge { flex: none; font-weight: 700; }
.log-text { flex: 1; min-width: 0; white-space: pre-wrap; word-break: break-word; }
.log-line.lv-error { color: var(--err); }
.log-line.lv-warning { color: var(--warn); }
.log-line.lv-info { color: #4b4468; }
.log-line.lv-error .log-badge { color: var(--err); }
.log-line.lv-warning .log-badge { color: var(--warn); }
.log-line.lv-info .log-badge { color: var(--muted); }

/* ---- 人工操作弹窗 ---- */
.manual-mask {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(11, 18, 32, 0.55);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.manual-card {
  width: min(720px, 100%); max-height: 88vh;
  background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
  box-shadow: 0 18px 48px rgba(11, 18, 32, 0.28);
  display: flex; flex-direction: column; overflow: hidden;
}
.manual-head { padding: 14px 18px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.manual-tag { background: rgba(var(--accent-2-rgb), 0.14); color: var(--accent); border-radius: 999px; padding: 2px 10px; font-size: 12px; font-weight: 600; }
.manual-title { margin: 0; font-size: 15px; color: var(--text); }
.manual-res { margin-left: auto; font-family: var(--mono, ui-monospace, Consolas, monospace); font-size: 11.5px; color: var(--muted); }
.manual-body { padding: 16px 18px; overflow: auto; font-size: 13px; line-height: 1.75; color: var(--text); }
.manual-body h1, .manual-body h2 { font-size: 15px; margin: 0 0 8px; }
.manual-body h3 { font-size: 13.5px; margin: 12px 0 6px; }
.manual-body p { margin: 6px 0; }
.manual-body ul, .manual-body ol { margin: 6px 0 6px 20px; padding: 0; }
.manual-body li { margin: 3px 0; }
.manual-body code { font-family: var(--mono, ui-monospace, Consolas, monospace); font-size: 12px; background: rgba(var(--accent-rgb), 0.08); padding: 1px 5px; border-radius: 4px; }
.manual-body blockquote { margin: 8px 0; padding: 6px 12px; border-left: 3px solid var(--accent); background: rgba(var(--accent-rgb), 0.05); color: var(--muted); font-size: 12.5px; }
.manual-body img { max-width: 100%; border-radius: 8px; margin: 8px 0; }
.manual-body hr { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
.manual-foot { padding: 12px 18px 16px; border-top: 1px solid var(--border); display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.manual-hint { font-size: 12px; color: var(--muted); flex: 1; min-width: 220px; }
.manual-actions { display: flex; gap: 10px; }
.manual-err { margin: 0 18px; }
.manual-note { padding: 0 18px 12px; display: flex; flex-direction: column; gap: 8px; }
.manual-note-label { font-size: 12px; color: var(--muted); }
.manual-note textarea {
  width: 100%; box-sizing: border-box; resize: vertical; min-height: 64px;
  padding: 8px 10px; border: 1px solid var(--border-strong); border-radius: 8px;
  background: var(--panel); color: var(--text); font: inherit; font-size: 13px; line-height: 1.6;
}
.manual-note textarea:focus { outline: none; border-color: var(--accent); }
.manual-note-actions { display: flex; justify-content: flex-end; gap: 10px; }
.btn.fail { border-color: rgba(220, 38, 38, 0.45); color: var(--err); font-weight: 600; }
.btn.fail:hover:not(:disabled) { border-color: var(--err); color: var(--err); background: rgba(220, 38, 38, 0.06); box-shadow: none; }
.step-type-manual { color: var(--accent); font-weight: 600; }

/* ---- 单步执行（cmd_suit 条目 step=true）---- */
.step-type-step { color: var(--accent); font-weight: 600; }
.calib-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0 10px;
  padding: 8px 10px;
  border: 1px solid rgba(109, 40, 217, 0.35);
  border-radius: 8px;
  background: rgba(109, 40, 217, 0.07);
  font-size: 12.5px;
  color: var(--accent);
}

.calib-tag {
  flex: 0 0 auto;
  padding: 1px 7px;
  border-radius: 5px;
  background: var(--accent);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.step-type-calib {
  background: rgba(109, 40, 217, 0.12);
  color: var(--accent);
  border-color: rgba(109, 40, 217, 0.35);
}

.check-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  padding: 6px 8px;
  border: 1px dashed rgba(109, 40, 217, 0.4);
  border-radius: 7px;
  background: rgba(109, 40, 217, 0.04);
  font-size: 12px;
}

.check-hint {
  color: var(--muted);
}

.check-done {
  color: var(--ok);
  font-weight: 600;
}

.check-rejected {
  color: var(--err);
  font-weight: 600;
}

.step-hold-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid var(--accent);
  border-radius: 10px;
  background: rgba(var(--accent-rgb), 0.06);
}
.step-hold-tag {
  background: rgba(var(--accent-2-rgb), 0.16);
  color: var(--accent);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
}
.step-hold-text { flex: 1; min-width: 200px; font-size: 12.5px; color: var(--text); }
.step-hold-row { display: flex; align-items: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
.step-hold-hint { font-size: 11.5px; color: var(--accent); }
.step-next { padding: 4px 12px; font-size: 12px; }

/* ---- 装备树上方: 运行档位区（生产工单闸门 / 调试仿真开关） ---- */
.wo-box, .dbg-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
}
.wo-box { border-left-color: #d97706; background: rgba(217, 119, 6, 0.06); }
.dbg-box { border-left-color: #0ea5e9; background: rgba(14, 165, 233, 0.05); }
.wo-head, .dbg-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.wo-tag { font-size: 12px; font-weight: 700; color: #b45309; }
.dbg-tag { font-size: 12px; font-weight: 700; color: #0369a1; }
.wo-note, .dbg-note { font-size: 10.5px; color: var(--muted); }
.wo-row { display: flex; align-items: center; gap: 8px; }
.wo-row .input { flex: 1; min-width: 0; font-size: 13px; letter-spacing: 1px; }
.wo-count { font-family: var(--mono); font-size: 11px; color: var(--muted); flex: none; }
.wo-count.ok { color: var(--ok); font-weight: 700; }
.wo-hint, .dbg-hint { font-size: 11px; line-height: 1.5; }
.wo-hint.ok { color: var(--ok); }
.wo-hint.warn { color: var(--warn); }
.dbg-hint { color: var(--muted); }
.sim-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 8px;
  border: 1px dashed var(--border-strong);
  background: #fff;
  cursor: pointer;
  text-align: left;
}
.sim-btn.on { border-style: solid; border-color: #0ea5e9; background: rgba(14, 165, 233, 0.1); }
.sim-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.sim-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex: none; }
.sim-btn.on .sim-dot { background: #0ea5e9; box-shadow: 0 0 8px rgba(14, 165, 233, 0.7); }
.sim-label { font-size: 12px; font-weight: 700; color: var(--text); }
.sim-state { font-size: 11px; color: var(--muted); }

/* ---- 云端上报状态 ---- */
.cloud-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: var(--muted);
  white-space: nowrap;
}
.cloud-chip.ok { color: var(--ok); border-color: rgba(21, 128, 61, 0.4); background: rgba(21, 128, 61, 0.1); }
.cloud-chip.err { color: var(--err); border-color: rgba(220, 38, 38, 0.4); background: rgba(220, 38, 38, 0.1); }
.cloud-retry {
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  border-radius: 999px;
  font-size: 11px;
  padding: 1px 8px;
  cursor: pointer;
}
.cloud-retry:disabled { opacity: 0.6; cursor: not-allowed; }

/* ---- 云端数据库面板（演示） ---- */
.cloud-panel {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px dashed var(--border-strong);
  border-radius: 10px;
  background: var(--panel-2);
}
.cloud-panel-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cloud-panel-title { font-size: 12.5px; font-weight: 700; }
.cloud-panel-state { font-size: 11px; font-weight: 700; margin-left: auto; }
.cloud-panel-state.ok { color: var(--ok); }
.cloud-panel-state.err { color: var(--err); }
.cloud-panel-meta { margin-top: 6px; font-size: 11px; color: var(--muted); font-family: var(--mono); word-break: break-all; line-height: 1.6; }
.cloud-records { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; max-height: 160px; overflow: auto; }
.cloud-rec { display: flex; align-items: center; gap: 10px; font-size: 11.5px; font-family: var(--mono); }
.cr-wo { font-weight: 700; color: var(--accent); }
.cr-uut { color: var(--text); }
.cr-result.ok { color: var(--ok); font-weight: 700; }
.cr-result.err { color: var(--err); font-weight: 700; }
.cr-time { margin-left: auto; color: var(--muted); }
.cloud-empty { margin-top: 8px; font-size: 11.5px; color: var(--muted); }

@media (max-width: 900px) {
  .tps-wrap { grid-template-columns: 1fr; padding: 14px; }
  .report-frame { height: 320px; }
}
.monitor-btn { margin-left: auto; }

.monitor-hint { font-size: 11.5px; color: var(--ok); margin-left: 6px; }

.deploy-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.deploy-panel { margin-top: 8px; border: 1px solid #e6e1d8; border-radius: 8px; padding: 8px 10px; background: #fbfbf9; }
.deploy-head { font-size: 12px; font-weight: 700; color: #6b6b66; margin-bottom: 6px; }
.deploy-item { display: flex; align-items: center; gap: 8px; font-size: 12px; padding: 3px 0; }
.deploy-name { min-width: 180px; color: #141414; }
.deploy-ver { color: #6b6b66; }
.deploy-badge { padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.deploy-badge.wait { background: #eeeeee; color: #6b6b66; }
.deploy-badge.run { background: #fff4e5; color: #b45309; }
.deploy-badge.ok { background: #e8f5ec; color: #15803d; }
.deploy-badge.err { background: #fdeaea; color: #b91c1c; }
.deploy-msg { color: #6b6b66; }

.mc-flag { display: inline-block; min-width: 16px; height: 16px; line-height: 16px; padding: 0 3px;
  margin-right: 6px; border-radius: 3px; background: #8b0f1a; color: #fff; font-size: 11px;
  font-weight: 700; text-align: center; vertical-align: 1px; }
</style>
