<script setup>
/**
 * 版本升级入口组件（挂载于侧栏页脚）
 *
 * 流程：检查更新 -> 展示新版本与 release note -> 下载 -> 升级 -> 提示重启
 * 升级在后台只做"准备"（下载 + 校验 + 登记），目录切换由 ATE_Launcher 在下次启动完成；
 * 测试任务执行期间服务端会拒绝升级（返回 BUSY），界面同步禁用按钮并给出原因。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'

const POLL_MS = 10 * 60 * 1000

const status = ref(null)
const note = ref(null)
const step = ref('overview')
const busy = ref(false)
const opened = ref(false)
const error = ref('')
let timer = null

const phase = computed(() => status.value?.phase || 'idle')
const available = computed(() => !!status.value?.update_available)
const latest = computed(() => status.value?.latest || null)
const current = computed(() => status.value?.version || '—')
const blocked = computed(() => (status.value?.active_tasks || []).length > 0)
const canApply = computed(() => phase.value === 'downloaded' && !blocked.value)

async function req(path, opts) {
  const resp = await fetch(path, opts)
  const data = await resp.json().catch(() => ({ success: false, message: '响应解析失败' }))
  if (!data.success) {
    error.value = data.message || data.code || '请求失败'
  }
  return data
}

async function refresh() {
  const data = await req('/api/updates/status')
  if (data.success) {
    status.value = data
    const pending = data.note_after
    if (pending && !(data.note_ack || {})[pending]) {
      await openNote(pending, 'after')
    }
  }
}

async function checkNow() {
  busy.value = true
  error.value = ''
  try {
    await req('/api/updates/check?force=1')
    await refresh()
  } finally {
    busy.value = false
  }
}

async function openNote(version, kind = 'before') {
  const data = await req('/api/updates/release-note/' + encodeURIComponent(version))
  if (data.success) {
    note.value = data
    step.value = kind === 'after' ? 'note-after' : 'note-before'
    opened.value = true
  }
}

function openDialog() {
  error.value = ''
  step.value = available.value ? 'note-before' : 'overview'
  opened.value = true
  if (available.value && latest.value) openNote(latest.value.version, 'before')
}

function close() {
  opened.value = false
  error.value = ''
}

async function doDownload() {
  busy.value = true
  error.value = ''
  try {
    const data = await req('/api/updates/download', { method: 'POST' })
    if (data.success) {
      status.value = data
      step.value = 'confirm'
    }
  } finally {
    busy.value = false
  }
}

async function doApply() {
  busy.value = true
  error.value = ''
  try {
    const data = await req('/api/updates/apply', { method: 'POST' })
    if (data.success) {
      status.value = data
      step.value = 'done'
    }
  } finally {
    busy.value = false
  }
}

async function ackNote() {
  const v = note.value?.version
  const data = await req('/api/updates/ack-note?version=' + encodeURIComponent(v || ''), { method: 'POST' })
  if (data.success) status.value = data
  note.value = null
  step.value = available.value ? 'overview' : 'overview'
  opened.value = false
}

function humanSize(n) {
  if (!n) return '—'
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(2) + ' MB'
}

onMounted(async () => {
  await refresh()
  if (!available.value && phase.value === 'idle') await checkNow()
  timer = setInterval(refresh, POLL_MS)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="upd">
    <button class="upd-line" :class="{ hot: available }" @click="openDialog">
      <span class="dot" :class="{ on: available }" />
      <span class="txt">
        {{ available ? '发现新版本 v' + (latest?.version || '') : 'ATE RUNNER v' + current }}
      </span>
    </button>

    <div v-if="opened" class="mask" @click.self="close">
      <div class="dlg">
        <div class="dlg-head">
          <span>版本升级</span>
          <button class="x" @click="close">×</button>
        </div>

        <div class="dlg-body">
          <!-- 概览 -->
          <template v-if="step === 'overview'">
            <p class="kv"><b>当前版本</b>{{ current }}</p>
            <p class="kv"><b>可用版本</b>{{ latest?.version || '暂无新版本' }}</p>
            <p v-if="latest?.released_at" class="kv"><b>发布日期</b>{{ latest.released_at }}</p>
            <p v-if="latest?.package" class="kv"><b>安装包</b>{{ latest.package.name }} · {{ humanSize(latest.package.size) }}</p>
            <p v-if="blocked" class="warn">
              当前有 {{ status.active_tasks.length }} 个测试任务正在执行，请等待任务结束后再升级。
            </p>
          </template>

          <!-- release note（升级前 / 升级后） -->
          <template v-else-if="step === 'note-before' || step === 'note-after'">
            <p class="kv"><b>{{ step === 'note-after' ? '已升级至' : '新版本' }}</b>v{{ note?.version }}</p>
            <p v-if="note?.title" class="kv"><b>标题</b>{{ note.title }}</p>
            <ul v-if="note?.highlights?.length" class="hl">
              <li v-for="(h, i) in note.highlights" :key="i">{{ h }}</li>
            </ul>
            <pre class="note">{{ note?.release_note || '（无附加说明）' }}</pre>
          </template>

          <!-- 下载中 -->
          <template v-else-if="step === 'progress'">
            <p class="kv"><b>阶段</b>下载安装包</p>
          </template>

          <!-- 确认升级 -->
          <template v-else-if="step === 'confirm'">
            <p class="kv"><b>安装包</b>已下载并校验通过</p>
            <p class="kv"><b>目标版本</b>v{{ latest?.version }}</p>
            <p class="hint">
              升级将替换程序文件，本地测试记录、运行目录与授权文件会完整保留。
              升级完成后需重启 ATE Runner 生效。
            </p>
          </template>

          <!-- 完成 -->
          <template v-else-if="step === 'done'">
            <p class="ok">升级已完成登记，重启后生效</p>
            <p class="kv"><b>新版本</b>v{{ status?.installed?.to }}</p>
            <p class="kv"><b>数据保留</b>{{ status?.installed?.preserved_intact ? '校验通过' : '需人工确认' }}</p>
            <p v-if="status?.installed?.pending_swap" class="hint">
              待替换清单：{{ status.installed.pending_swap }}
            </p>
          </template>

          <p v-if="error" class="err">{{ error }}</p>
        </div>

        <div class="dlg-foot">
          <template v-if="step === 'overview'">
            <button class="btn" :disabled="busy" @click="checkNow">检查更新</button>
            <button class="btn primary" :disabled="!available" @click="openNote(latest.version, 'before')">查看更新</button>
          </template>
          <template v-else-if="step === 'note-before'">
            <button class="btn" @click="close">取消</button>
            <button class="btn primary" :disabled="busy || blocked" @click="doDownload">
              {{ blocked ? '任务执行中，暂不可升级' : '同意并下载' }}
            </button>
          </template>
          <template v-else-if="step === 'note-after'">
            <button class="btn primary" @click="ackNote">我知道了</button>
          </template>
          <template v-else-if="step === 'confirm'">
            <button class="btn" @click="close">稍后</button>
            <button class="btn primary" :disabled="busy || !canApply" @click="doApply">
              {{ blocked ? '任务执行中，暂不可升级' : '立即升级' }}
            </button>
          </template>
          <template v-else-if="step === 'done'">
            <button class="btn primary" @click="close">关闭（请重启生效）</button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.upd { width: 100%; }
.upd-line {
  display: flex; align-items: center; gap: 6px; width: 100%;
  padding: 4px 6px; border: 1px solid transparent; border-radius: 5px;
  background: transparent; color: inherit; font: inherit; cursor: pointer;
  opacity: .85;
}
.upd-line:hover { border-color: rgba(255, 255, 255, .25); opacity: 1; }
.upd-line.hot { color: #ffe08a; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: #9aa7ae; flex: 0 0 6px; }
.dot.on { background: #ffc53d; box-shadow: 0 0 6px #ffc53d; }
.txt { font-size: 11px; letter-spacing: .3px; white-space: nowrap; }

.mask { position: fixed; inset: 0; background: rgba(10, 14, 20, .55); display: flex; align-items: center; justify-content: center; z-index: 90; }
.dlg { width: 560px; max-width: 92vw; max-height: 84vh; display: flex; flex-direction: column; background: #fff; border-radius: 10px; box-shadow: 0 18px 50px rgba(0, 0, 0, .3); overflow: hidden; }
.dlg-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; background: #8b1a1a; color: #fff; font-weight: 700; }
.dlg-head .x { background: transparent; border: 0; color: #fff; font-size: 20px; line-height: 1; cursor: pointer; }
.dlg-body { padding: 16px; overflow: auto; }
.dlg-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid #e5e9ec; }
.kv { margin: 0 0 8px; font-size: 13px; color: #16222e; }
.kv b { display: inline-block; min-width: 76px; color: #5b6b7a; font-weight: 700; }
.hl { margin: 6px 0 10px 18px; font-size: 13px; color: #16222e; }
.note { margin: 8px 0 0; padding: 10px 12px; background: #f7f9fa; border: 1px solid #e3e9eb; border-radius: 6px; font-size: 12px; line-height: 1.7; color: #16222e; white-space: pre-wrap; font-family: inherit; }
.hint { margin: 6px 0 0; font-size: 12px; color: #5b6b7a; }
.warn { margin: 8px 0 0; padding: 8px 10px; background: #fdf3e3; border: 1px solid #f0d9ab; border-radius: 6px; font-size: 12px; color: #8a5a12; }
.ok { margin: 0 0 10px; padding: 8px 10px; background: #eaf3f3; border: 1px solid #bfdede; border-radius: 6px; font-size: 13px; color: #0e6e6e; }
.err { margin: 10px 0 0; padding: 8px 10px; background: #fdecec; border: 1px solid #f0b8b8; border-radius: 6px; font-size: 12px; color: #8b1a1a; }
.btn { padding: 7px 14px; border: 1px solid #cfd8dd; background: #fff; color: #16222e; border-radius: 6px; font-size: 13px; cursor: pointer; }
.btn:hover:not(:disabled) { border-color: #9aa7ae; }
.btn.primary { background: #8b1a1a; border-color: #8b1a1a; color: #fff; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
</style>
