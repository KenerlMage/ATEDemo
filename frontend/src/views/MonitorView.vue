<script setup>
/**
 * 实时数据监视窗口（独立控件）
 *  - 入口：测试执行页「实时监视」按钮，以独立窗口打开本页（?task=<任务号>）
 *  - 数据来源：测试用例运行中通过 ate_rt 库写入（Redis KVS，缺 Redis 时回落本地 SQLite），
 *              本页通过 /api/monitor/* 轮询读取：测试用例 -> 数据列表 -> 数据
 *  - 刷新：默认 1 Hz，可切 2 Hz（上限），支持暂停；折线图取每个选中数据项的最近 N 点
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'

const route = useRoute()

const tasks = ref([])
const taskId = ref('')
const channels = ref([])
const backend = ref('')
const taskMeta = ref({})
const selected = ref([])          // 已选中的数据项名（用于折线图），最多 4 个
const series = ref({})            // name -> [{ts_ms, v}]
const rateHz = ref(1)
const paused = ref(false)
const keyword = ref('')
const numericOnly = ref(false)
const loading = ref(true)
const err = ref('')
const lastAt = ref('')
const canvas = ref(null)

const COLORS = ['#6d28d9', '#0f766e', '#b45309', '#b91c1c']
const MAX_SEL = 4
const SERIES_LIMIT = 240

const activeTask = computed(() => tasks.value.find((t) => t.task_id === taskId.value) || null)
const shownChannels = computed(() => {
  let list = channels.value
  if (numericOnly.value) list = list.filter((c) => c.numeric)
  const kw = keyword.value.trim().toLowerCase()
  if (kw) list = list.filter((c) => (c.name + ' ' + c.label + ' ' + (c.group || '')).toLowerCase().includes(kw))
  return list
})
const numericChannels = computed(() => channels.value.filter((c) => c.numeric))
const selChannels = computed(() => selected.value.map((n) => channels.value.find((c) => c.name === n)).filter(Boolean))
const rateText = computed(() => (paused.value ? '已暂停' : `${rateHz.value} Hz`))
const taskLabel = computed(() => (activeTask.value ? activeTask.value.name : '未选择任务'))

function fmtValue(c) {
  if (c.value === null || c.value === undefined) return '—'
  if (typeof c.value === 'number') {
    const abs = Math.abs(c.value)
    if (abs !== 0 && (abs < 0.01 || abs >= 100000)) return c.value.toExponential(3)
    return Number.isInteger(c.value) ? String(c.value) : c.value.toFixed(3)
  }
  return String(c.value)
}
function fmtAge(age) {
  if (age === null || age === undefined) return '—'
  if (age < 1) return '<1 s'
  if (age < 60) return `${age.toFixed(0)} s`
  return `${(age / 60).toFixed(1)} min`
}
function fmtClock(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

/* ---------------- 数据加载 ---------------- */

async function loadTasks() {
  try {
    const res = await api.monitorTasks(30)
    tasks.value = (res && res.tasks) || []
    if (!taskId.value && tasks.value.length) {
      const q = String(route.query.task || '')
      taskId.value = tasks.value.find((t) => t.task_id === q) ? q : tasks.value[0].task_id
    }
  } catch (e) {
    err.value = `读取任务失败：${e.message}`
  }
}

async function loadSnapshot() {
  if (!taskId.value) return
  try {
    const res = await api.monitorSnapshot(taskId.value)
    if (!res || res.success === false) throw new Error((res && res.message) || '读取失败')
    channels.value = res.channels || []
    taskMeta.value = res.task || {}
    backend.value = res.backend || ''
    err.value = ''
    lastAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    // 选中的数据项若已消失则剔除
    selected.value = selected.value.filter((n) => channels.value.some((c) => c.name === n && c.numeric))
  } catch (e) {
    err.value = `读取实时数据失败：${e.message}`
  }
}

