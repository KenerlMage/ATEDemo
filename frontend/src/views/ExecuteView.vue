<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
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
const manualError = ref('')
// 提示内容由后端从 TPS 包 resource/ 目录读出并随任务快照下发
const manualHtml = computed(() => renderMarkdown(manual.value?.markdown || '', selectedTpsId.value))
// ---- 单步执行（cmd_suit 条目 step=true）----
// 该条用例跑完后 Runner 暂停：界面弹出「下一步」按钮，放行后才执行清单里的下一条
const manualKind = computed(() => manual.value?.kind || 'manual')
const manualIsManual = computed(() => manualOpen.value && manualKind.value === 'manual')
const stepHoldOpen = computed(() => manualOpen.value && manualKind.value === 'step')

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

async function answerManual(decision) {
  const id = currentTask.value?.id
  if (!id || manualSubmitting.value) return
  manualSubmitting.value = true
  manualError.value = ''
  try {
    const res = await api.decideTpsManual(id, decision)
    if (!res.success) manualError.value = res.message || '提交失败'
    else await pollTask()
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
  try {
    const content = await file.text()
    importingTps.value = true
    const res = await api.importTps(content, file.name)
    if (res.success) {
      tpsImportMsg.value = '✅ ' + (res.message || 'TPS 导入成功')
      await loadTree()
    } else {
      tpsImportErr.value = '❌ ' + (res.message || '导入失败')
    }
  } catch (err) {
    tpsImportErr.value = '❌ 导入失败：' + err.message
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
    duration: null
  }
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
    await nextTick()
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
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

// ---- 生命周期 ----
onMounted(async () => {
  await loadTree()
  await loadCloudStatus()
  await loadCloudRecords()
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="tps-wrap">
    <!-- 左列 -->
    <div class="left-col">
      <!-- 左上: 装备树 -->
      <section class="tree-panel">
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
          <button class="btn ghost small block" :disabled="importingTps" @click="tpsFileInput?.click()">
            {{ importingTps ? '导入中…' : '📂 导入 TPS 文件' }}
          </button>
          <input ref="tpsFileInput" type="file" accept=".json,application/json" class="hidden-input" @change="pickTpsFile" />
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

        <div v-if="stepHoldOpen" class="step-hold-banner">
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
                <span v-if="stepState[i + 1]?.duration != null" class="step-dur">
                  耗时 {{ stepState[i + 1].duration.toFixed(2) }}s
                </span>
              </div>
              <pre v-if="stepState[i + 1]?.detail" class="step-detail">{{ stepState[i + 1].detail }}</pre>
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

      <!-- 右下: 测试报告 -->
      <section class="report-panel">
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
      <div class="manual-card">
        <div class="manual-head">
          <span class="manual-tag">人工操作</span>
          <h3 class="manual-title">{{ manual.name }}</h3>
          <span v-if="manual.resource" class="manual-res">resource/{{ manual.resource }}</span>
        </div>
        <div class="manual-body" v-html="manualHtml"></div>
        <div v-if="manualError" class="tip err manual-err">{{ manualError }}</div>
        <div class="manual-foot">
          <span class="manual-hint">
            「继续下一步」执行清单里的下一条用例；「中止测试」停止本轮，直接进入收尾与报告。
            <template v-if="manual.timeout_s">等待超时设为 {{ manual.timeout_s }} 秒，超时按中止处理。「中止」用于判定当前环节不通过并结束本轮；「取消」用于主动放弃本轮（如装夹错误需重装）。</template>
          </span>
          <div class="manual-actions">
            <button class="btn ghost" :disabled="manualSubmitting" @click="answerManual('cancel')">取消本轮测试</button>
            <button class="btn ghost" :disabled="manualSubmitting" @click="answerManual('abort')">中止测试</button>
            <button class="btn primary" :disabled="manualSubmitting" @click="answerManual('next')">继续下一步</button>
          </div>
        </div>
      </div>
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
.step-type-manual { color: var(--accent); font-weight: 600; }

/* ---- 单步执行（cmd_suit 条目 step=true）---- */
.step-type-step { color: var(--accent); font-weight: 600; }
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
</style>
