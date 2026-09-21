<script setup>
/**
 * 测试台导航页
 *  - 中部: 测试台指标块
 *  - 下部: 已注册测试台以「独立标签」形式展示, 点击标签查看该测试台的
 *          产品/子系统归属、产线工位信息、设备连接配置与最近一次连通性自检清单
 *   注: 页面级导航已统一收到左侧垂直导航栏 (App.vue)
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const loading = ref(true)
const error = ref('')
const tip = ref('')
const tipErr = ref(false)
const busy = ref(false)
const benches = ref([])
const overview = ref(null)
const activeId = ref('')

const VERIFY_TEXT = { pass: '自检通过', warn: '待人工确认', fail: '自检未通过' }
const NET = ['LAN', 'ETHERNET', 'TCP', 'IP']
const SER = ['SERIAL', 'RS232', 'RS485', 'UART', 'COM']

const active = computed(() => benches.value.find((t) => t.id === activeId.value) || null)
// 兼容性保护: 历史/半成品数据缺少字段时不至于渲染报错
const st = computed(() => (active.value && active.value.station) || {})
const devs = computed(() => (active.value && active.value.devices) || [])
const verification = computed(() => (active.value && active.value.verification) || null)
const checklist = computed(() => (verification.value && verification.value.checklist) || [])
const configChecks = computed(() => checklist.value.filter((c) => c.kind !== 'device'))
const deviceChecks = computed(() => checklist.value.filter((c) => c.kind === 'device'))

const verifyClass = (v) => (v === 'pass' ? 'v-pass' : v === 'warn' ? 'v-warn' : v === 'fail' ? 'v-fail' : 'v-none')
const verifyText = (v) => VERIFY_TEXT[v] || '未自检'
const checkClass = (s) => (s === 'pass' ? 'c-pass' : s === 'fail' ? 'c-fail' : 'c-skip')
const checkText = (s) => (s === 'pass' ? '通过' : s === 'fail' ? '失败' : '跳过')

function devTarget(d) {
  const itf = (d.interface || 'NONE').toUpperCase()
  if (NET.includes(itf)) return d.host ? `${d.host}:${d.port ?? '—'}` : '未配置'
  if (SER.includes(itf)) return d.serial_port ? `${d.serial_port} @ ${d.baudrate || 9600}` : '未配置'
  if (d.address) return d.address
  return '不支持自动探测'
}

function deviceResult(dev) {
  const key = `device::${dev.id || dev.name}`
  return deviceChecks.value.find((c) => c.key === key) || null
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, ov] = await Promise.all([api.getTestbenches(), api.getTestbenchOverview()])
    benches.value = list.testbenches || []
    overview.value = ov && ov.success !== false ? ov : null
    if (!activeId.value || !benches.value.some((t) => t.id === activeId.value)) {
      activeId.value = benches.value.length ? benches.value[0].id : ''
    }
  } catch (e) {
    error.value = `无法连接后端：${e.message}`
  } finally {
    loading.value = false
  }
}

async function runVerify(mode) {
  if (!active.value || busy.value) return
  busy.value = true
  tip.value = ''
  tipErr.value = false
  try {
    const res = await api.verifyTestbench(active.value.id, mode, mode === 'simulate' ? 0.2 : 2)
    if (!res.success) throw new Error(res.message || '自检失败')
    const r = res.result
    tip.value =
      mode === 'simulate'
        ? `模拟自检完成（离线演示，未发起真实连接）：${r.summary.pass}/${r.summary.total} 项通过`
        : `自检完成：通过 ${r.summary.pass} / 失败 ${r.summary.fail} / 跳过 ${r.summary.skip}`
    tipErr.value = r.overall === 'fail'
    await load()
  } catch (e) {
    tipErr.value = true
    tip.value = `自检失败：${e.message}`
  } finally {
    busy.value = false
  }
}

async function removeBench() {
  if (!active.value) return
  if (!window.confirm(`确认删除测试台「${active.value.title}」(${active.value.serial})？该操作不可撤销。`)) return
  busy.value = true
  try {
    const res = await api.deleteTestbench(active.value.id)
    if (!res.success) throw new Error(res.message || '删除失败')
    tipErr.value = false
    tip.value = res.message || '已删除'
    activeId.value = ''
    await load()
  } catch (e) {
    tipErr.value = true
    tip.value = `删除失败：${e.message}`
  } finally {
    busy.value = false
  }
}

async function restoreDefaults() {
  busy.value = true
  try {
    const res = await api.restoreDefaultTestbenches()
    if (!res.success) throw new Error(res.message || '恢复失败')
    tipErr.value = false
    tip.value = res.message || '已重建默认测试台'
    const created = (res.created || [])[0]
    await load()
    if (created) activeId.value = created.id
  } catch (e) {
    tipErr.value = true
    tip.value = `恢复默认测试台失败：${e.message}`
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="tbn-page">
    <!-- ============ 指标块 ============ -->
    <section v-if="overview" class="metrics">
      <div class="metric">
        <div class="metric-label">预设测试台类型</div>
        <div class="metric-value">{{ overview.preset_count }}</div>
        <div class="metric-foot">产品 {{ overview.product_count || 0 }} · 子系统 {{ overview.subsystem_count || 0 }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">已注册测试台</div>
        <div class="metric-value accent">{{ overview.registered_count }}</div>
        <div class="metric-foot">草稿 {{ overview.draft_count }} 台</div>
      </div>
      <div class="metric">
        <div class="metric-label">纳管设备总数</div>
        <div class="metric-value">{{ overview.device_total }}</div>
        <div class="metric-foot">可编程 {{ overview.programmable_total }} 台</div>
      </div>
      <div class="metric">
        <div class="metric-label">自检状态</div>
        <div class="metric-value" :class="overview.verify.fail ? 'err' : 'ok'">
          {{ overview.verify.pass }}/{{ overview.registered_count }}
        </div>
        <div class="metric-foot">
          待确认 {{ overview.verify.warn }} · 未通过 {{ overview.verify.fail }} · 未自检 {{ overview.verify.unchecked }}
        </div>
      </div>
    </section>

    <div v-if="loading" class="tip tip-loading">正在加载测试台注册表…</div>
    <div v-else-if="error" class="tip tip-err">{{ error }}</div>

    <template v-else>
      <!-- ============ 已注册测试台 (独立标签) ============ -->
      <section class="tbn-panel">
        <div class="panel-head">
          <div class="panel-title">已注册测试台</div>
          <span class="panel-sub">点击标签查看该测试台的产品/子系统、设备配置与自检清单</span>
        </div>

        <div class="tb-tabs">
          <button
            v-for="t in benches"
            :key="t.id"
            class="tb-tab"
            :class="{ active: t.id === activeId }"
            @click="activeId = t.id"
          >
            <span class="tb-dot" :class="verifyClass(t.verify_summary.overall)"></span>
            <span class="tb-tab-name">{{ t.title }}</span>
            <span class="tb-tab-serial">{{ t.serial }}</span>
            <span v-if="t.status !== 'registered'" class="tb-tag draft">草稿</span>
            <span v-else class="tb-tag" :class="verifyClass(t.verify_summary.overall)">
              {{ t.verify_summary.overall === 'pass' ? '可投用' : verifyText(t.verify_summary.overall) }}
            </span>
          </button>
          <router-link to="/testbenches/register" class="tb-tab tb-tab-add">＋ 新增测试台</router-link>
          <button class="tb-tab tb-tab-restore" :disabled="busy" @click="restoreDefaults">↺ 恢复默认测试台</button>
        </div>

        <!-- 空态 -->
        <div v-if="!benches.length" class="empty">
          <div class="empty-title">暂无已注册测试台</div>
          <div class="empty-text">先通过「新增测试台」完成三步注册：选择预设类型并获取 BOM、填写可编程设备配置、注册并验证连通性。也可直接恢复系统默认测试台（SPM读头动态测试台，已预置设备与自检结果）。</div>
          <div class="empty-actions">
            <router-link to="/testbenches/register" class="btn primary empty-btn">开始注册测试台</router-link>
            <button class="btn empty-btn" :disabled="busy" @click="restoreDefaults">↺ 恢复默认测试台</button>
          </div>
        </div>

        <!-- 测试台详情 -->
        <div v-else-if="active" class="tb-detail">
          <div class="detail-head">
            <div class="dh-left">
              <div class="dh-title">{{ active.title }}</div>
              <div class="dh-meta">
                <span class="mono">{{ active.serial }}</span>
                <span class="sep">·</span>
                <span v-if="active.product || active.subsystem" class="dh-branch">
                  {{ active.product || '—' }} / {{ active.subsystem || '—' }}
                </span>
                <span v-if="active.product || active.subsystem" class="sep">·</span>
                <span>{{ active.preset_name || active.preset_id }}</span>
                <span class="sep">·</span>
                <span>{{ devs.length }} 台设备（可编程 {{ active.programmable_count || 0 }}）</span>
              </div>
            </div>
            <div class="dh-right">
              <span class="badge" :class="active.status === 'registered' ? 'ok' : 'idle'">
                {{ active.status === 'registered' ? '已注册' : '草稿' }}
              </span>
              <span class="badge" :class="verifyClass(active.verify_summary.overall)">
                {{ verifyText(active.verify_summary.overall) }}
              </span>
            </div>
          </div>

          <!-- 基本信息 + 产线工位 -->
          <div class="info-grid">
            <div class="info-item"><span class="info-k">产品</span><span class="info-v">{{ active.product || '—' }}</span></div>
            <div class="info-item"><span class="info-k">子系统</span><span class="info-v">{{ active.subsystem || '—' }}</span></div>
            <div class="info-item"><span class="info-k">预设测试台类型</span><span class="info-v">{{ active.preset_name || active.preset_id || '—' }}</span></div>
            <div class="info-item"><span class="info-k">产线</span><span class="info-v">{{ st.line || '—' }}</span></div>
            <div class="info-item"><span class="info-k">工位</span><span class="info-v">{{ st.station || '—' }}</span></div>
            <div class="info-item"><span class="info-k">物理位置</span><span class="info-v">{{ st.location || '—' }}</span></div>
            <div class="info-item"><span class="info-k">注册时间</span><span class="info-v">{{ active.registered_at || '—' }}</span></div>
            <div class="info-item"><span class="info-k">最近自检</span><span class="info-v">{{ active.verify_summary.checked_at || '—' }}</span></div>
          </div>

          <!-- 设备配置表 -->
          <div class="sub-title">设备连接配置</div>
          <div class="table-wrap">
            <table class="tb-table">
              <thead>
                <tr>
                  <th>设备</th><th>型号 / 厂商</th><th>接口</th><th>连接配置</th><th>协议</th><th>程控</th><th>最近自检</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="d in devs" :key="d.id || d.name">
                  <td class="td-name">{{ d.name }}</td>
                  <td>
                    <div>{{ d.model || '—' }}</div>
                    <div class="td-muted">{{ d.vendor || '—' }}</div>
                  </td>
                  <td><span class="iface">{{ d.interface || 'NONE' }}</span></td>
                  <td class="mono td-target">{{ devTarget(d) }}</td>
                  <td class="td-muted">{{ d.protocol || '—' }}</td>
                  <td>
                    <span class="badge" :class="d.programmable ? 'accent' : 'idle'">{{ d.programmable ? '可编程' : '手动' }}</span>
                  </td>
                  <td>
                    <span v-if="deviceResult(d)" class="check-badge" :class="checkClass(deviceResult(d).status)">
                      {{ checkText(deviceResult(d).status) }}
                    </span>
                    <span v-else class="td-muted">—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 自检清单 -->
          <div class="sub-title">
            自检清单
            <span v-if="active.verification" class="sub-title-note">
              （{{ active.verification.mode === 'simulate' ? '离线模拟' : '真实探测' }} · {{ active.verification.checked_at }}）
            </span>
          </div>
          <div v-if="!verification" class="empty-line">该测试台尚未执行连通性自检，点击下方「重新自检」开始。</div>
          <template v-else>
            <div class="verify-conclusion" :class="verifyClass(verification.overall)">
              {{ verification.conclusion }}
            </div>
            <div class="check-grid">
              <div v-for="c in configChecks" :key="c.key" class="check-item" :class="checkClass(c.status)">
                <span class="check-dot"></span>
                <div class="check-body">
                  <div class="check-label">{{ c.label }}</div>
                  <div class="check-detail">{{ c.detail }}</div>
                </div>
                <span class="check-badge" :class="checkClass(c.status)">{{ checkText(c.status) }}</span>
              </div>
              <div v-for="c in deviceChecks" :key="c.key" class="check-item" :class="checkClass(c.status)">
                <span class="check-dot"></span>
                <div class="check-body">
                  <div class="check-label">{{ c.label }}<span class="check-iface">{{ c.interface }}</span></div>
                  <div class="check-detail">
                    {{ c.target ? c.target + ' · ' : '' }}{{ c.detail }}
                    <span v-if="c.simulated" class="sim-tag">模拟</span>
                  </div>
                </div>
                <span class="check-badge" :class="checkClass(c.status)">{{ checkText(c.status) }}</span>
              </div>
            </div>
          </template>

          <!-- 操作 -->
          <div class="actions">
            <button class="btn primary" :disabled="busy" @click="runVerify('real')">重新自检（真实探测）</button>
            <button class="btn" :disabled="busy" @click="runVerify('simulate')">模拟自检（离线演示）</button>
            <router-link :to="`/testbenches/register?id=${active.id}`" class="btn">编辑配置</router-link>
            <router-link to="/equipment" class="btn ghost">查看装备清单</router-link>
            <button class="btn ghost danger" :disabled="busy" @click="removeBench">删除测试台</button>
          </div>
          <div v-if="tip" class="tip" :class="tipErr ? 'tip-err' : 'tip-ok'">{{ tip }}</div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.tbn-page { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }

/* ---- 面板 ---- */
.tbn-panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.panel-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.panel-title { font-size: 15px; font-weight: 700; letter-spacing: 0.5px; }
.panel-sub { color: var(--muted); font-size: 12.5px; }
.sub-title { font-size: 13.5px; font-weight: 700; color: var(--accent); letter-spacing: 0.5px; margin-top: 2px; }
.sub-title-note { color: var(--muted); font-weight: 400; font-size: 12px; }