async function loadSeries() {
  if (!taskId.value || !selected.value.length) return
  const result = {}
  await Promise.all(selected.value.map(async (name) => {
    try {
      const res = await api.monitorSeries(taskId.value, name, SERIES_LIMIT)
      result[name] = (res && res.points) || []
    } catch (e) {
      result[name] = []
    }
  }))
  series.value = result
  drawChart()
}

async function tick() {
  if (paused.value || !taskId.value) return
  await loadSnapshot()
  await loadSeries()
}

let timer = null
function rearm() {
  if (timer) clearInterval(timer)
  timer = setInterval(tick, Math.round(1000 / rateHz.value))
}

/* ---------------- 交互 ---------------- */

function selectTask(id) {
  taskId.value = id
  selected.value = []
  channels.value = []
  series.value = {}
  loadSnapshot().then(loadSeries)
}

function toggleChannel(c) {
  if (!c.numeric) return
  const idx = selected.value.indexOf(c.name)
  if (idx >= 0) selected.value.splice(idx, 1)
  else if (selected.value.length < MAX_SEL) selected.value.push(c.name)
  loadSeries()
}

function setRate(hz) {
  rateHz.value = hz
  rearm()
  flash()
}

function togglePause() {
  paused.value = !paused.value
  if (!paused.value) tick()
  flash()
}

const hint = ref('')
function flash(text = '') {
  hint.value = text || `刷新率 ${rateHz.value} Hz`
  setTimeout(() => { if (hint.value) hint.value = '' }, 2200)
}

function openTasks() {
  loadTasks()
}

/* ---------------- 折线图 ---------------- */

