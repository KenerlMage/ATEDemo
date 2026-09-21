<script setup>
/**
 * 装备助手页
 *  - 卡片形式提供控制设备的小工具（首期：数字示波器 · 泰克 MSO54 方案）
 *  - 单击 / 双击卡片选中 → 点「运行」按钮启动工具
 *  - 示波器面板: 绑定已注册测试台中的设备、连接/识别、运行/停止/单次、
 *    自动设置、时基、通道、波形显示(canvas)、参数测量、SCPI 命令日志
 */
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api } from '../api'

const loading = ref(true)
const error = ref('')
const tools = ref([])
const selectedId = ref('')
const running = ref('')            // 已运行的工具 id

// 示波器状态
const binds = ref([])              // 可绑定设备（来自注册 BOM）
const bindSel = ref({ bench_id: '', device_id: '' })
const scopeMode = ref('simulate')  // simulate | real
const connected = ref(false)
const busy = ref(false)
const notice = ref({ text: '', kind: 'ok' })
const state = reactive({
  idn: '', host: '', port: '', timebase: 1e-6, running: true, single: false,
  channels: {}, signal: {}, log: [], mode: '', opened_at: ''
})
const wave = ref({ samples: [], x_incr: 0, source: '', points: 0 })
const measures = ref([])
const activeCh = ref('CH1')

/* ---------------- 仿真信号源 ---------------- */
const SIM_FREQS = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000]
const SIM_TYPES = [
  { id: 'sine', label: '正弦波' },
  { id: 'square', label: '方波' },
  { id: 'triangle', label: '三角波' },
  { id: 'sawtooth', label: '锯齿波' },
  { id: 'dc', label: '直流' },
  { id: 'noise', label: '噪声' }
]
const simForm = ref({ type: 'sine', freq: 1000, vpp: 2.4, offset: 0, noise: 0.02, cycles: 5 })
const simFreqIdx = ref(6)
const simTimer = ref(null)
const simActive = computed(() => connected.value && state.mode === 'simulate')
const simTypeLabel = computed(() => (SIM_TYPES.find((s) => s.id === simForm.value.type) || SIM_TYPES[0]).label)
const virtualBind = computed(() => binds.value.find((b) => b.virtual || b.bench_id === '__sim__') || null)

function fmtFreq(f) {
  const v = Number(f) || 0
  if (v >= 1e6) return `${(v / 1e6).toFixed(3)} MHz`
  if (v >= 1e3) return `${(v / 1e3).toFixed(3)} kHz`
  return `${v.toFixed(0)} Hz`
}

function nearestFreqIdx(f) {
  const v = Number(f) || 1000
  let best = 0
  SIM_FREQS.forEach((x, i) => { if (Math.abs(x - v) < Math.abs(SIM_FREQS[best] - v)) best = i })
  return best
}

/** 用后端返回的仿真参数回填界面，保证界面与服务端一致 */
function syncSimForm(sig, sim) {
  if (!sig || !sig.type) return
  simForm.value = {
    type: sig.type,
    freq: Number(sig.freq) || 1000,
    vpp: Number(sig.vpp || (sig.amp ? sig.amp * 2 : 2.4)),
    offset: Number(sig.offset) || 0,
    noise: Number(sig.noise) || 0,
    cycles: Number((sim && sim.cycles) || 5)
  }
  simFreqIdx.value = nearestFreqIdx(simForm.value.freq)
}

function setSimType(id) {
  simForm.value = { ...simForm.value, type: id }
  pushSim(true)
}

function setSimFreq(idx) {
  const i = Math.min(Math.max(Number(idx) || 0, 0), SIM_FREQS.length - 1)
  simFreqIdx.value = i
  simForm.value = { ...simForm.value, freq: SIM_FREQS[i] }
  pushSim()
}

function setSimNum(key, value, immediate = false) {
  simForm.value = { ...simForm.value, [key]: Number(value) }
  pushSim(immediate)
}

/** 参数变更 → 防抖下发仿真信号源并重画波形 */
function pushSim(immediate = false) {
  if (!simActive.value) return
  if (simTimer.value) clearTimeout(simTimer.value)
  simTimer.value = setTimeout(() => { simTimer.value = null; applySim() }, immediate ? 0 : 160)
}

async function applySim() {
  if (!simActive.value) return
  const f = simForm.value
  const r = await act('sim_signal', {
    type: f.type,
    freq: f.freq,
    vpp: f.vpp,
    offset: f.offset,
    noise: f.noise,
    cycles: f.cycles
  })
  if (r && r.note) flash(r.note, 'warn')
  await frame()
}

/** 一键仿真：不选设备也能进入（后端自动使用内置仿真信号源） */
async function quickSim() {
  scopeMode.value = 'simulate'
  if (!bindSel.value.device_id && virtualBind.value) {
    bindSel.value = { bench_id: virtualBind.value.bench_id, device_id: virtualBind.value.device_id }
  }
  await connect()
}

const logBox = ref(null)
const canvas = ref(null)

