<script setup>
/**
 * 工业相机装备助手工作区（Basler pylon 8.0.0）
 *  - 参数设置：采集几何 / 像质 / 采集节奏与触发 / 仿真场景（表单由后端 param_groups 驱动）
 *  - 曝光控制：绝对值、倍数微调、预设档位、按目标亮度自动曝光
 *  - 数据保存：单帧抓取即落盘、连拍序列落盘、按业务命名导出归档、帧预览与下载
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const emit = defineEmits(['close'])

const info = ref(null)
const key = ref('')
const mode = ref('simulate')
const serial = ref('')
const connected = ref(false)
const busy = ref(false)
const state = reactive({
  idn: '', opened_at: '', setup: {}, device: {}, last_stats: {}, warnings: [], pylon: {}, fps_frames: 0
})
const form = reactive({})
const exposureUs = ref(5000)
const targetMean = ref(118)
const seqCount = ref(8)
const seqInterval = ref(200)
const exportName = ref('')
const frames = ref([])
const preview = ref('')
const lastFrame = ref(null)
const logs = ref([])
const notice = ref({ text: '', kind: 'ok' })

const exposurePresets = computed(() => (info.value && info.value.exposure_presets) || [])
const groups = computed(() => {
  const list = (info.value && info.value.param_groups) || []
  return list.filter((g) => !g.sim_only || mode.value === 'simulate')
})
const stats = computed(() => {
  const s = (lastFrame.value && lastFrame.value.stats) || state.last_stats || {}
  return [
    { k: 'mean', label: '平均灰度', v: fmt(s.mean, 2) },
    { k: 'min', label: '最小', v: fmt(s.min, 0) },
    { k: 'max', label: '最大', v: fmt(s.max, 0) },
    { k: 'std', label: '标准差', v: fmt(s.std, 2) },
    { k: 'saturated', label: '过曝比', v: s.saturated === undefined ? '—' : `${(s.saturated * 100).toFixed(2)} %` }
  ]
})
const connLabel = computed(() => {
  if (!connected.value) return '未连接'
  return mode.value === 'real' ? '真实相机已连接' : '仿真相机（确定性合成帧）'
})
const exposureText = computed(() => {
  const us = Number(state.setup.exposure_us || exposureUs.value || 0)
  if (!us) return '—'
  return us >= 1000 ? `${(us / 1000).toFixed(us >= 10000 ? 0 : 2)} ms` : `${us.toFixed(0)} µs`
})

function fmt(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(digits)
}

function flash(text, kind = 'ok') {
  notice.value = { text, kind }
  logs.value.unshift({ at: new Date().toLocaleTimeString('zh-CN', { hour12: false }), text, kind })
  logs.value = logs.value.slice(0, 60)
  setTimeout(() => { if (notice.value.text === text) notice.value = { text: '', kind: 'ok' } }, 4200)
}

function applyState(s) {
  if (!s) return
  key.value = s.key || key.value
  state.idn = s.idn || ''
  state.opened_at = s.opened_at || ''
  state.setup = s.setup || {}
  state.device = s.device || {}
  state.last_stats = s.last_stats || {}
  state.warnings = s.warnings || []
  state.pylon = s.pylon || {}
  state.fps_frames = s.fps_frames || 0
  connected.value = !!s.opened
  if (s.last_frame) lastFrame.value = s.last_frame
  syncForm(state.setup)
}

function syncForm(setup) {
  Object.keys(setup || {}).forEach((k) => { form[k] = setup[k] })
  if (setup && setup.exposure_us !== undefined) exposureUs.value = Number(setup.exposure_us)
}

/* ---------------- 加载 ---------------- */

async function loadInfo() {
  try {
    info.value = await api.cameraInfo()
  } catch (e) {
    flash(`读取相机能力清单失败：${e.message}`, 'warn')
  }
}

async function loadFrames() {
  try {
    const res = await api.cameraFrames(12)
    frames.value = (res && res.items) || []
  } catch (e) {
    frames.value = []
  }
}

onMounted(async () => {
  await Promise.all([loadInfo(), loadFrames()])
})

/* ---------------- 连接 ---------------- */

async function connect() {
  busy.value = true
  try {
    const res = await api.connectCamera({ mode: mode.value, serial: serial.value })
    if (!res || res.success === false) { flash((res && res.message) || '连接失败', 'warn'); return }
    applyState(res.state)
    flash(res.message)
    await grab(true)
  } catch (e) {
    flash(`连接失败：${e.message}`, 'warn')
  } finally {
    busy.value = false
  }
}