/* ---- 产品 / 子系统 归属标识 ---- */
.dh-branch {
  font-family: var(--mono);
  font-size: 11.5px;
  font-weight: 700;
  color: var(--accent);
  background: rgba(var(--accent-rgb), 0.08);
  border: 1px solid rgba(var(--accent-rgb), 0.25);
  border-radius: 5px;
  padding: 1px 7px;
}

/* ---- 指标块 ---- */
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.metric {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.metric-label { color: var(--muted); font-size: 12px; letter-spacing: 0.5px; }
.metric-value { font-size: 24px; font-weight: 800; font-family: var(--mono); }
.metric-value.accent { color: var(--accent); }
.metric-value.ok { color: var(--ok); }
.metric-value.err { color: var(--err); }
.metric-foot { color: var(--muted); font-size: 11.5px; }

/* ---- 测试台标签 ---- */
.tb-tabs { display: flex; flex-wrap: wrap; gap: 8px; }
.tb-tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: 9px 9px 0 0;
  border: 1px solid var(--border);
  border-bottom: 2px solid transparent;
  background: var(--panel-2);
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  transition: all 0.15s ease;
}
.tb-tab:hover { color: var(--text); border-color: rgba(var(--accent-rgb), 0.4); }
.tb-tab.active { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.45); border-bottom-color: var(--accent); background: rgba(var(--accent-rgb), 0.1); }
.tb-tab-add { border-style: dashed; color: var(--accent); }
.tb-tab-restore { border-style: dashed; color: var(--muted); cursor: pointer; background: var(--panel); font: inherit; }
.tb-tab-restore:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.tb-tab-restore:disabled { opacity: .55; cursor: not-allowed; }
.empty-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; }
.tb-tab-name { color: inherit; }
.tb-tab-serial { font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
.tb-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex: 0 0 auto; }
.tb-dot.v-pass { background: var(--ok); box-shadow: 0 0 8px rgba(21, 128, 61, 0.7); }
.tb-dot.v-warn { background: var(--warn); box-shadow: 0 0 8px rgba(180, 83, 9, 0.7); }
.tb-dot.v-fail { background: var(--err); box-shadow: 0 0 8px rgba(220, 38, 38, 0.7); }
.tb-tag { padding: 1px 8px; border-radius: 999px; font-size: 11px; border: 1px solid var(--border); color: var(--muted); }
.tb-tag.draft { color: var(--warn); border-color: rgba(180, 83, 9, 0.4); background: rgba(180, 83, 9, 0.1); }

