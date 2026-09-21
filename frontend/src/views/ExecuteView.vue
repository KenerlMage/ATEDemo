<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { api } from '../api'

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
  uutError.value = ''
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
    const res = await api.runTps(selectedTpsId.value, uut)
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
          <button class="btn primary" :disabled="running || !steps.length" @click="startRun">
            {{ running ? '执行中…' : '执行 TPS' }}
          </button>
        </div>
        <div v-if="uutError" class="tip err">{{ uutError }}</div>

        <div v-if="currentTask" class="task-status-row">
          <span class="status-label">任务状态</span>
          <span class="badge" :class="taskStatusCls">{{ taskStatusText }}</span>
          <span class="task-id">任务 {{ currentTask.id }}</span>
          <span v-if="currentTask.duration != null" class="task-id">耗时 {{ currentTask.duration.toFixed(2) }}s</span>
        </div>

        <div class="progress-track">
          <div class="progress-fill" :class="taskStatusCls" :style="{ width: progress + '%' }"></div>
        </div>
        <div class="progress-meta">
          <span>总进度 {{ progress }}%</span>
          <span v-if="currentTask?.current_step">当前第 {{ currentTask.current_step }} / {{ steps.length }} 步</span>
        </div>

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
                <span class="step-type">{{ { init: '环境初始化', test: '测试用例', teardown: '环境终止' }[st.type] || st.type }}</span>
                <span v-if="stepState[i + 1]?.duration != null" class="step-dur">
                  耗时 {{ stepState[i + 1].duration.toFixed(2) }}s
                </span>
              </div>
              <pre v-if="stepState[i + 1]?.detail" class="step-detail">{{ stepState[i + 1].detail }}</pre>
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
      </section>
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

@media (max-width: 900px) {
  .tps-wrap { grid-template-columns: 1fr; padding: 14px; }
  .report-frame { height: 320px; }
}
</style>