async function disconnect() {
  try {
    const res = await api.disconnectCamera({ key: key.value })
    if (!res || res.success === false) { flash((res && res.message) || '断开失败', 'warn'); return }
    connected.value = false
    preview.value = ''
    flash(res.message)
  } catch (e) {
    flash(`断开失败：${e.message}`, 'warn')
  }
}

/* ---------------- 参数与曝光 ---------------- */

async function applyParams() {
  if (!connected.value) { flash('请先连接相机', 'warn'); return }
  const payload = {}
  Object.keys(form).forEach((k) => { payload[k] = form[k] })
  busy.value = true
  try {
    const res = await api.cameraParams({ key: key.value, params: payload })
    if (!res || res.success === false) { flash((res && res.message) || '参数设置失败', 'warn'); return }
    applyState(res.state)
    flash(res.message, res.warnings && res.warnings.length ? 'warn' : 'ok')
    await grab(true)
  } catch (e) {
    flash(`参数设置失败：${e.message}`, 'warn')
  } finally {
    busy.value = false
  }
}

async function resetParams() {
  busy.value = true
  try {
    const res = await api.cameraParams({ key: key.value, reset: true })
    if (!res || res.success === false) { flash((res && res.message) || '恢复默认失败', 'warn'); return }
    applyState(res.state)
    flash(res.message)
    await grab(true)
  } finally {
    busy.value = false
  }
}

async function setExposure(us) {
  if (!connected.value) { flash('请先连接相机', 'warn'); return }
  busy.value = true
  try {
    const res = await api.cameraExposure({ key: key.value, exposure_us: Number(us) })
    if (!res || res.success === false) { flash((res && res.message) || '曝光设置失败', 'warn'); return }
    applyState(res.state)
    flash(res.message)
    await grab(true)
  } finally {
    busy.value = false
  }
}

async function scaleExposure(scale) {
  if (!connected.value) { flash('请先连接相机', 'warn'); return }
  const res = await api.cameraExposure({ key: key.value, scale })
  if (!res || res.success === false) { flash((res && res.message) || '曝光调整失败', 'warn'); return }
  applyState(res.state)
  flash(res.message)
  await grab(true)
}

async function autoExpose() {
  if (!connected.value) { flash('请先连接相机', 'warn'); return }
  busy.value = true
  try {
    const res = await api.cameraAutoExposure({ key: key.value, target_mean: Number(targetMean.value), max_rounds: 6 })
    if (!res || res.success === false) { flash((res && res.message) || '自动曝光失败', 'warn'); return }
    applyState(res.state)
    flash(res.message, res.result && res.result.converged ? 'ok' : 'warn')
    if (res.result && res.result.trace) {
      logs.value.unshift({
        at: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
        text: '自动曝光过程：' + res.result.trace.map((t) => `${t.exposure_us}µs→${t.mean}`).join(' · '),
        kind: 'info'
      })
    }
    await grab(true)
  } finally {
    busy.value = false
  }
}

/* ---------------- 采集与保存 ---------------- */

async function grab(silent = false) {
  if (!connected.value) { if (!silent) flash('请先连接相机', 'warn'); return }
  busy.value = true
  try {
    const res = await api.cameraGrab({ key: key.value, note: exportName.value })
    if (!res || res.success === false) { flash((res && res.message) || '抓取失败', 'warn'); return }
    lastFrame.value = res.frame
    preview.value = `${res.frame.url}&t=${Date.now()}`
    if (res.state) applyState(res.state)
    if (!silent) flash(res.message)
    await loadFrames()
  } catch (e) {
    if (!silent) flash(`抓取失败：${e.message}`, 'warn')
  } finally {
    busy.value = false
  }
}

async function runSequence() {
  if (!connected.value) { flash('请先连接相机', 'warn'); return }
  busy.value = true
  try {
    const res = await api.cameraSequence({
      key: key.value, count: Number(seqCount.value), interval_ms: Number(seqInterval.value),
      prefix: exportName.value || 'seq'
    })
    if (!res || res.success === false) { flash((res && res.message) || '连拍失败', 'warn'); return }
    flash(res.message)
    if (res.result && res.result.frames && res.result.frames.length) {
      const last = res.result.frames[res.result.frames.length - 1]
      preview.value = `${last.url}&t=${Date.now()}`
    }
    await loadFrames()
  } finally {
    busy.value = false
  }
}