/* ---- 详情 ---- */
.tb-detail { display: flex; flex-direction: column; gap: 14px; border-top: 1px solid var(--border); padding-top: 14px; }
.detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; flex-wrap: wrap; }
.dh-title { font-size: 17px; font-weight: 800; }
.dh-meta { color: var(--muted); font-size: 12.5px; margin-top: 4px; display: flex; gap: 8px; flex-wrap: wrap; }
.dh-meta .mono { color: var(--accent); font-family: var(--mono); }
.sep { opacity: 0.5; }
.dh-right { display: flex; gap: 8px; }

.info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
.info-item {
  display: flex; flex-direction: column; gap: 3px;
  padding: 9px 12px; border-radius: 9px;
  background: var(--panel-2); border: 1px solid var(--border);
}
.info-k { color: var(--muted); font-size: 11.5px; }
.info-v { font-size: 13.5px; font-weight: 600; word-break: break-all; }

/* ---- 表格 ---- */
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
.tb-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 720px; }
.tb-table th {
  text-align: left; padding: 9px 12px; font-size: 12px; font-weight: 600;
  color: var(--muted); background: var(--panel-2); border-bottom: 1px solid var(--border); white-space: nowrap;
}
.tb-table td { padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
.tb-table tr:last-child td { border-bottom: none; }
.td-name { font-weight: 600; }
.td-muted { color: var(--muted); font-size: 12px; }
.td-target { color: var(--accent); word-break: break-all; }
.mono { font-family: var(--mono); }
.iface { padding: 1px 8px; border-radius: 6px; font-size: 11.5px; font-family: var(--mono); background: rgba(var(--accent-2-rgb), 0.12); border: 1px solid rgba(var(--accent-2-rgb), 0.3); color: var(--accent-2); }

/* ---- 徽标 ---- */
.badge {
  display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 999px;
  font-size: 11.5px; font-weight: 600; border: 1px solid var(--border); color: var(--muted); white-space: nowrap;
}
.badge.ok, .badge.v-pass { color: var(--ok); border-color: rgba(21, 128, 61, 0.4); background: rgba(21, 128, 61, 0.1); }
.badge.v-warn { color: var(--warn); border-color: rgba(180, 83, 9, 0.4); background: rgba(180, 83, 9, 0.1); }
.badge.v-fail { color: var(--err); border-color: rgba(220, 38, 38, 0.4); background: rgba(220, 38, 38, 0.1); }
.badge.accent { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.4); background: rgba(var(--accent-rgb), 0.1); }
.badge.idle { color: var(--muted); background: var(--panel-2); }