const tool = computed(() => tools.value.find((t) => t.id === selectedId.value) || null)
const bindsGrouped = computed(() => {
  const real = binds.value.filter((b) => !b.virtual && b.bench_id !== '__sim__')
  const scope = real.filter((b) => b.is_scope)
  const other = real.filter((b) => !b.is_scope)
  return { scope, other }
})
const channels = computed(() => Object.entries(state.channels || {}).map(([name, st]) => ({ name, ...st })))
const connLabel = computed(() => {
  if (!connected.value) return '未连接'
  return state.mode === 'simulate' ? (state.virtual ? '仿真中（内置信号源）' : '仿真中（绑定设备）') : '已连接仪器'
})

function flash(text, kind = 'ok') {
  notice.value = { text, kind }
  setTimeout(() => { if (notice.value.text === text) notice.value = { text: '', kind } }, 4200)
}

async function loadTools() {
  try {
    const res = await api.getTools()
    tools.value = (res && res.tools) || []
    if (!selectedId.value && tools.value.length) selectedId.value = tools.value[0].id
  } catch (e) {
    error.value = `无法连接后端: ${e.message}`
  }
}

async function loadBindings() {
  try {
    const res = await api.getOscilloscopeBindings()
    binds.value = (res && res.devices) || []
    if (!bindSel.value.device_id && binds.value.length) {
      const first = binds.value.find((b) => b.is_scope) || binds.value[0]
      bindSel.value = { bench_id: first.bench_id, device_id: first.device_id }
    }
    if (res && res.hint) flash(res.hint, 'warn')
  } catch (e) {
    flash(`读取装备绑定失败: ${e.message}`, 'warn')
  }
}

onMounted(async () => {
  await Promise.all([loadTools(), loadBindings()])
  loading.value = false
})

/* ---------------- 卡片选中 / 运行 ---------------- */
function selectCard(t) {
  selectedId.value = t.id
}
function runSelected() {
  const t = tool.value
  if (!t) { flash('请先点选一张工具卡片', 'warn'); return }
  if (!t.available) { flash(`「${t.name}」尚在规划中，请先使用数字示波器`, 'warn'); return }
  running.value = t.id
  if (t.id === 'oscilloscope') {
    setTimeout(() => canvas.value && draw(), 60)
  }
}

/* ---------------- 示波器操作 ---------------- */
async function connect() {
  if (scopeMode.value === 'real' && (!bindSel.value.bench_id || !bindSel.value.device_id)) {
    flash('真实仪器模式请先选择测试台与示波器设备', 'warn'); return
  }
  if (scopeMode.value === 'simulate' && !bindSel.value.device_id && virtualBind.value) {
    bindSel.value = { bench_id: virtualBind.value.bench_id, device_id: virtualBind.value.device_id }
  }
  busy.value = true
  try {
    const res = await api.connectOscilloscope({ ...bindSel.value, mode: scopeMode.value, timeout: 2, allow_fallback: true })
    if (!res || res.success === false) { flash((res && res.message) || '连接失败', 'warn'); return }
    applyState(res.state)
    connected.value = true
    const isSim = res.state && res.state.mode === 'simulate'
    flash(res.fallback ? res.message : (isSim ? res.message : `已连接：${(res.state && res.state.name) || ''}`), res.fallback ? 'warn' : 'ok')
    await frame()
  } catch (e) {
    flash(`连接失败: ${e.message}`, 'warn')
  } finally {
    busy.value = false
  }
}

async function disconnect() {
  await act('close', {})
  connected.value = false
  wave.value = { samples: [], x_incr: 0, source: '', points: 0 }
  measures.value = []
  flash('已断开示波器')
}

function applyState(s) {
  if (!s) return
  state.idn = s.idn || state.idn
  state.host = s.host || ''
  state.port = s.port || ''
  state.timebase = s.timebase || state.timebase
  state.running = !!s.running
  state.single = !!s.single
  state.channels = s.channels || {}
  state.signal = s.signal || {}
  syncSimForm(s.signal, s.sim)
  state.log = s.log || []
  state.mode = s.mode || ''
  state.opened_at = s.opened_at || ''
  scrollLog()
}

function scrollLog() {
  setTimeout(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight }, 40)
}

async function act(action, params = {}, extra = {}) {
  if (!connected.value) { flash('尚未连接示波器，请先点「运行」', 'warn'); return null }
  busy.value = true
  try {
    const res = await api.oscilloscopeAction({
      bench_id: bindSel.value.bench_id,
      device_id: bindSel.value.device_id,
      action,
      channel: activeCh.value,
      params,
      ...extra
    })
    applyState(res && res.state)
    if (!res || res.success === false) { flash((res && res.message) || `${action} 失败`, 'warn'); return null }
    return res.result
  } catch (e) {
    flash(`${action} 失败: ${e.message}`, 'warn')
    return null
  } finally {
    busy.value = false
  }
}

async function frame() {
  const r = await act('frame', { points: 1000 })
  if (!r) return
  wave.value = r.waveform || wave.value
  measures.value = r.measurements || []
  draw()
  scrollLog()
}