async function exportFrame() {
  if (!lastFrame.value && !preview.value) { flash('请先抓取一帧再导出', 'warn'); return }
  const res = await api.cameraSave({
    key: key.value,
    path: (lastFrame.value && lastFrame.value.path) || '',
    name: exportName.value,
    subdir: 'exports'
  })
  if (!res || res.success === false) { flash((res && res.message) || '导出失败', 'warn'); return }
  flash(res.message)
  await loadFrames()
}

function pickFrame(f) {
  preview.value = `${f.url}&t=${Date.now()}`
  lastFrame.value = { ...(lastFrame.value || {}), name: f.name, path: f.path, url: f.url, bytes: f.bytes }
}

function download(f) {
  window.open(`${f.url}${f.url.includes('?') ? '&' : '?'}download=1`, '_blank')
}
</script>

<template>
  <section class="cam">
    <!-- 头部 -->
    <div class="cam-head">
      <div class="cam-left">
        <span class="cam-title">工业相机</span>
        <span class="dot" :class="connected ? (mode === 'real' ? 'ok' : 'warn') : 'gray'"></span>
        <span class="cam-state">{{ connLabel }}</span>
        <span v-if="state.idn" class="cam-idn" :title="state.idn">{{ state.idn }}</span>
      </div>
      <div class="cam-right">
        <div class="seg">
          <button class="chip" :class="{ on: mode === 'simulate' }" @click="mode = 'simulate'">仿真</button>
          <button class="chip" :class="{ on: mode === 'real' }" @click="mode = 'real'">真实相机</button>
        </div>
        <input v-model="serial" class="inp sn" placeholder="序列号（可空）" :disabled="connected" />
        <button v-if="!connected" class="btn primary sm" :disabled="busy" @click="connect">连接</button>
        <button v-else class="btn ghost sm" @click="disconnect">断开</button>
        <button class="btn ghost sm" @click="emit('close')">关闭</button>
      </div>
    </div>

    <div v-if="notice.text" class="notice" :class="notice.kind">{{ notice.text }}</div>

    <div class="cam-body">
      <!-- 参数设置 -->
      <aside class="col params">
        <div class="col-head">
          <span>参数设置</span>
          <span class="hint" v-if="state.pylon">pylon {{ state.pylon.baseline || '8.0.0' }}<template v-if="state.pylon.pypylon"> · {{ state.pylon.pypylon }}</template></span>
        </div>
        <div class="col-scroll">
          <div v-for="g in groups" :key="g.key" class="pgroup">
            <div class="pg-title">{{ g.label }}</div>
            <label v-for="it in g.items" :key="it.key" class="prow">
              <span class="plabel">{{ it.label }}<i v-if="it.unit">（{{ it.unit }}）</i></span>
              <select v-if="it.type === 'select'" v-model="form[it.key]" class="inp">
                <option v-for="o in it.options" :key="o" :value="o">{{ o }}</option>
              </select>
              <input
                v-else
                v-model.number="form[it.key]"
                type="number"
                class="inp"
                :min="it.min"
                :max="it.max"
                :step="it.step || 1"
              />
            </label>
          </div>
        </div>
        <div class="col-foot">
          <button class="btn primary sm" :disabled="!connected || busy" @click="applyParams">应用参数</button>
          <button class="btn ghost sm" :disabled="!connected || busy" @click="resetParams">恢复默认</button>
        </div>
      </aside>

      <!-- 预览与采集 -->
      <main class="col view">
        <div class="col-head">
          <span>帧预览</span>
          <span class="hint" v-if="lastFrame">{{ lastFrame.width }}×{{ lastFrame.height }} · {{ lastFrame.pixel_format }} · {{ (lastFrame.bytes / 1024).toFixed(1) }} KB</span>
        </div>
        <div class="preview">
          <img v-if="preview" :src="preview" alt="相机帧" />
          <div v-else class="ph">连接相机后自动抓取首帧；无硬件时用「仿真」模式</div>
        </div>
        <div class="statbar">
          <span v-for="s in stats" :key="s.k" class="stat">{{ s.label }}<b>{{ s.v }}</b></span>
          <span class="stat">已采集<b>{{ state.fps_frames }}</b>帧</span>
        </div>
        <div class="actions">
          <button class="btn primary sm" :disabled="!connected || busy" @click="grab(false)">抓取一帧</button>
          <span class="sep"></span>
          <span class="alabel">连拍</span>
          <input v-model.number="seqCount" type="number" min="1" max="200" class="inp tiny" />
          <span class="alabel">帧，间隔</span>
          <input v-model.number="seqInterval" type="number" min="0" max="10000" step="50" class="inp tiny" />
          <span class="alabel">ms</span>
          <button class="btn ghost sm" :disabled="!connected || busy" @click="runSequence">开始连拍</button>
          <span class="sep"></span>
          <input v-model="exportName" class="inp name" placeholder="导出名（如 PN1234-AOI）" />
          <button class="btn ghost sm" :disabled="!lastFrame" @click="exportFrame">导出归档</button>
        </div>
      </main>

      <!-- 曝光与帧列表 -->
      <aside class="col side">
        <div class="col-head"><span>曝光控制</span><span class="hint">{{ exposureText }}</span></div>
        <div class="exp-box">
          <div class="presets">
            <button
              v-for="p in exposurePresets"
              :key="p.us"
              class="preset"
              :class="{ on: Math.abs(Number(state.setup.exposure_us) - p.us) < 0.5 }"
              :disabled="!connected || busy"
              @click="setExposure(p.us)"
            >{{ p.label }}</button>
          </div>
          <div class="exp-row">
            <input v-model.number="exposureUs" type="number" min="10" max="60000000" step="100" class="inp" />
            <span class="alabel">µs</span>
            <button class="btn ghost sm" :disabled="!connected || busy" @click="setExposure(exposureUs)">设定</button>
          </div>
          <div class="exp-row">
            <button class="btn ghost sm" :disabled="!connected || busy" @click="scaleExposure(0.5)">曝光 ÷2</button>
            <button class="btn ghost sm" :disabled="!connected || busy" @click="scaleExposure(2)">曝光 ×2</button>
          </div>
          <div class="exp-row">
            <span class="alabel">目标灰度</span>
            <input v-model.number="targetMean" type="number" min="1" max="254" class="inp tiny" />
            <button class="btn primary sm" :disabled="!connected || busy" @click="autoExpose">自动曝光</button>
          </div>
        </div>

        <div class="col-head"><span>最近帧</span><span class="hint">共 {{ frames.length }} 个</span></div>
        <div class="frames">
          <article v-for="f in frames" :key="f.path" class="fitem" @click="pickFrame(f)">
            <img :src="`${f.url}`" :alt="f.name" loading="lazy" />
            <div class="fmeta">
              <div class="fname" :title="f.name">{{ f.name }}</div>
              <div class="fsub">{{ (f.bytes / 1024).toFixed(1) }} KB · {{ f.mtime.slice(11) }}</div>
            </div>
            <button class="dl" title="下载" @click.stop="download(f)">↓</button>
          </article>
          <div v-if="!frames.length" class="ph small">还没有采集记录</div>
        </div>
      </aside>
    </div>

    <!-- 日志 -->
    <div class="logbar">
      <span class="log-title">操作记录</span>
      <span v-if="!logs.length" class="muted">连接、参数、曝光、采集与导出的每一步都会记录在这里</span>
      <span v-for="(l, i) in logs.slice(0, 6)" :key="i" class="log-item" :class="l.kind">{{ l.at }} {{ l.text }}</span>
    </div>
  </section>