/* ---- 自检清单 ---- */
.verify-conclusion { padding: 10px 14px; border-radius: 9px; font-size: 13px; font-weight: 600; border: 1px solid var(--border); }
.verify-conclusion.v-pass { color: var(--ok); background: rgba(21, 128, 61, 0.1); border-color: rgba(21, 128, 61, 0.35); }
.verify-conclusion.v-warn { color: var(--warn); background: rgba(180, 83, 9, 0.1); border-color: rgba(180, 83, 9, 0.35); }
.verify-conclusion.v-fail { color: var(--err); background: rgba(220, 38, 38, 0.1); border-color: rgba(220, 38, 38, 0.35); }
.check-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 10px; }
.check-item {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 12px; border-radius: 9px;
  background: var(--panel-2); border: 1px solid var(--border);
}
.check-item.c-pass { border-color: rgba(21, 128, 61, 0.3); }
.check-item.c-fail { border-color: rgba(220, 38, 38, 0.4); background: rgba(220, 38, 38, 0.07); }
.check-item.c-skip { border-color: rgba(180, 83, 9, 0.35); background: rgba(180, 83, 9, 0.06); }
.check-dot { width: 9px; height: 9px; border-radius: 50%; margin-top: 5px; flex: 0 0 auto; background: var(--muted); }
.c-pass .check-dot { background: var(--ok); }
.c-fail .check-dot { background: var(--err); }
.c-skip .check-dot { background: var(--warn); }
.check-body { flex: 1; min-width: 0; }
.check-label { font-size: 13px; font-weight: 600; }
.check-iface { margin-left: 6px; font-family: var(--mono); font-size: 11px; color: var(--muted); }
.check-detail { color: var(--muted); font-size: 12px; margin-top: 3px; word-break: break-all; }
.sim-tag { margin-left: 6px; padding: 0 6px; border-radius: 5px; font-size: 11px; color: var(--warn); border: 1px solid rgba(180, 83, 9, 0.4); }
.check-badge { padding: 1px 8px; border-radius: 6px; font-size: 11.5px; font-weight: 700; white-space: nowrap; }
.check-badge.c-pass { color: var(--ok); background: rgba(21, 128, 61, 0.12); border: 1px solid rgba(21, 128, 61, 0.35); }
.check-badge.c-fail { color: var(--err); background: rgba(220, 38, 38, 0.12); border: 1px solid rgba(220, 38, 38, 0.35); }
.check-badge.c-skip { color: var(--warn); background: rgba(180, 83, 9, 0.12); border: 1px solid rgba(180, 83, 9, 0.35); }
.empty-line { color: var(--muted); font-size: 13px; }

/* ---- 操作区 ---- */
.actions { display: flex; gap: 10px; flex-wrap: wrap; padding-top: 4px; }
.actions .btn.danger { color: var(--err); }
.actions .btn.danger:hover:not(:disabled) { border-color: var(--err); }

/* ---- 空态 / 提示 ---- */
.empty { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 30px 16px; text-align: center; }
.empty-title { font-size: 15px; font-weight: 700; }
.empty-text { color: var(--muted); font-size: 13px; max-width: 620px; line-height: 1.7; }
.empty-btn { text-decoration: none; }
.tip { padding: 10px 14px; border-radius: 9px; font-size: 13px; }
.tip-loading { color: var(--muted); background: var(--panel); border: 1px solid var(--border); }
.tip-err { color: var(--err); background: rgba(220, 38, 38, 0.1); border: 1px solid rgba(220, 38, 38, 0.3); }
.tip-ok { color: var(--ok); background: rgba(21, 128, 61, 0.1); border: 1px solid rgba(21, 128, 61, 0.3); }
</style>