async function runAcq() { await act('run'); flash('已开始采集 (RUN)') }
async function stopAcq() { await act('stop'); flash('已停止采集 (STOP)') }
async function singleAcq() { await act('single'); flash('已触发单次采集 (SINGLE)') }
async function autoset() { await act('autoset'); flash('自动设置完成'); await frame() }
async function setTimebase(delta) {
  const cur = Number(state.timebase) || 1e-6
  const next = Math.min(Math.max(cur * delta, 1e-9), 10)
  await act('timebase', { scale: next })
  await frame()
}
async function setTimebaseValue(v) {
  const scale = Number(v)
  if (!scale || scale <= 0) return
  await act('timebase', { scale })
  await frame()
}
async function toggleChannel(name) {
  const st = state.channels[name] || {}
  await act('channel', { display: !st.display }, { channel: name })
  if (!st.display) activeCh.value = name
  await frame()
}
async function setChannelValue(name, key, value) {
  const params = {}
  params[key] = value
  await act('channel', params, { channel: name })
  if (key === 'scale' || key === 'offset') await frame()
}
async function pickChannel(name) {
  activeCh.value = name
  await frame()
}

/* ---------------- 波形绘制 ---------------- */
function draw() {
  const el = canvas.value
  if (!el) return
  const dpr = window.devicePixelRatio || 1
  const w = el.clientWidth || 720
  const h = el.clientHeight || 300
  el.width = w * dpr
  el.height = h * dpr
  const ctx = el.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  // 工业风网格（10 × 8 格）
  const COLS = 10
  const ROWS = 8
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, w, h)
  ctx.strokeStyle = 'rgba(109, 40, 217, 0.13)'
  ctx.lineWidth = 1
  for (let i = 1; i < COLS; i++) {
    const x = (w / COLS) * i
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke()
  }
  for (let j = 1; j < ROWS; j++) {
    const y = (h / ROWS) * j
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke()
  }
  // 中线
  ctx.strokeStyle = 'rgba(109, 40, 217, 0.32)'
  ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke()
  ctx.beginPath(); ctx.moveTo(w / 2, 0); ctx.lineTo(w / 2, h); ctx.stroke()

  const samples = (wave.value.samples || [])
  if (!samples.length) {
    ctx.fillStyle = 'rgba(110, 104, 138, 0.85)'
    ctx.font = '13px "Segoe UI", "Microsoft YaHei", sans-serif'
    ctx.fillText('无波形数据 — 点击「运行」建立连接后自动采集', 16, 24)
    return
  }

  const st = state.channels[activeCh.value] || {}
  const scale = Number(st.scale) || 1            // V/div
  const offset = Number(st.offset) || 0          // V
  const half = (ROWS / 2) * scale                // 半屏电压范围

  ctx.strokeStyle = '#6d28d9'
  ctx.lineWidth = 1.6
  ctx.beginPath()
  const n = samples.length
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * w
    const v = samples[i] - offset
    const y = h / 2 - (v / half) * (h / 2)
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  }
  ctx.stroke()

  // 量程标注
  ctx.fillStyle = 'rgba(110, 104, 138, 0.9)'
  ctx.font = '11px Consolas, monospace'
  ctx.fillText(`${activeCh.value}  ${scale} V/div  ${st.coupling || 'DC'}  offset ${offset} V`, 10, h - 10)
  ctx.fillText(`${wave.value.source === 'instrument' ? '仪器实测' : '离线模拟'} · ${wave.value.points} 点`, w - 150, h - 10)
}

watch(() => state.channels, () => draw(), { deep: true })
watch(() => activeCh.value, () => draw())
watch(() => wave.value.samples.length, () => draw())
window.addEventListener('resize', draw)
onUnmounted(() => window.removeEventListener('resize', draw))

function fmtTime(s) {
  if (!s) return '—'
  const v = Number(s)
  if (v >= 1) return `${v} s`
  if (v >= 1e-3) return `${(v * 1e3).toFixed(3)} ms`
  if (v >= 1e-6) return `${(v * 1e6).toFixed(3)} µs`
  return `${(v * 1e9).toFixed(1)} ns`
}
function fmtVal(m) {
  if (m.value === null || m.value === undefined) return '—'
  const v = Number(m.value)
  if (!Number.isFinite(v)) return '—'
  const unit = m.unit || ''
  if (unit === 'Hz') return v >= 1e6 ? `${(v / 1e6).toFixed(4)} MHz` : `${v.toFixed(1)} Hz`
  if (unit === 's') return fmtTime(v)
  if (Math.abs(v) >= 1000) return `${v.toFixed(2)} ${unit}`
  if (Math.abs(v) < 0.01 && v !== 0) return `${(v * 1000).toFixed(2)} m${unit}`
  return `${v.toFixed(4)} ${unit}`
}
const MEAS_LABEL = {
  PK2PK: '峰峰值 Vpp', AMPLITUDE: '幅值', FREQUENCY: '频率', PERIOD: '周期',
  MEAN: '平均值', RMS: '有效值 RMS', RISE: '上升时间', FALL: '下降时间'
}
</script>