</template>

<style scoped>
.cam { border: 1px solid var(--border); border-radius: 12px; background: var(--panel); display: flex; flex-direction: column; overflow: hidden; }
.cam-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--border); background: var(--panel-2); flex-wrap: wrap; }
.cam-left, .cam-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cam-title { font-size: 14px; font-weight: 800; }
.cam-state { font-size: 12px; color: var(--muted); }
.cam-idn { font-family: var(--mono); font-size: 11px; color: var(--border-strong); max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border-strong); }
.dot.ok { background: var(--ok); }
.dot.warn { background: var(--warn); }
.dot.gray { background: var(--border-strong); }
.seg { display: flex; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; }
.chip { font-size: 11.5px; padding: 4px 10px; border: none; background: transparent; color: var(--muted); cursor: pointer; }
.chip.on { background: rgba(var(--accent-rgb), 0.12); color: var(--accent); font-weight: 700; }
.inp { padding: 4px 8px; font-size: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel); color: inherit; font-family: inherit; }
.inp:focus { outline: none; border-color: rgba(var(--accent-rgb), 0.5); }
.inp.sn { width: 130px; }
.inp.tiny { width: 68px; }
.inp.name { width: 176px; }
.btn.sm { padding: 4px 10px; font-size: 12px; }
.notice { padding: 6px 14px; font-size: 12px; border-bottom: 1px solid var(--border); }
.notice.ok { color: var(--ok); background: rgba(21, 128, 61, 0.06); }
.notice.warn { color: var(--warn); background: rgba(180, 83, 9, 0.08); }

