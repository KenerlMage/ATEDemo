<script setup>
/**
 * 装备管理模块 · 连接管理
 *  - 左侧：可连接设备（来自注册表投影），可在此建立/释放连接
 *  - 中间：已建立的连接（DM 持有），显示句柄、占用者、调用次数、空闲时长
 *  - 右侧：模块状态（进程、管道、共享内存）与请求日志
 *
 * 口径：所有连接由装备管理模块持有，本页只是操作界面；关掉本页不影响连接。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

const status = ref({ running: false, endpoint: {}, stats: null, devices: [], sessions: [], error: '' })
const catalog = ref({ bench_id: '', benches: [], available: [], connected: [] })
const logLines = ref([])
const busy = ref(false)
const notice = ref({ text: '', kind: 'ok' })
const autoRefresh = ref(true)
const mode = ref('simulate')
const keyword = ref('')
let timer = null

const connectedMap = computed(() => {
  const map = {}
  for (const slot of status.value.devices || []) {
    map[slot.key] = slot
    if (slot.device_id) map[slot.device_id] = slot
    if (slot.alias) map[slot.alias] = slot
  }
  return map
})

const availableRows = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return (catalog.value.available || []).filter((d) => {
    if (!kw) return true
    return `${d.device_id} ${d.name} ${d.model} ${d.device_type} ${d.address}`.toLowerCase().includes(kw)
  })
})

const sessions = computed(() => status.value.sessions || [])
const ring = computed(() => (status.value.stats && status.value.stats.ring) || {})
const stats = computed(() => status.value.stats || {})

function flash(text, kind = 'ok') {
  notice.value = { text, kind }
  setTimeout(() => { if (notice.value.text === text) notice.value = { text: '', kind: 'ok' } }, 4200)
}

function fmtBytes(n) {
  const v = Number(n || 0)
  if (v >= 1 << 20) return `${(v / (1 << 20)).toFixed(2)} MB`
  if (v >= 1024) return `${(v / 1024).toFixed(1)} KB`
  return `${v} B`
}

function ownerOf(row) {
  const hit = connectedMap.value[row.device_id] || connectedMap.value[row.alias]
  return hit ? hit : null
}

async function loadStatus() {
  try {
    const res = await api.dmStatus()
    status.value = res || {}
    if (!status.value.running) {
      status.value = { ...status.value, error: status.value.error || '尚未启动装备管理模块' }
    }
  } catch (e) {
    status.value = { running: false, error: `读取状态失败：${e.message}`, devices: [], sessions: [] }
  }
}

async function loadCatalog(benchId = '') {
  try {
    catalog.value = await api.dmDevices(benchId || catalog.value.bench_id || '')
  } catch (e) {
    catalog.value = { ...catalog.value, available: [], connected: [] }
  }
}

async function loadLog() {
  try {
    const res = await api.dmLog(40)
    logLines.value = (res && res.lines) || []
  } catch (e) {
    logLines.value = []
  }
}

async function refreshAll() {
  await loadStatus()
  await loadCatalog()
  await loadLog()
}

async function refreshLoop() {
  if (autoRefresh.value) await refreshAll()
  timer = setTimeout(refreshLoop, 2000)
}

onMounted(async () => {
  await refreshAll()
  timer = setTimeout(refreshLoop, 2000)
})

onUnmounted(() => { if (timer) clearTimeout(timer) })

async function startDm() {
  busy.value = true
  try {
    const res = await api.dmStart({ force: false, bench: catalog.value.bench_id || '' })
    flash((res && res.message) || (res.success ? '已启动' : '启动失败'), res.success ? 'ok' : 'warn')
    await refreshAll()
  } catch (e) {
    flash(`启动失败：${e.message}`, 'warn')
  } finally {
    busy.value = false
  }
}

async function stopDm() {
  busy.value = true
  try {
    const res = await api.dmStop()
    flash((res && res.message) || '已停止', res.success ? 'ok' : 'warn')
    await refreshAll()
  } finally {
    busy.value = false
  }
}

async function connectDevice(row, devMode) {
  busy.value = true
  try {
    const res = await api.dmConnect({
      bench_id: catalog.value.bench_id,
      device_id: row.device_id,
      alias: row.alias || row.device_id,
      mode: devMode || mode.value
    })
    flash((res && res.message) || (res.success ? '已连接' : '连接失败'), res.success ? 'ok' : 'warn')
    await refreshAll()
  } catch (e) {
    flash(`连接失败：${e.message}`, 'warn')
  } finally {
    busy.value = false
  }
}

async function disconnectDevice(slot) {
  busy.value = true
  try {
    const handle = slotHandle(slot)
    const res = await api.dmDisconnect({ handle })
    flash((res && res.message) || '已断开', res.success ? 'ok' : 'warn')
    await refreshAll()
  } finally {
    busy.value = false
  }
}

/** 句柄优先取 DM 设备视图下发的 handles；老版本服务端没有时再从会话反查 */
function slotHandle(slot) {
  if (slot && Array.isArray(slot.handles) && slot.handles.length) return slot.handles[0]
  for (const sess of sessions.value) {
    for (const h of sess.handles || []) {
      if (h.key === slot.key) return h.handle
    }
  }
  return 0
}

