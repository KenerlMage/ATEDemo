<script setup>
/**
 * 装备货架页
 *  - 入口：装备助手页右上角「装备货架」按钮
 *  - 清单来源：controller（MockController 下发）/ cache（Controller 不可达，用本地缓存）
 *          / local（完全回退到随程序的本地清单），三态在顶部 chip 与信息区展示
 *  - 下载：把安装包复制到本地安装目录（assistant_shelf/installed/<id>），
 *          完成后该助手即出现在「装备助手」页的卡片列表里
 *  - 启动：调用后端 launch 接口，由安装目录内的启动脚本拉起助手
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()

const loading = ref(true)
const err = ref('')
const info = ref({ source: '', channel: '', controller_url: '', updated_at: '', note: '', count: 0 })
// /api/assistants/source 的来源状态（取不到时保持 available=false，不打断主流程）
const remote = ref({ available: false, mode: '', controller_url: '', cached_at: '', count: 0 })
const items = ref([])
const busyId = ref('')
const keyword = ref('')
const confirmId = ref('')
const toast = ref({ text: '', kind: 'ok' })

const SOURCE_LABEL = {
  controller: 'MockController 下发',
  cache: '本地缓存（Controller 不可达）',
  local: '本地兜底清单'
}
const sourceLabel = computed(() => SOURCE_LABEL[info.value.source] || '来源未知')
const controllerUrl = computed(() => info.value.controller_url || remote.value.controller_url || '')
// 清单更新时间优先用下发清单自带的时间，其次退回缓存抓取时间
const updatedAt = computed(() => info.value.updated_at || remote.value.cached_at || '')

const installedCount = computed(() => items.value.filter((x) => x.installed).length)
const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return items.value
  return items.value.filter((x) =>
    [x.name, x.summary, (x.tags || []).join(' '), x.vendor, x.id].join(' ').toLowerCase().includes(kw)
  )
})

function flash(text, kind = 'ok') {
  toast.value = { text, kind }
  setTimeout(() => { if (toast.value.text === text) toast.value = { text: '', kind } }, 4200)
}

// 文件大小：有 size_bytes 用它（KB/MB 自适应，保留 1 位小数），否则沿用 size_hint
function sizeText(it) {
  const n = Number(it.size_bytes || 0)
  if (n > 0) {
    const kb = n / 1024
    return kb < 1024 ? `${kb.toFixed(1)} KB` : `${(kb / 1024).toFixed(1)} MB`
  }
  if (it.size_hint) return it.size_hint
  return it.package_ready ? (it.package_size_text || '—') : '缺失'
}

// 来源状态接口：可选依赖，失败只记录「不可用」，不影响清单展示
async function loadSource() {
  try {
    const res = await api.getAssistantSource()
    if (!res || res.success === false) return
    remote.value = {
      available: true,
      // 后端当前返回 mode/cached_count，一并兼容 source/count 命名
      mode: res.source || res.mode || '',
      controller_url: res.controller_url || '',
      cached_at: res.cached_at || '',
      count: Number(res.count || res.cached_count || 0)
    }
  } catch (e) {
    remote.value = { available: false, mode: '', controller_url: '', cached_at: '', count: 0 }
  }
}

async function load() {
  loading.value = true
  err.value = ''
  loadSource()
  try {
    const res = await api.getAssistantCatalog()
    if (!res || res.success === false) throw new Error((res && res.message) || '读取货架清单失败')
    items.value = res.items || []
    info.value = {
      source: res.source || '',
      channel: res.channel || '',
      controller_url: res.controller_url || res.remote_url || '',
      updated_at: res.updated_at || '',
      note: res.note || '',
      count: res.count || 0
    }
  } catch (e) {
    err.value = `无法读取货架清单：${e.message}`
  } finally {
    loading.value = false
  }
}

async function download(item) {
  if (busyId.value) return
  busyId.value = item.id
  try {
    const res = await api.installAssistant(item.id)
    if (!res || res.success === false) throw new Error((res && res.message) || '下载失败')
    flash(res.message || '下载完成')
    await load()
  } catch (e) {
    flash(`下载失败：${e.message}`, 'warn')
  } finally {
    busyId.value = ''
  }
}

async function launch(item) {
  if (busyId.value) return
  busyId.value = item.id
  try {
    const res = await api.launchAssistant(item.id)
    if (!res || res.success === false) throw new Error((res && res.message) || '启动失败')
    flash(res.message || '已启动')
    await load()
  } catch (e) {
    flash(`启动失败：${e.message}`, 'warn')
  } finally {
    busyId.value = ''
  }
}

async function remove(item) {
  if (confirmId.value !== item.id) {
    confirmId.value = item.id
    flash(`再次点击「确认移除」将移除 ${item.name}（目录会归档，不物理删除）`, 'warn')
    setTimeout(() => { if (confirmId.value === item.id) confirmId.value = '' }, 3600)
    return
  }
  confirmId.value = ''
  busyId.value = item.id
  try {
    const res = await api.uninstallAssistant(item.id)
    if (!res || res.success === false) throw new Error((res && res.message) || '移除失败')
    flash(res.message || '已移除')
    await load()
  } catch (e) {
    flash(`移除失败：${e.message}`, 'warn')
  } finally {
    busyId.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="sh-page">
    <section class="sh-head">
      <div class="hd-left">
        <button class="btn ghost back" @click="router.push('/assistant')">← 装备助手</button>
        <div>
          <div class="hd-title">装备货架</div>
          <div class="hd-sub">下载装备助手到本机；下载后自动出现在装备助手页，可直接启动。</div>
        </div>
      </div>
      <div class="hd-right">
        <span class="src-chip" :class="info.source || 'unknown'">
          {{ sourceLabel }}
          <template v-if="info.channel"> · {{ info.channel }}</template>
        </span>
        <span class="cnt-chip">共 {{ info.count }} 个 · 已下载 {{ installedCount }}</span>
        <button class="btn ghost" @click="load" :disabled="loading">刷新清单</button>
        <button class="btn primary" @click="router.push('/assistant')">前往装备助手</button>
      </div>
    </section>

    <div class="sh-note" :class="{ degraded: info.source === 'cache' }">
      <div><b>清单来源：</b>{{ info.note || '货架清单由 MockController 下发；不可达时使用本地缓存或兜底清单。' }}</div>
      <div class="src-meta">
        <span><i>Controller</i>{{ controllerUrl || '—' }}</span>
        <span><i>更新时间</i>{{ updatedAt || '—' }}</span>
        <span v-if="remote.available" class="muted">· 来源接口已连通{{ remote.count ? `（缓存 ${remote.count} 条）` : '' }}</span>
      </div>
    </div>

    <div class="sh-bar">
      <input v-model="keyword" class="input" placeholder="按名称 / 标签 / 供应方检索" />
      <span class="muted">说明：下载 = 复制安装包到本机；启动 = 调用助手目录内的启动脚本。</span>
    </div>

    <div v-if="toast.text" class="notice" :class="toast.kind">{{ toast.text }}</div>
    <div v-if="loading" class="tip">正在读取货架清单…</div>
    <div v-else-if="err" class="tip err">{{ err }}</div>
    <div v-else-if="!shown.length" class="tip">没有匹配的助手。</div>

    <section v-else class="grid">
      <article v-for="it in shown" :key="it.id" class="card" :class="{ own: it.installed }">
        <div class="card-top">
          <img class="ico" :src="it.icon_url" :alt="it.name" loading="lazy" />
          <div class="id-box">
            <div class="nm">{{ it.name }}</div>
            <div class="meta">v{{ it.version }} · {{ it.vendor }}</div>
          </div>
          <span v-if="it.installed && it.updated" class="tag update">有新版本</span>
          <span v-else-if="it.installed" class="tag done">已下载</span>
        </div>

        <div class="sum">{{ it.summary }}</div>
        <div class="desc">{{ it.description }}</div>

        <div class="tags">
          <span v-for="t in it.tags || []" :key="t" class="tg">{{ t }}</span>
        </div>

        <div class="facts">
          <span><i>安装包</i>{{ sizeText(it) }}</span>
          <span><i>发布</i>{{ (it.published_at || '').slice(0, 10) || '—' }}</span>
          <span v-if="it.installed"><i>下载于</i>{{ it.installed_at }}</span>
        </div>

        <div class="rel">
          <span class="rel-h">版本说明</span>
          <span class="rel-t">{{ it.release_note || '—' }}</span>
        </div>

        <div class="req">运行要求：{{ it.requires }}</div>

        <div class="acts">
          <button
            v-if="!it.installed"
            class="btn primary"
            :disabled="!it.package_ready || busyId === it.id"
            @click="download(it)"
          >
            {{ busyId === it.id ? '下载中…' : '下载到本机' }}
          </button>
          <template v-else>
            <button class="btn primary" :disabled="busyId === it.id" @click="launch(it)">启动</button>
            <button class="btn ghost" :disabled="busyId === it.id" @click="download(it)">
              {{ it.updated ? '更新到 ' + it.version : '重新下载' }}
            </button>
            <button
              class="btn ghost danger"
              :disabled="busyId === it.id"
              @click="remove(it)"
            >
              {{ confirmId === it.id ? '确认移除' : '移除' }}
            </button>
          </template>
        </div>
      </article>
    </section>
  </div>
</template>

<style scoped>
.sh-page { flex: 1; padding: 18px 22px 26px; display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }

.sh-head {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 14px 18px; border-top: 3px solid var(--accent);
}
.hd-left { display: flex; align-items: center; gap: 14px; }
.back { padding: 7px 12px; }
.hd-title { font-size: 19px; font-weight: 800; }
.hd-sub { color: var(--muted); font-size: 12.5px; margin-top: 4px; }
.hd-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.src-chip { font-size: 11.5px; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--border-strong); color: var(--muted); }
.src-chip.controller { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.4); background: rgba(var(--accent-rgb), 0.07); }
.src-chip.cache { color: var(--warn); border-color: rgba(180, 120, 20, 0.4); background: rgba(180, 120, 20, 0.08); }
.src-chip.local { color: var(--warn); border-color: rgba(180, 120, 20, 0.3); border-style: dashed; background: transparent; }
.cnt-chip { font-size: 11.5px; color: var(--muted); }

.sh-note {
  font-size: 12.5px; line-height: 1.7; color: var(--muted);
  background: var(--panel-2); border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: 8px; padding: 9px 12px;
}
.sh-note b { color: var(--text, #222); }
.sh-note.degraded { border-left-color: var(--warn); }
.src-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 3px; font-size: 11.5px; }
.src-meta i { font-style: normal; color: var(--border-strong); margin-right: 5px; }

.sh-bar { display: flex; align-items: center; gap: 12px; }
.sh-bar .input { max-width: 320px; }
.sh-bar .muted { font-size: 11.5px; }

.tip { color: var(--muted); font-size: 13px; padding: 18px 2px; }
.tip.err { color: var(--err); }
.notice { font-size: 12.5px; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--panel-2); }
.notice.ok { color: var(--ok); border-color: rgba(21, 128, 61, 0.35); background: rgba(21, 128, 61, 0.07); }
.notice.warn { color: var(--warn); border-color: rgba(180, 120, 20, 0.35); background: rgba(180, 120, 20, 0.07); }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 14px; }
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 14px; display: flex; flex-direction: column; gap: 9px;
}
.card.own { border-color: rgba(var(--accent-rgb), 0.4); }
.card-top { display: flex; align-items: center; gap: 10px; }
.ico {
  width: 48px; height: 48px; border-radius: 10px; flex: 0 0 48px;
  border: 1px solid var(--border); background: #F7F0E6; object-fit: cover;
}
.id-box { min-width: 0; }
.nm { font-size: 15px; font-weight: 800; }
.meta { font-size: 11.5px; color: var(--muted); margin-top: 2px; }
.tag { margin-left: auto; font-size: 11px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); background: var(--panel-2); }
.tag.done { color: var(--ok); border-color: rgba(21, 128, 61, 0.35); background: rgba(21, 128, 61, 0.09); }
.tag.update { color: var(--warn); border-color: rgba(180, 120, 20, 0.4); background: rgba(180, 120, 20, 0.09); }

.sum { font-size: 13px; color: var(--accent); font-weight: 600; }
.desc { font-size: 12.5px; color: var(--muted); line-height: 1.65; }
.tags { display: flex; flex-wrap: wrap; gap: 5px; }
.tg { font-size: 11px; color: var(--muted); border: 1px dashed var(--border-strong); border-radius: 5px; padding: 1px 6px; }

.facts { display: flex; flex-wrap: wrap; gap: 14px; font-size: 11.5px; color: var(--muted); }
.facts i { font-style: normal; color: var(--border-strong); margin-right: 5px; }

.rel { display: flex; flex-direction: column; gap: 3px; border-top: 1px solid var(--border); padding-top: 7px; }
.rel-h { font-size: 11.5px; color: var(--accent); font-weight: 700; }
.rel-t { font-size: 12px; color: var(--muted); line-height: 1.6; }
.req { font-size: 11.5px; color: var(--muted); }

.acts { display: flex; gap: 8px; margin-top: auto; padding-top: 8px; border-top: 1px solid var(--border); flex-wrap: wrap; }
.acts .btn { padding: 7px 14px; font-size: 12.5px; }
.btn.ghost.danger { color: var(--err); border-color: rgba(190, 40, 40, 0.3); }
</style>