function drawChart() {
  const cv = canvas.value
  if (!cv) return
  const dpr = window.devicePixelRatio || 1
  const rect = cv.getBoundingClientRect()
  const w = Math.max(rect.width, 320)
  const h = Math.max(rect.height, 180)
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr)
    cv.height = Math.round(h * dpr)
  }
  const ctx = cv.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  const padL = 58, padR = 14, padT = 16, padB = 26
  const plotW = w - padL - padR
  const plotH = h - padT - padB

  // 收集所有点的时间范围
  const names = selected.value.filter((n) => (series.value[n] || []).length)
  let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity
  names.forEach((n) => {
    (series.value[n] || []).forEach((p) => {
      if (typeof p.v !== 'number') return
      tMin = Math.min(tMin, p.ts_ms)
      tMax = Math.max(tMax, p.ts_ms)
      vMin = Math.min(vMin, p.v)
      vMax = Math.max(vMax, p.v)
    })
  })

  ctx.strokeStyle = '#ECE6F5'
  ctx.lineWidth = 1
  ctx.fillStyle = '#8A8A8A'
  ctx.font = '11px "Microsoft YaHei", sans-serif'

  if (!names.length || tMin === Infinity) {
    for (let i = 0; i <= 4; i += 1) {
      const y = padT + (plotH * i) / 4
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke()
    }
    ctx.fillText('选择左侧数值项以绘制折线（最多 4 项）', padL + 8, padT + plotH / 2)
    return
  }

  const span = Math.max(tMax - tMin, 1)
  if (vMax - vMin < 1e-9) { vMax += 0.5; vMin -= 0.5 }
  const vPad = (vMax - vMin) * 0.12
  vMin -= vPad; vMax += vPad

  const xOf = (t) => padL + ((t - tMin) / span) * plotW
  const yOf = (v) => padT + plotH - ((v - vMin) / (vMax - vMin)) * plotH

  // 网格与 Y 轴刻度
  for (let i = 0; i <= 4; i += 1) {
    const y = padT + (plotH * i) / 4
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke()
    const val = vMax - ((vMax - vMin) * i) / 4
    ctx.fillText(Math.abs(val) >= 1000 ? val.toFixed(0) : val.toFixed(2), 8, y + 4)
  }
  // X 轴时间刻度
  for (let i = 0; i <= 4; i += 1) {
    const x = padL + (plotW * i) / 4
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + plotH); ctx.stroke()
    const t = tMin + (span * i) / 4
    ctx.fillText(fmtClock(t), x - 18, padT + plotH + 16)
  }

  // 折线
  names.forEach((name, idx) => {
    const pts = (series.value[name] || []).filter((p) => typeof p.v === 'number')
    ctx.strokeStyle = COLORS[idx % COLORS.length]
    ctx.lineWidth = 1.8
    ctx.beginPath()
    pts.forEach((p, i) => {
      const x = xOf(p.ts_ms), y = yOf(p.v)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.stroke()
    const last = pts[pts.length - 1]
    if (last) {
      ctx.fillStyle = COLORS[idx % COLORS.length]
      ctx.beginPath(); ctx.arc(xOf(last.ts_ms), yOf(last.v), 2.6, 0, Math.PI * 2); ctx.fill()
    }
  })

  // 图例
  let lx = padL + 4
  names.forEach((name, idx) => {
    const ch = channels.value.find((c) => c.name === name)
    const pts = series.value[name] || []
    const last = pts.length ? pts[pts.length - 1].v : null
    const text = `${ch ? (ch.label || ch.name) : name}${ch && ch.unit ? ` (${ch.unit})` : ''} ${last === null ? '' : fmtNumber(last)}`
    ctx.fillStyle = COLORS[idx % COLORS.length]
    ctx.fillRect(lx, padT + 2, 10, 3)
    ctx.fillStyle = '#333'
    ctx.fillText(text, lx + 14, padT + 8)
    lx += ctx.measureText(text).width + 34
  })
}

function fmtNumber(v) {
  if (typeof v !== 'number') return String(v)
  const abs = Math.abs(v)
  if (abs !== 0 && (abs < 0.01 || abs >= 100000)) return v.toExponential(2)
  return Number.isInteger(v) ? String(v) : v.toFixed(3)
}

let ro = null
onMounted(async () => {
  await loadTasks()
  await loadSnapshot()
  if (numericChannels.value.length && !selected.value.length) {
    selected.value = [numericChannels.value[0].name]
  }
  await loadSeries()
  loading.value = false
  rearm()
  if (window.ResizeObserver && canvas.value) {
    ro = new ResizeObserver(() => drawChart())
    ro.observe(canvas.value)
  }
  window.addEventListener('resize', drawChart)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (ro) ro.disconnect()
  window.removeEventListener('resize', drawChart)
})

watch(() => route.query.task, (v) => {
  if (v && tasks.value.some((t) => t.task_id === v)) selectTask(String(v))
})
</script>

<template>
  <div class="mon-page">
    <header class="mon-head">
      <div class="hd-left">
        <span class="hd-title">实时数据监视</span>
        <span class="hd-sub">{{ taskLabel }}</span>
        <span v-if="activeTask" class="chip" :class="activeTask.status">{{ activeTask.status }}</span>
        <span v-if="taskMeta.uut" class="chip ghost">UUT {{ taskMeta.uut }}</span>
        <span class="chip ghost">{{ backend === 'redis' ? 'Redis KVS' : (backend === 'sqlite' ? 'SQLite 回退' : '存储未知') }}</span>
      </div>
      <div class="hd-right">
        <span v-if="hint" class="hint">{{ hint }}</span>
        <span class="clock" :class="{ paused }">{{ rateText }} · {{ lastAt || '—' }}</span>
        <button class="btn ghost small" :class="{ on: rateHz === 1 }" @click="setRate(1)">1 Hz</button>
        <button class="btn ghost small" :class="{ on: rateHz === 2 }" @click="setRate(2)">2 Hz</button>
        <button class="btn small" @click="togglePause">{{ paused ? '继续' : '暂停' }}</button>
        <button class="btn ghost small" @click="openTasks">刷新任务</button>
      </div>
    </header>

    <div v-if="err" class="tip err">{{ err }}</div>

    <div class="mon-body">
      <aside class="task-col">
        <div class="col-head">测试用例<span class="cnt">{{ tasks.length }}</span></div>
        <div class="task-list">
          <article
            v-for="t in tasks"
            :key="t.task_id"
            class="task"
            :class="{ on: t.task_id === taskId }"
            @click="selectTask(t.task_id)"
          >
            <div class="task-name">{{ t.name || t.task_id }}</div>
            <div class="task-meta">
              <span class="dot" :class="t.status"></span>
              <span>{{ t.status || '—' }}</span>
              <span v-if="t.mode">· {{ t.mode }}</span>
            </div>
            <div class="task-meta muted">{{ t.uut || '—' }} · {{ t.tps_name || t.tps_id || '—' }}</div>
          </article>
          <div v-if="!tasks.length" class="empty">暂无任务：执行一次 TPS 或运行示例脚本后出现</div>
        </div>
      </aside>

      <main class="data-col">
        <section class="list-pane">
          <div class="col-head">
            数据列表<span class="cnt">{{ shownChannels.length }}</span>
            <input v-model="keyword" class="input small" placeholder="检索数据项" />
            <label class="chk"><input type="checkbox" v-model="numericOnly" />仅数值</label>
            <span class="sel-note">已选 {{ selected.length }}/{{ MAX_SEL }}（点击行加入折线）</span>
          </div>
          <div class="table-wrap">
            <table class="mon-table">
              <thead>
                <tr>
                  <th class="c-name">数据项</th>
                  <th class="c-val">当前值</th>
                  <th class="c-unit">单位</th>
                  <th class="c-grp">分组</th>
                  <th class="c-age">更新时间</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="c in shownChannels"
                  :key="c.name"
                  :class="{ sel: selected.includes(c.name), stale: c.stale, dim: !c.numeric }"
                  @click="toggleChannel(c)"
                >
                  <td class="c-name">
                    <span class="swatch" :class="{ on: selected.includes(c.name) }"></span>
                    {{ c.label || c.name }}
                    <span class="raw-name">{{ c.name }}</span>
                  </td>
                  <td class="c-val mono" :class="{ num: c.numeric }">{{ fmtValue(c) }}</td>
                  <td class="c-unit">{{ c.unit || '—' }}</td>
                  <td class="c-grp">{{ c.group || '—' }}</td>
                  <td class="c-age">{{ fmtAge(c.age_s) }}</td>
                </tr>
                <tr v-if="!shownChannels.length">
                  <td colspan="5" class="empty">该任务暂无数据项：用例中调用 ate_rt.declare / ate_rt.write 后出现</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="chart-pane">
          <div class="col-head">
            折线图
            <span class="legend" v-for="(c, i) in selChannels" :key="c.name">
              <i :style="{ background: COLORS[i % COLORS.length] }"></i>{{ c.label || c.name }}
              <button class="x" @click="toggleChannel(c)">×</button>
            </span>
            <span class="pane-note">最近 {{ SERIES_LIMIT }} 点 · 时间升序</span>
          </div>
          <div class="canvas-box">
            <canvas ref="canvas"></canvas>
            <div v-if="loading" class="overlay">正在读取…</div>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<style scoped>
.mon-page { flex: 1; display: flex; flex-direction: column; gap: 10px; padding: 12px 14px 14px; min-height: 100vh; background: var(--panel-2); }
.mon-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 14px; border-left: 3px solid var(--accent);
}
.hd-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hd-title { font-size: 16px; font-weight: 800; }
.hd-sub { font-size: 12.5px; color: var(--accent); font-weight: 600; }
.hd-right { display: flex; align-items: center; gap: 6px; }
.hint { font-size: 11.5px; color: var(--accent); }
.clock { font-size: 11.5px; color: var(--muted); font-family: var(--mono); }
.clock.paused { color: var(--warn); }
.chip { font-size: 11px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); background: var(--panel-2); }
.chip.running { color: var(--ok); border-color: rgba(21, 128, 61, 0.35); background: rgba(21, 128, 61, 0.08); }
.chip.failed, .chip.error { color: var(--err); border-color: rgba(190, 40, 40, 0.3); background: rgba(190, 40, 40, 0.07); }
.chip.completed, .chip.done { color: var(--muted); }
.chip.ghost { background: transparent; }