<template>
  <div class="as-page">
    <!-- 头部 -->
    <section class="as-head">
      <div>
        <div class="hd-title">装备助手</div>
        <div class="hd-sub">控制测试装备的小工具集。单击 / 双击卡片选中工具，再点右上角「运行」启动。</div>
      </div>
      <div class="hd-right">
        <span v-if="tool" class="sel-chip">
          已选：<b>{{ tool.name }}</b>
        </span>
        <button class="btn primary run-btn" :disabled="!tool || !tool.available" @click="runSelected">
          ▶ 运行工具
        </button>
      </div>
    </section>

    <div v-if="loading" class="tip">正在加载工具清单…</div>
    <div v-else-if="error" class="tip err">{{ error }}</div>

    <!-- 工具卡片 -->
    <section class="cards">
      <article
        v-for="t in tools"
        :key="t.id"
        class="tool"
        :class="{ on: selectedId === t.id, off: !t.available, running: running === t.id }"
        @click="selectCard(t)"
        @dblclick="selectCard(t); runSelected()"
      >
        <div class="tool-top">
          <span class="tool-icon" :class="'ic-' + t.icon"></span>
          <span v-if="t.available" class="tag ok">可用</span>
          <span v-else class="tag">规划中</span>
          <span v-if="selectedId === t.id" class="tag sel">已选中</span>
        </div>
        <div class="tool-name">{{ t.name }}</div>
        <div class="tool-sub">{{ t.subtitle }}</div>
        <div class="tool-desc">{{ t.desc }}</div>
        <div class="tool-caps">
          <span v-for="c in t.capabilities" :key="c" class="cap">{{ c }}</span>
        </div>
        <div class="tool-foot">
          <span class="proto">{{ t.protocol }}</span>
          <span class="hint">{{ t.available ? '单击选中 · 双击直接运行' : '敬请期待' }}</span>
        </div>
      </article>
    </section>

    <!-- 示波器工作区 -->
    <section v-if="running === 'oscilloscope'" class="work">
      <div class="work-head">
        <div class="wk-left">
          <span class="wk-title">数字示波器</span>
          <span class="dot" :class="connected ? (state.mode === 'simulate' ? 'warn' : 'ok') : 'gray'"></span>
          <span class="wk-state">{{ connLabel }}</span>
          <span v-if="state.idn" class="wk-idn" :title="state.idn">{{ state.idn }}</span>
        </div>
        <div class="wk-right">
          <div class="mode">
            <button class="chip" :class="{ on: scopeMode === 'simulate' }" @click="scopeMode = 'simulate'">仿真信号源</button>
            <button class="chip" :class="{ on: scopeMode === 'real' }" @click="scopeMode = 'real'">真实仪器</button>
            <span v-if="simActive" class="sim-badge">仿真 · {{ simTypeLabel }}</span>
          </div>
          <button class="btn ghost" @click="frame" :disabled="!connected || busy">刷新波形</button>
          <button class="btn ghost" v-if="connected" @click="disconnect">断开</button>
        </div>
      </div>

      <!-- 绑定设备 -->
      <div class="bind-row">
        <label class="fld">
          <span>绑定设备（来自测试台注册 BOM）</span>
          <select v-model="bindSel.device_id" class="input" @change="bindSel.bench_id = (binds.find(b => b.device_id === bindSel.device_id) || {}).bench_id || ''">
            <optgroup v-if="virtualBind" label="内置仿真（无需硬件）">
              <option :value="virtualBind.device_id">{{ virtualBind.name }} · {{ virtualBind.model }}</option>
            </optgroup>
            <optgroup v-if="bindsGrouped.scope.length" label="示波器">
              <option v-for="b in bindsGrouped.scope" :key="b.bench_id + b.device_id" :value="b.device_id">
                {{ b.name }} · {{ b.model }} · {{ b.host || '未配置IP' }}:{{ b.port || '—' }} @{{ b.bench_title }}
              </option>
            </optgroup>
            <optgroup v-if="bindsGrouped.other.length" label="其他网口设备（非示波器）">
              <option v-for="b in bindsGrouped.other" :key="b.bench_id + b.device_id" :value="b.device_id">
                {{ b.name }} · {{ b.model }} · {{ b.host || '未配置IP' }}:{{ b.port || '—' }}
              </option>
            </optgroup>
          </select>
        </label>
        <div class="bind-info">
          <span v-if="!binds.length" class="warn-text">还没有可绑定设备：仿真模式下会自动使用<b>内置仿真信号源</b>；要连真机请先到「装备属性配置」确认设备，或在「测试台导航」注册测试台。</span>
          <span v-else>
            资源：<code>{{ (binds.find(b => b.device_id === bindSel.device_id) || {}).host || '—' }}:{{ (binds.find(b => b.device_id === bindSel.device_id) || {}).port || '—' }}</code>
            <span class="muted">· 泰克示波器常用 4000 端口 (SCPI socket)</span>
          </span>
        </div>
        <button class="btn" :disabled="busy" @click="quickSim">一键仿真</button>
        <button class="btn primary" :disabled="busy" @click="connect">
          {{ connected ? '重新连接' : (scopeMode === 'simulate' ? '进入仿真' : '连接并识别') }}
        </button>
      </div>

      <div v-if="notice.text" class="notice" :class="notice.kind">{{ notice.text }}</div>

      <!-- 控制区 + 波形区 -->
      <div class="scope-body">
        <!-- 左：控制 -->
        <div class="ctrl">
          <div class="blk">
            <div class="blk-title">采集</div>
            <div class="btn-row">
              <button class="btn" :class="{ on: state.running }" :disabled="!connected || busy" @click="runAcq">运行</button>
              <button class="btn" :class="{ on: !state.running && !state.single }" :disabled="!connected || busy" @click="stopAcq">停止</button>
              <button class="btn" :class="{ on: state.single }" :disabled="!connected || busy" @click="singleAcq">单次</button>
            </div>
            <button class="btn ghost wide" :disabled="!connected || busy" @click="autoset">自动设置 (AUTOSET)</button>
          </div>

          <div class="blk">
            <div class="blk-title">时基</div>
            <div class="tb-row">
              <button class="mini" :disabled="!connected || busy" @click="setTimebase(0.5)">÷2</button>
              <span class="tb-val mono">{{ fmtTime(state.timebase) }}<i>/div</i></span>
              <button class="mini" :disabled="!connected || busy" @click="setTimebase(2)">×2</button>
            </div>
            <div class="quick">
              <button v-for="v in [1e-6, 2e-6, 5e-6, 1e-5]" :key="v" class="mini" :disabled="!connected || busy" @click="setTimebaseValue(v)">
                {{ fmtTime(v) }}
              </button>
            </div>
          </div>

          <div class="blk">
            <div class="blk-title">通道</div>
            <div v-for="c in channels" :key="c.name" class="ch-row" :class="{ on: activeCh === c.name }">
              <button class="ch-name" @click="pickChannel(c.name)">{{ c.name }}</button>
              <button class="ch-sw" :class="{ on: c.display }" :disabled="!connected || busy" @click="toggleChannel(c.name)">
                {{ c.display ? 'ON' : 'OFF' }}
              </button>
              <select class="mini-sel" :value="c.coupling" :disabled="!connected || busy" @change="setChannelValue(c.name, 'coupling', $event.target.value)">
                <option value="DC">DC</option>
                <option value="AC">AC</option>
                <option value="GND">GND</option>
              </select>
              <input
                class="mini-num"
                type="number"
                step="0.1"
                min="0.01"
                :value="c.scale"
                :disabled="!connected || busy"
                @change="setChannelValue(c.name, 'scale', Number($event.target.value))"
              />
              <span class="ch-unit">V/div</span>
            </div>
          </div>

          <div class="blk">
            <div class="blk-title">显示通道</div>
            <div class="btn-row">
              <button class="btn ghost" @click="pickChannel(activeCh)">
                当前：{{ activeCh }}
              </button>
            </div>
            <div class="signal-row">
              {{ simActive ? '仿真信号源：' : '信号源：' }}
              <span class="mono">{{ simActive ? `${simTypeLabel} ${fmtFreq(simForm.freq)} · Vpp ${Number(simForm.vpp).toFixed(2)} V` : '仪器输入' }}</span>
            </div>
          </div>

          <div class="blk sim" :class="{ live: simActive }">
            <div class="blk-title">
              仿真信号源
              <span v-if="!simActive" class="sim-tip">点「一键仿真」即可用</span>
              <span v-else class="sim-tip ok">调整即刷新波形</span>
            </div>
            <div class="sq">
              <button
                v-for="s in SIM_TYPES"
                :key="s.id"
                class="chip"
                :class="{ on: simForm.type === s.id }"
                :disabled="!simActive || busy"
                @click="setSimType(s.id)"
              >{{ s.label }}</button>
            </div>
            <label class="sim-row">
              <span class="sr-k">频率</span>
              <input class="sr-range" type="range" min="0" max="15" step="1" :value="simFreqIdx" :disabled="!simActive || busy" @input="setSimFreq($event.target.value)" />
              <span class="sr-v mono">{{ fmtFreq(simForm.freq) }}</span>
            </label>
            <label class="sim-row">
              <span class="sr-k">幅度 Vpp</span>
              <input class="sr-range" type="range" min="0.1" max="10" step="0.1" :value="simForm.vpp" :disabled="!simActive || busy" @input="setSimNum('vpp', $event.target.value)" />
              <span class="sr-v mono">{{ Number(simForm.vpp).toFixed(2) }} V</span>
            </label>
            <label class="sim-row">
              <span class="sr-k">偏置</span>
              <input class="sr-range" type="range" min="-5" max="5" step="0.1" :value="simForm.offset" :disabled="!simActive || busy" @input="setSimNum('offset', $event.target.value)" />
              <span class="sr-v mono">{{ Number(simForm.offset).toFixed(1) }} V</span>
            </label>
            <label class="sim-row">
              <span class="sr-k">噪声</span>
              <input class="sr-range" type="range" min="0" max="0.5" step="0.01" :value="simForm.noise" :disabled="!simActive || busy" @input="setSimNum('noise', $event.target.value)" />
              <span class="sr-v mono">{{ Number(simForm.noise).toFixed(2) }}</span>
            </label>
            <div class="sim-row">
              <span class="sr-k">屏内周期</span>
              <div class="sq">
                <button
                  v-for="c in [1, 2, 5, 10]"
                  :key="c"
                  class="chip"
                  :class="{ on: Number(simForm.cycles) === c }"
                  :disabled="!simActive || busy"
                  @click="setSimNum('cycles', c, true)"
                >{{ c }}</button>
              </div>
              <span class="sr-v mono">{{ fmtTime(state.timebase) }}/div</span>
            </div>
            <div class="sim-note">
              波形由后端按该信号源算法生成（与真实取波形同构），不冒充实测；切到「真实仪器」后这些参数不影响实测。
            </div>
          </div>
        </div>

        <!-- 右：波形 + 测量 -->
        <div class="viz">
          <canvas ref="canvas" class="screen"></canvas>
          <div class="meas">
            <div v-for="m in measures" :key="m.type" class="meas-card" :class="{ bad: m.value === null || m.value === undefined }">
              <span class="mk">{{ MEAS_LABEL[m.type] || m.type }}</span>
              <b>{{ fmtVal(m) }}</b>
              <span class="msrc" :class="m.source">{{ m.source === 'instrument' ? '实测' : m.source === 'simulate' ? '模拟' : '无效' }}</span>
            </div>
          </div>
          <div class="log-head">SCPI 命令日志<span class="muted">（最近 {{ state.log.length }} 条）</span></div>
          <div ref="logBox" class="log">
            <div v-for="(l, i) in state.log" :key="i" class="log-row" :class="l.dir">
              <span class="log-dir">{{ l.dir === 'tx' ? '→' : l.dir === 'rx' ? '←' : '·' }}</span>
              <span class="log-txt">{{ l.text }}</span>
            </div>
            <div v-if="!state.log.length" class="muted pad">连接后这里会记录每条 SCPI 命令与仪器应答</div>
          </div>
        </div>
      </div>
    </section>

    <!-- 未运行提示 -->
    <section v-else class="placeholder">
      选中卡片后点右上角「运行工具」启动。首期已支持<b>数字示波器</b>（泰克 MSO54 方案），
      支持离线模拟演示：无仪器也能看到波形、测量值与完整操作流程。
    </section>
  </div>