async function switchBench(benchId) {
  catalog.value.bench_id = benchId
  await loadCatalog(benchId)
}

async function quickCall(slot) {
  busy.value = true
  try {
    const res = await api.dmCall({ handle: slotHandle(slot), action: 'identify', params: {} })
    const value = res && res.result && res.result.value
    flash(res.success ? `识别：${typeof value === 'object' ? JSON.stringify(value).slice(0, 90) : value}` : res.message,
      res.success ? 'ok' : 'warn')
    await refreshAll()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="dm">
    <header class="dm-head">
      <div class="hd-left">
        <span class="title">连接管理</span>
        <span class="dot" :class="status.running ? 'ok' : 'off'"></span>
        <span class="state">{{ status.running ? '装备管理模块运行中' : '装备管理模块未启动' }}</span>
        <span v-if="status.endpoint && status.endpoint.bench" class="chip">测试台 {{ status.endpoint.bench }}</span>
        <span v-if="status.running" class="chip ghost">PID {{ status.endpoint.pid }}</span>
        <span v-if="status.running" class="chip ghost">{{ sessions.length }} 个调用方</span>
      </div>
      <div class="hd-right">
        <span v-if="notice.text" class="notice" :class="notice.kind">{{ notice.text }}</span>
        <label class="chk"><input v-model="autoRefresh" type="checkbox" />自动刷新</label>
        <button v-if="!status.running" class="btn primary sm" :disabled="busy" @click="startDm">启动模块</button>
        <template v-else>
          <button class="btn ghost sm" :disabled="busy" @click="stopDm">停止模块</button>
          <button class="btn ghost sm" @click="refreshAll">刷新</button>
        </template>
      </div>
    </header>

    <div v-if="status.error" class="tip warn">{{ status.error }}</div>

    <div class="dm-body">
      <!-- 可连接设备 -->
      <article class="pane">
        <div class="pane-head">
          <span>可连接设备</span>
          <select v-if="catalog.benches && catalog.benches.length" class="inp"
                  :value="catalog.bench_id" @change="switchBench($event.target.value)">
            <option v-for="b in catalog.benches" :key="b.bench_id" :value="b.bench_id">
              {{ b.bench_id }} {{ b.name || '' }}
            </option>
          </select>
        </div>
        <div class="pane-tools">
          <input v-model="keyword" class="inp grow" placeholder="按名称 / 型号 / 类型过滤" />
          <select v-model="mode" class="inp">
            <option value="simulate">仿真</option>
            <option value="real">真机</option>
          </select>
        </div>
        <div class="list">
          <div v-for="d in availableRows" :key="d.device_id" class="row">
            <div class="rmain">
              <div class="rname">{{ d.name || d.device_id }}</div>
              <div class="rsub">
                <span class="mono">{{ d.device_id }}</span>
                <span v-if="d.model" class="sep">·</span><span v-if="d.model">{{ d.model }}</span>
                <span v-if="d.device_type" class="sep">·</span><span v-if="d.device_type">{{ d.device_type }}</span>
              </div>
              <div class="rsub muted">{{ d.address || d.host || d.serial_port || '无连接参数（仿真）' }}</div>
            </div>
            <span v-if="ownerOf(d)" class="tag ok">已连接</span>
            <button v-else class="btn ghost sm" :disabled="busy || !status.running" @click="connectDevice(d)">
              建立连接
            </button>
          </div>
          <div v-if="!availableRows.length" class="empty">
            该测试台没有设备记录：先在「测试台导航」注册测试台并维护 BOM 清单
          </div>
        </div>
      </article>

      <!-- 已建立的连接 -->
      <article class="pane wide">
        <div class="pane-head">
          <span>已建立的连接（由装备管理模块持有）</span>
          <span class="hint">{{ (status.devices || []).length }} 个设备 · {{ stats.device_calls || 0 }} 次调用</span>
        </div>
        <div class="list">
          <div v-for="slot in status.devices || []" :key="slot.key" class="conn">
            <div class="cmain">
              <div class="cname">
                {{ slot.name || slot.alias || slot.key }}
                <span class="tag" :class="slot.simulated ? 'warn' : 'ok'">{{ slot.simulated ? '仿真' : '真机' }}</span>
                <span class="tag ghost">引用 {{ slot.refs }}</span>
              </div>
              <div class="rsub">
                <span class="mono">{{ slot.driver }}</span>
                <span v-if="slot.resource" class="sep">·</span><span v-if="slot.resource" class="mono">{{ slot.resource }}</span>
              </div>
              <div class="rsub muted">
                句柄 {{ slotHandle(slot) || '—' }} · 调用 {{ slot.calls }} 次 · 失败 {{ slot.errors }} 次 ·
                空闲 {{ slot.idle_s }} s
              </div>
              <div v-if="slot.idn" class="idn">{{ slot.idn }}</div>
            </div>
            <div class="cact">
              <button class="btn ghost sm" :disabled="busy || !slotHandle(slot)" @click="quickCall(slot)">识别</button>
              <button class="btn ghost sm" :disabled="busy || !slotHandle(slot)" @click="disconnectDevice(slot)">断开</button>
            </div>
          </div>
          <div v-if="!(status.devices || []).length" class="empty">
            还没有已建立的连接。左侧选设备 → 建立连接；连接建立后，测试程序通过装备管理模块操作它
          </div>
        </div>

        <div class="sessions">
          <div class="pane-head sub"><span>调用方（谁连着这个模块）</span></div>
          <div v-for="s in sessions" :key="s.id" class="srow">
            <span class="mono">#{{ s.id }}</span>
            <span class="grow">{{ s.name || '未命名' }}</span>
            <span class="muted">{{ (s.handles || []).length }} 个句柄 · {{ s.calls }} 次调用 · {{ s.connected_s }} s</span>
          </div>
          <div v-if="!sessions.length" class="empty small">暂无调用方</div>
        </div>
      </article>

      <!-- 模块状态 -->
      <article class="pane side">
        <div class="pane-head"><span>模块状态</span></div>
        <dl class="kv">
          <dt>端点</dt>
          <dd class="mono">{{ status.endpoint.address || '—' }}</dd>
          <dt>通道类型</dt>
          <dd>{{ status.endpoint.kind === 'tcp' ? '本地 TCP' : '命名管道' }}</dd>
          <dt>协议版本</dt>
          <dd>{{ status.endpoint.protocol || '—' }}</dd>
          <dt>共享内存段</dt>
          <dd class="mono small">{{ ring.shm || '—' }}</dd>
          <dt>单块上限</dt>
          <dd>{{ ring.block ? fmtBytes(ring.block) : '—' }}</dd>
          <dt>槽位占用</dt>
          <dd>{{ ring.live_blocks === undefined ? '—' : `${ring.live_blocks} / ${ring.slots}` }}</dd>
          <dt>已下发数据</dt>
          <dd>{{ fmtBytes(stats.streamed_bytes) }}</dd>
          <dt>请求 / 失败</dt>
          <dd>{{ stats.requests || 0 }} / {{ stats.errors || 0 }}</dd>
          <dt>已运行</dt>
          <dd>{{ stats.uptime_s ? `${stats.uptime_s} s` : '—' }}</dd>
        </dl>

        <div class="pane-head sub"><span>最近请求</span></div>
        <div class="logs">
          <div v-for="(l, i) in logLines.slice(-16).reverse()" :key="i" class="lrow">
            <span class="lt">{{ l.ts ? new Date(l.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—' }}</span>
            <span class="lo">{{ l.op }}</span>
            <span class="ld grow">{{ l.detail || '' }}</span>
            <span class="lm">{{ l.ms }} ms</span>
          </div>
          <div v-if="!logLines.length" class="empty small">暂无请求</div>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.dm { display: flex; flex-direction: column; gap: 10px; padding: 4px 2px 18px; }
.dm-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; padding: 10px 14px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2); }
.hd-left, .hd-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.title { font-size: 14px; font-weight: 800; }
.state { font-size: 12px; color: var(--muted); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border-strong); }
.dot.ok { background: var(--ok); }
.dot.off { background: var(--border-strong); }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: rgba(var(--accent-rgb), .1); color: var(--accent); }
.chip.ghost { background: var(--panel-3); color: var(--muted); }
.notice { font-size: 11.5px; }
.notice.ok { color: var(--ok); }
.notice.warn { color: var(--warn); }
.chk { font-size: 11.5px; color: var(--muted); display: flex; align-items: center; gap: 4px; }
.btn.sm { padding: 4px 10px; font-size: 12px; }
.tip.warn { font-size: 12px; color: var(--warn); padding: 6px 12px; border: 1px solid rgba(180, 83, 9, .25); border-radius: 8px; background: rgba(180, 83, 9, .06); }

.dm-body { display: grid; grid-template-columns: 320px minmax(360px, 1fr) 320px; gap: 10px; align-items: start; }
.pane { border: 1px solid var(--border); border-radius: 10px; background: var(--panel); display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
.pane-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 12px; font-weight: 700; }
.pane-head.sub { font-weight: 600; color: var(--muted); }
.hint { font-size: 11px; color: var(--muted); font-weight: 500; }
.pane-tools { display: flex; gap: 6px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.inp { padding: 4px 8px; font-size: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel); color: inherit; font-family: inherit; }
.inp.grow { flex: 1; min-width: 0; }
.list { max-height: 380px; overflow-y: auto; padding: 6px; display: flex; flex-direction: column; gap: 4px; }
.row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border: 1px solid transparent; border-radius: 8px; }
.row:hover { background: var(--panel-2); border-color: var(--border); }
.rmain { flex: 1; min-width: 0; }
.rname { font-size: 12.5px; font-weight: 600; }
.rsub { font-size: 11px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rsub.muted { color: var(--border-strong); }
.sep { margin: 0 4px; }
.mono { font-family: var(--mono); }
.tag { font-size: 10.5px; padding: 1px 7px; border-radius: 999px; background: rgba(21, 128, 61, .12); color: var(--ok); }
.tag.warn { background: rgba(180, 83, 9, .14); color: var(--warn); }
.tag.ghost { background: var(--panel-3); color: var(--muted); }
.conn { display: flex; align-items: flex-start; gap: 8px; padding: 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel-2); }
.cmain { flex: 1; min-width: 0; }
.cname { font-size: 12.5px; font-weight: 700; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.idn { font-size: 10.5px; font-family: var(--mono); color: var(--border-strong); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cact { display: flex; flex-direction: column; gap: 4px; }
.sessions { border-top: 1px solid var(--border); }
.srow { display: flex; align-items: center; gap: 8px; padding: 5px 12px; font-size: 11.5px; }
.srow:nth-child(even) { background: var(--panel-2); }
.grow { flex: 1; min-width: 0; }
.muted { color: var(--muted); }
.kv { display: grid; grid-template-columns: 84px 1fr; gap: 3px 8px; padding: 8px 12px; font-size: 11.5px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; overflow: hidden; text-overflow: ellipsis; }
.small { font-size: 10.5px; }
.logs { max-height: 210px; overflow-y: auto; padding: 4px 8px 8px; display: flex; flex-direction: column; gap: 2px; }
.lrow { display: flex; align-items: center; gap: 6px; font-size: 10.5px; font-family: var(--mono); color: var(--muted); }
.lt { color: var(--border-strong); }
.lo { color: var(--accent); min-width: 48px; }
.ld { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lm { color: var(--border-strong); }
.empty { padding: 14px 12px; font-size: 11.5px; color: var(--muted); line-height: 1.6; }
.empty.small { padding: 8px 12px; font-size: 11px; }
@media (max-width: 1280px) { .dm-body { grid-template-columns: 1fr 1fr; } }
</style>