.cam-body { display: grid; grid-template-columns: 262px minmax(320px, 1fr) 296px; min-height: 420px; }
.col { display: flex; flex-direction: column; min-width: 0; }
.col + .col { border-left: 1px solid var(--border); }
.col-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 12px; border-bottom: 1px solid var(--border); font-size: 12px; font-weight: 700; }
.hint { font-size: 11px; color: var(--muted); font-weight: 500; }
.col-scroll { flex: 1; overflow-y: auto; padding: 6px 10px 8px; max-height: 420px; }
.col-foot { display: flex; gap: 6px; padding: 8px 10px; border-top: 1px solid var(--border); }
.pgroup { margin-bottom: 8px; }
.pg-title { font-size: 11px; font-weight: 700; color: var(--accent); padding: 6px 0 3px; }
.prow { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 2px 0; }
.plabel { font-size: 11.5px; color: var(--muted); }
.plabel i { font-style: normal; color: var(--border-strong); }
.prow .inp { width: 108px; text-align: right; }

.preview { flex: 1; display: flex; align-items: center; justify-content: center; background: #0f0f12; min-height: 240px; padding: 8px; }
.preview img { max-width: 100%; max-height: 300px; image-rendering: pixelated; }
.ph { color: #8a8a95; font-size: 12px; text-align: center; padding: 20px; }
.ph.small { padding: 10px; font-size: 11.5px; }
.statbar { display: flex; gap: 14px; flex-wrap: wrap; padding: 7px 12px; border-top: 1px solid var(--border); font-size: 11.5px; color: var(--muted); }
.stat b { color: var(--accent); font-family: var(--mono); margin-left: 5px; }
.actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 8px 12px; border-top: 1px solid var(--border); }
.alabel { font-size: 11.5px; color: var(--muted); }
.sep { width: 1px; height: 18px; background: var(--border); margin: 0 4px; }

.exp-box { padding: 8px 10px; display: flex; flex-direction: column; gap: 7px; border-bottom: 1px solid var(--border); }
.presets { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; }
.preset { font-size: 11px; padding: 4px 2px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel-2); color: var(--muted); cursor: pointer; }
.preset.on { border-color: rgba(var(--accent-rgb), 0.5); color: var(--accent); font-weight: 700; background: rgba(var(--accent-rgb), 0.08); }
.preset:disabled { opacity: 0.5; cursor: not-allowed; }
.exp-row { display: flex; align-items: center; gap: 6px; }
.exp-row .inp { flex: 1; min-width: 0; }
.frames { flex: 1; overflow-y: auto; padding: 6px; display: flex; flex-direction: column; gap: 4px; max-height: 260px; }
.fitem { display: flex; align-items: center; gap: 8px; padding: 4px 6px; border: 1px solid transparent; border-radius: 6px; cursor: pointer; }
.fitem:hover { background: var(--panel-2); border-color: var(--border); }
.fitem img { width: 46px; height: 36px; object-fit: cover; background: #0f0f12; border-radius: 3px; image-rendering: pixelated; }
.fmeta { flex: 1; min-width: 0; }
.fname { font-size: 11.5px; font-family: var(--mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fsub { font-size: 10.5px; color: var(--muted); }
.dl { border: 1px solid var(--border); background: var(--panel-2); border-radius: 5px; font-size: 11px; padding: 1px 6px; cursor: pointer; color: var(--muted); }
.logbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 7px 12px; border-top: 1px solid var(--border); background: var(--panel-2); font-size: 11px; color: var(--muted); }
.log-title { font-weight: 700; color: var(--accent); }
.log-item { font-family: var(--mono); }
.log-item.warn { color: var(--warn); }
.log-item.info { color: var(--border-strong); }
.muted { color: var(--border-strong); }
</style>