</template>

<style scoped>
.as-page { flex: 1; padding: 18px 22px 26px; display: flex; flex-direction: column; gap: 14px; overflow-y: auto; }

/* ---- 头部 ---- */
.as-head {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 16px 18px; border-top: 3px solid var(--accent);
}
.hd-title { font-size: 19px; font-weight: 800; }
.hd-sub { color: var(--muted); font-size: 12.5px; margin-top: 4px; }
.hd-right { display: flex; align-items: center; gap: 10px; }
.sel-chip { font-size: 12.5px; color: var(--muted); }
.sel-chip b { color: var(--accent); }
.run-btn { padding: 9px 18px; font-weight: 700; }

/* ---- 工具卡片 ---- */
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(258px, 1fr)); gap: 14px; }
.tool {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 14px; display: flex; flex-direction: column; gap: 8px; cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s; position: relative;
}
.tool:hover { transform: translateY(-2px); border-color: rgba(var(--accent-rgb), 0.45); }
.tool.on { border-color: var(--accent); box-shadow: 0 8px 22px rgba(var(--accent-rgb), 0.14); }
.tool.on::after {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
  background: var(--accent); border-radius: 12px 0 0 12px;
}
.tool.off { opacity: 0.72; }
.tool.running { border-color: rgba(var(--accent-rgb), 0.6); background: linear-gradient(180deg, rgba(var(--accent-rgb), 0.05), #fff); }
.tool-top { display: flex; align-items: center; gap: 8px; }
.tool-icon {
  width: 34px; height: 34px; border-radius: 9px; flex: 0 0 34px;
  background: linear-gradient(135deg, #8b5cf6, #5b21b6); position: relative;
  box-shadow: 0 3px 10px rgba(var(--accent-rgb), 0.28);
}
.tool-icon::after { content: ''; position: absolute; inset: 9px 6px; border-radius: 2px; }
.ic-wave::after { background: none; border-bottom: 2px solid #fff; border-radius: 0; height: 12px; top: 8px; }
.ic-meter::after { border: 2px solid #fff; border-radius: 50%; inset: 10px; }
.ic-power::after { background: #fff; border-radius: 50%; inset: 13px; }
.ic-load::after { background: #fff; clip-path: polygon(0 100%, 50% 0, 100% 100%); inset: 9px 7px; }
.tag {
  font-size: 11px; padding: 1px 7px; border-radius: 999px; color: var(--muted);
  border: 1px solid var(--border); background: var(--panel-2);
}
.tag.ok { color: var(--ok); border-color: rgba(21, 128, 61, 0.35); background: rgba(21, 128, 61, 0.09); }
.tag.sel { margin-left: auto; color: var(--accent); border-color: rgba(var(--accent-rgb), 0.4); background: rgba(var(--accent-rgb), 0.1); font-weight: 700; }
.tool-name { font-size: 15.5px; font-weight: 800; }
.tool-sub { font-size: 11.5px; color: var(--accent); margin-top: -4px; }
.tool-desc { font-size: 12.5px; color: var(--muted); line-height: 1.55; }
.tool-caps { display: flex; flex-wrap: wrap; gap: 5px; }
.cap { font-size: 11px; color: var(--muted); border: 1px dashed var(--border-strong); border-radius: 5px; padding: 1px 6px; }
.tool-foot { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--border); padding-top: 7px; margin-top: 2px; }
.proto { font-family: var(--mono); font-size: 10.5px; color: var(--muted); }
.hint { font-size: 11px; color: var(--accent); }

/* ---- 工作区 ---- */
.work { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 12px; }
.work-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.wk-left { display: flex; align-items: center; gap: 8px; min-width: 0; }
.wk-title { font-size: 15px; font-weight: 800; }
.wk-state { font-size: 12.5px; color: var(--muted); }
.wk-idn { font-family: var(--mono); font-size: 11px; color: var(--muted); max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wk-right { display: flex; align-items: center; gap: 8px; }
.mode { display: flex; gap: 4px; }
.chip { padding: 5px 10px; border-radius: 999px; font-size: 12px; cursor: pointer; background: var(--panel); border: 1px solid var(--border); color: var(--muted); }
.chip.on { background: rgba(var(--accent-rgb), 0.12); border-color: rgba(var(--accent-rgb), 0.5); color: var(--accent); font-weight: 700; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }
.dot.ok { background: var(--ok); box-shadow: 0 0 0 3px rgba(21, 128, 61, 0.16); }
.dot.warn { background: var(--warn); box-shadow: 0 0 0 3px rgba(180, 83, 9, 0.16); }
.dot.gray { background: #a8a3bd; }

.bind-row { display: grid; grid-template-columns: minmax(280px, 1.4fr) minmax(220px, 1fr) auto; gap: 12px; align-items: end; border: 1px solid var(--border); border-radius: 10px; padding: 11px 12px; background: var(--panel-2); }
.fld { display: flex; flex-direction: column; gap: 5px; }
.fld span { font-size: 11.5px; color: var(--muted); }
.bind-info { font-size: 12px; color: var(--text); }
.bind-info code { font-family: var(--mono); background: #fff; border: 1px solid var(--border); border-radius: 5px; padding: 1px 6px; }
.muted { color: var(--muted); }
.warn-text { color: var(--warn); font-size: 12px; }
.notice { border-radius: 9px; padding: 9px 12px; font-size: 12.5px; border: 1px solid rgba(var(--accent-rgb), 0.35); background: rgba(var(--accent-rgb), 0.07); }
.notice.warn { border-color: rgba(180, 83, 9, 0.4); background: rgba(180, 83, 9, 0.08); color: var(--warn); }

.scope-body { display: grid; grid-template-columns: minmax(220px, 268px) 1fr; gap: 14px; }
.ctrl { display: flex; flex-direction: column; gap: 10px; }
.blk { border: 1px solid var(--border); border-radius: 10px; padding: 10px 11px; display: flex; flex-direction: column; gap: 8px; }
.blk-title { font-size: 11.5px; letter-spacing: 1px; color: var(--accent); font-weight: 700; }
.btn-row { display: flex; gap: 6px; }
.btn-row .btn { flex: 1; padding: 7px 8px; font-size: 12.5px; }
.btn.on { border-color: var(--accent); color: var(--accent); background: rgba(var(--accent-rgb), 0.1); font-weight: 700; }
.btn.ghost.wide { width: 100%; font-size: 12.5px; padding: 7px 8px; }
.tb-row { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.tb-val { font-size: 13px; font-weight: 700; color: var(--accent); }
.tb-val i { font-style: normal; color: var(--muted); font-size: 11px; }
.mini { padding: 4px 8px; border-radius: 6px; border: 1px solid var(--border); background: #fff; font-size: 11.5px; cursor: pointer; color: var(--text); }
.mini:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.mini:disabled { opacity: 0.5; cursor: not-allowed; }
.quick { display: flex; gap: 4px; flex-wrap: wrap; }
.ch-row { display: flex; align-items: center; gap: 5px; padding: 4px 5px; border-radius: 8px; border: 1px solid transparent; }
.ch-row.on { border-color: rgba(var(--accent-rgb), 0.4); background: rgba(var(--accent-rgb), 0.06); }
.ch-name { width: 38px; border: none; background: var(--panel-2); border-radius: 5px; font-family: var(--mono); font-size: 11.5px; padding: 3px 0; cursor: pointer; color: var(--text); }
.ch-sw { border: 1px solid var(--border); background: #fff; border-radius: 5px; font-size: 10.5px; padding: 3px 6px; cursor: pointer; color: var(--muted); font-family: var(--mono); }
.ch-sw.on { color: var(--ok); border-color: rgba(21, 128, 61, 0.4); background: rgba(21, 128, 61, 0.09); font-weight: 700; }
.mini-sel { border: 1px solid var(--border); border-radius: 5px; font-size: 11px; padding: 2px 3px; background: #fff; }
.mini-num { width: 52px; border: 1px solid var(--border); border-radius: 5px; font-size: 11px; padding: 2px 4px; font-family: var(--mono); }
.ch-unit { font-size: 10.5px; color: var(--muted); }
.signal-row { font-size: 11.5px; color: var(--text); }

.viz { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.screen {
  width: 100%; height: 300px; border-radius: 10px;
  border: 1px solid var(--border-strong); background: #fff;
  background-image:
    linear-gradient(rgba(var(--accent-rgb), 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(var(--accent-rgb), 0.05) 1px, transparent 1px);
  background-size: 22px 22px;
}
.meas { display: grid; grid-template-columns: repeat(auto-fit, minmax(122px, 1fr)); gap: 8px; }
.meas-card {
  position: relative; border: 1px solid var(--border); border-radius: 9px; padding: 8px 10px;
  background: var(--panel-2); display: flex; flex-direction: column; gap: 2px;
}
.meas-card.bad { border-color: rgba(220, 38, 38, 0.35); background: rgba(220, 38, 38, 0.05); }
.mk { font-size: 11px; color: var(--muted); }
.meas-card b { font-family: var(--mono); font-size: 15px; color: var(--accent); }
.msrc { position: absolute; right: 7px; top: 7px; font-size: 9.5px; color: var(--muted); border: 1px solid var(--border); border-radius: 999px; padding: 0 5px; }
.msrc.instrument { color: var(--ok); border-color: rgba(21, 128, 61, 0.35); }
.msrc.simulate { color: var(--warn); border-color: rgba(180, 83, 9, 0.35); }
.msrc.error { color: var(--err); border-color: rgba(220, 38, 38, 0.35); }
.log-head { font-size: 11.5px; color: var(--accent); font-weight: 700; letter-spacing: 0.5px; }
.log {
  height: 148px; overflow-y: auto; border: 1px solid var(--border); border-radius: 9px;
  background: #faf9ff; padding: 8px 10px; font-family: var(--mono); font-size: 11.5px;
}
.log-row { display: flex; gap: 7px; padding: 1px 0; }
.log-dir { color: var(--muted); width: 10px; flex: 0 0 10px; }
.log-txt { word-break: break-all; }
.log-row.tx .log-dir, .log-row.tx .log-txt { color: var(--accent); }
.log-row.rx .log-dir, .log-row.rx .log-txt { color: #15803d; }
.log-row.info .log-txt { color: var(--muted); }
.log-row.warn .log-txt { color: var(--warn); }
.log-row.err .log-txt { color: var(--err); }
.pad { padding: 6px 0; }

.placeholder {
  background: var(--panel); border: 1px dashed var(--border-strong); border-radius: 12px;
  padding: 18px 20px; color: var(--muted); font-size: 13px;
}
.placeholder b { color: var(--accent); }
.tip { color: var(--muted); background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; font-size: 13px; }
.tip.err { color: var(--err); background: rgba(220, 38, 38, 0.08); border-color: rgba(220, 38, 38, 0.3); }

@media (max-width: 1080px) {
  .scope-body { grid-template-columns: 1fr; }
  .bind-row { grid-template-columns: 1fr; }
}

/* ---- 仿真信号源 ---- */
.sim-badge {
  font-size: 11.5px; padding: 2px 9px; border-radius: 999px;
  background: rgba(180, 83, 9, 0.1); color: var(--warn); border: 1px solid rgba(180, 83, 9, 0.35);
}
.blk.sim { border-left: 3px solid var(--border-strong); }
.blk.sim.live { border-left-color: var(--accent); background: rgba(var(--accent-rgb), 0.04); }
.sim-tip { font-size: 11px; color: var(--muted); font-weight: 400; margin-left: 6px; }
.sim-tip.ok { color: var(--ok); }
.sq { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.sim-row { display: flex; align-items: center; gap: 8px; margin-top: 7px; }
.sr-k { width: 60px; font-size: 12px; color: var(--muted); white-space: nowrap; }
.sr-range { flex: 1; min-width: 0; height: 18px; accent-color: var(--accent); }
.sr-range:disabled { opacity: 0.55; }
.sr-v { width: 84px; text-align: right; font-size: 11.5px; }
.sim-note { margin-top: 9px; font-size: 11px; color: var(--muted); line-height: 1.55; }
</style>