.btn.small { padding: 5px 10px; font-size: 12px; }
.btn.ghost.small.on { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.5); background: rgba(var(--accent-rgb), 0.08); }

.mon-body { flex: 1; display: grid; grid-template-columns: 250px 1fr; gap: 10px; min-height: 0; }
.task-col, .list-pane, .chart-pane { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; display: flex; flex-direction: column; min-height: 0; }
.col-head {
  display: flex; align-items: center; gap: 8px; padding: 9px 12px;
  border-bottom: 1px solid var(--border); font-size: 12.5px; font-weight: 700;
}
.cnt { font-size: 11px; color: var(--muted); font-weight: 500; }
.task-list { overflow-y: auto; padding: 6px; display: flex; flex-direction: column; gap: 4px; }
.task { border: 1px solid transparent; border-radius: 7px; padding: 7px 8px; cursor: pointer; }
.task:hover { background: var(--panel-2); }
.task.on { border-color: rgba(var(--accent-rgb), 0.45); background: rgba(var(--accent-rgb), 0.07); }
.task-name { font-size: 12.5px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.task-meta { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 5px; margin-top: 2px; }
.task-meta.muted { color: var(--border-strong); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border-strong); }
.dot.running { background: var(--ok); }
.dot.failed, .dot.error { background: var(--err); }
.dot.completed, .dot.done { background: var(--accent); }

.data-col { display: grid; grid-template-rows: minmax(180px, 44%) 1fr; gap: 10px; min-height: 0; }
.input.small { padding: 3px 8px; font-size: 11.5px; max-width: 160px; margin-left: auto; }
.chk { font-size: 11.5px; color: var(--muted); display: flex; align-items: center; gap: 4px; }
.sel-note { font-size: 11px; color: var(--muted); margin-left: auto; }
.table-wrap { overflow: auto; }
.mon-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.mon-table th {
  position: sticky; top: 0; background: var(--panel-2); text-align: left; font-weight: 700;
  font-size: 11.5px; color: var(--muted); padding: 6px 10px; border-bottom: 1px solid var(--border);
}
.mon-table td { padding: 5px 10px; border-bottom: 1px solid var(--border); }
.mon-table tbody tr { cursor: pointer; }
.mon-table tbody tr:hover { background: var(--panel-2); }
.mon-table tr.sel { background: rgba(var(--accent-rgb), 0.07); }
.mon-table tr.stale { opacity: 0.62; }
.mon-table tr.dim { cursor: default; }
.c-val { font-family: var(--mono); font-weight: 700; }
.c-val.num { color: var(--accent); }
.c-unit, .c-grp, .c-age { color: var(--muted); font-size: 11.5px; }
.c-age { text-align: right; width: 90px; }
.swatch { display: inline-block; width: 8px; height: 8px; border: 1px solid var(--border-strong); border-radius: 2px; margin-right: 6px; }
.swatch.on { background: var(--accent); border-color: var(--accent); }
.raw-name { font-family: var(--mono); font-size: 10.5px; color: var(--border-strong); margin-left: 6px; }
.empty { color: var(--muted); font-size: 12px; padding: 14px; text-align: center; }

.legend { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; color: var(--muted); font-weight: 500; }
.legend i { width: 10px; height: 3px; display: inline-block; }
.legend .x { border: none; background: none; color: var(--border-strong); cursor: pointer; font-size: 12px; padding: 0 2px; }
.pane-note { margin-left: auto; font-size: 11px; color: var(--muted); font-weight: 500; }
.canvas-box { position: relative; flex: 1; min-height: 160px; padding: 6px 8px 8px; }
.canvas-box canvas { width: 100%; height: 100%; display: block; }
.overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 12px; }
.tip.err { color: var(--err); font-size: 12px; padding: 4px 2px; }
</style>
