<script setup>
/**
 * 系统设置 · 模式权限矩阵
 *
 * 列 = 运行档位（生产 / 调试 / 仿真），行 = 功能项
 * （真机连接 / 本地记录留存 / 结果上报云端 / 单步执行 / 工单校验）。
 * 数据源：GET / PUT /api/settings/permissions，落库 backend/ate.db 的 mode_permission 表。
 * 保存后由执行侧统一按 ate_db.permission_of(mode, capability) 取生效值，前端不自行判定。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { runMode, simulate } from '../runmode'

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const tip = ref('')
const tipErr = ref(false)

const modes = ref([])
const caps = ref([])
const matrix = ref({})       // 服务端已保存的值
const draft = ref({})        // 界面正在编辑的值
const defaults = ref({})
const updatedAt = ref('')

const clone = (v) => JSON.parse(JSON.stringify(v || {}))

/** 当前生效档位：生产 / 调试 / 仿真 —— 用于高亮对应列 */
const activeModeKey = computed(() => {
  if (runMode.value === 'production') return 'production'
  return simulate.value ? 'simulate' : 'debug'
})

const dirty = computed(() => JSON.stringify(matrix.value) !== JSON.stringify(draft.value))

function countOn(modeKey) {
  const row = draft.value[modeKey] || {}
  return caps.value.filter((c) => !!row[c.key]).length
}

function toggle(modeKey, capKey) {
  const row = draft.value[modeKey]
  if (!row) return
  row[capKey] = !row[capKey]
  tip.value = ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.getPermissions()
    if (!res.success) throw new Error(res.message || '读取失败')
    modes.value = res.modes || []
    caps.value = res.capabilities || []
    matrix.value = clone(res.matrix)
    draft.value = clone(res.matrix)
    defaults.value = clone(res.defaults)
    updatedAt.value = res.updated_at || ''
  } catch (e) {
    error.value = '读取权限矩阵失败：' + e.message
  } finally {
    loading.value = false
  }
}

function loadDefaults() {
  draft.value = clone(defaults.value)
  tipErr.value = false
  tip.value = '已载入默认矩阵，点击「保存修改」后生效'
}

async function save() {
  if (!dirty.value || saving.value) return
  saving.value = true
  tip.value = ''
  try {
    const res = await api.savePermissions(draft.value)
    if (!res.success) throw new Error(res.message || '保存失败')
    matrix.value = clone(res.matrix || draft.value)
    draft.value = clone(res.matrix || draft.value)
    tipErr.value = false
    tip.value = '已保存，新发起的执行按本矩阵生效'
    const now = new Date()
    const p = (n) => String(n).padStart(2, '0')
    updatedAt.value = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())} ${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`
  } catch (e) {
    tipErr.value = true
    tip.value = '保存失败：' + e.message
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="set-page">
    <!-- 页头 + 页签 -->
    <section class="set-panel set-head-panel">
      <div class="head-left">
        <div class="set-title">系统设置</div>
        <div class="set-sub">运行档位与功能限制的集中配置，保存后即成为执行侧的唯一依据</div>
      </div>
      <div class="head-meta">
        <span class="meta-item">最近保存<b>{{ updatedAt || '—' }}</b></span>
      </div>
    </section>

    <section class="set-panel">
      <div class="tabs">
        <button class="tab active" type="button">权限矩阵</button>
        <span class="tab-note">当前仅开放此页，其余设置项后续提供</span>
      </div>

      <div v-if="error" class="notice err">{{ error }}</div>

      <div v-else-if="loading" class="loading">正在读取权限矩阵…</div>

      <template v-else>
        <div class="pm-top">
          <div class="pm-intro">
            <div class="pm-title">权限矩阵</div>
            <div class="pm-desc">
              列为运行档位，行为功能项。开关处于「允许」表示该档位可使用此项功能，处于「限制」表示该档位下不可用。
            </div>
          </div>
          <div class="pm-legend">
            <span class="lg"><i class="lg-dot on"></i>允许</span>
            <span class="lg"><i class="lg-dot off"></i>限制</span>
            <span class="lg"><i class="lg-bar"></i>当前档位列</span>
          </div>
        </div>

        <div class="pm-scroll">
          <table class="pm">
            <thead>
              <tr>
                <th class="col-cap">
                  <span class="th-main">功能项</span>
                  <span class="th-sub">共 {{ caps.length }} 项</span>
                </th>
                <th
                  v-for="m in modes"
                  :key="m.key"
                  class="col-mode"
                  :class="{ 'is-current': m.key === activeModeKey }"
                >
                  <span class="mode-name">
                    {{ m.label }}
                    <em v-if="m.key === activeModeKey">当前</em>
                  </span>
                  <span class="mode-desc">{{ m.desc }}</span>
                  <span class="mode-count">已开放 {{ countOn(m.key) }} / {{ caps.length }}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(c, i) in caps" :key="c.key">
                <td class="cap-cell">
                  <div class="cap-main">
                    <span class="cap-code">F{{ String(i + 1).padStart(2, '0') }}</span>
                    <span class="cap-name">{{ c.label }}</span>
                    <span v-if="c.risk" class="cap-risk">高风险</span>
                  </div>
                  <div class="cap-desc">{{ c.desc }}</div>
                  <div class="cap-scope">生效环节 · {{ c.scope }}</div>
                </td>
                <td
                  v-for="m in modes"
                  :key="m.key"
                  class="cell"
                  :class="{ 'is-current': m.key === activeModeKey }"
                >
                  <button
                    type="button"
                    class="sw"
                    :class="{ on: !!(draft[m.key] || {})[c.key] }"
                    role="switch"
                    :aria-checked="(draft[m.key] || {})[c.key] ? 'true' : 'false'"
                    :title="`${m.label} · ${c.label}`"
                    @click="toggle(m.key, c.key)"
                  >
                    <span class="sw-track"><span class="sw-dot"></span></span>
                    <span class="sw-text">{{ (draft[m.key] || {})[c.key] ? '允许' : '限制' }}</span>
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="pm-foot">
          <div class="foot-btns">
            <button class="btn primary" type="button" :disabled="!dirty || saving" @click="save">
              {{ saving ? '保存中…' : '保存修改' }}
            </button>
            <button class="btn" type="button" :disabled="saving" @click="loadDefaults">载入默认值</button>
            <span v-if="dirty" class="dirty-tag">有未保存的修改</span>
          </div>
          <div v-if="tip" class="foot-tip" :class="tipErr ? 'err' : 'ok'">{{ tip }}</div>
        </div>
      </template>
    </section>

    <!-- 使用说明 -->
    <section class="set-panel">
      <div class="pm-title">生效规则</div>
      <div class="notes">
        <div class="note">
          <span class="note-no">01</span>
          <div>
            <div class="note-name">按档位取用</div>
            <div class="note-text">执行启动时读取当前档位对应的一列，逐项判定是否放行，档位之间互不影响。</div>
          </div>
        </div>
        <div class="note">
          <span class="note-no">02</span>
          <div>
            <div class="note-name">保存即生效</div>
            <div class="note-text">保存后对之后发起的执行生效；正在运行的轮次沿用启动时的取值，避免中途改变判定。</div>
          </div>
        </div>
        <div class="note">
          <span class="note-no">03</span>
          <div>
            <div class="note-name">风险项提示</div>
            <div class="note-text">标注「高风险」的功能项涉及真实设备与外部系统，放开前需确认现场条件与数据口径。</div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.set-page {
  flex: 1;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
  min-width: 0;
}

.set-panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ---------- 页头 ---------- */
.set-head-panel { flex-direction: row; align-items: center; justify-content: space-between; gap: 16px; }
.head-left { display: flex; flex-direction: column; gap: 5px; }
.set-title { font-size: 18px; font-weight: 800; letter-spacing: 0.6px; }
.set-sub { color: var(--muted); font-size: 12.5px; }
.head-meta { display: flex; gap: 14px; }
.meta-item { color: var(--muted); font-size: 12px; display: flex; flex-direction: column; gap: 2px; text-align: right; }
.meta-item b { color: var(--text); font-family: var(--mono); font-size: 12.5px; font-weight: 600; }

/* ---------- 页签 ---------- */
.tabs { display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
.tab {
  border: 1px solid var(--border-strong);
  background: #fff;
  color: var(--muted);
  padding: 7px 16px;
  border-radius: 7px 7px 0 0;
  font-size: 13.5px;
  font-weight: 600;
  cursor: default;
}
.tab.active {
  background: linear-gradient(135deg, #7c3aed, #6d28d9);
  border-color: transparent;
  color: #fff;
}
.tab-note { color: var(--muted); font-size: 11.5px; }

/* ---------- 矩阵说明 ---------- */
.pm-top { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.pm-intro { display: flex; flex-direction: column; gap: 4px; }
.pm-title { font-size: 15px; font-weight: 700; letter-spacing: 0.4px; }
.pm-desc { color: var(--muted); font-size: 12.5px; line-height: 1.6; max-width: 760px; }
.pm-legend { display: flex; align-items: center; gap: 14px; color: var(--muted); font-size: 11.5px; }
.lg { display: inline-flex; align-items: center; gap: 6px; }
.lg-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.lg-dot.on { background: var(--accent); }
.lg-dot.off { background: #fff; border: 1px solid var(--border-strong); }
.lg-bar { width: 3px; height: 12px; background: var(--accent); border-radius: 2px; display: inline-block; }

/* ---------- 矩阵表 ---------- */
.pm-scroll { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.pm { width: 100%; border-collapse: collapse; table-layout: fixed; }
.pm th, .pm td { border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); vertical-align: top; }
.pm th:last-child, .pm td:last-child { border-right: none; }
.pm tbody tr:last-child td { border-bottom: none; }

.pm thead th { background: var(--panel-2); padding: 12px 14px; text-align: left; }
.col-cap { width: 34%; }
.col-mode { width: 22%; }
.th-main { display: block; font-size: 13.5px; font-weight: 700; }
.th-sub { display: block; color: var(--muted); font-size: 11px; margin-top: 3px; font-weight: 500; }
.mode-name { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 700; color: var(--text); }
.mode-name em {
  font-style: normal;
  font-size: 10px;
  font-weight: 700;
  color: #fff;
  background: var(--accent);
  border-radius: 4px;
  padding: 1px 5px;
  letter-spacing: 0.5px;
}
.mode-desc { display: block; color: var(--muted); font-size: 11.5px; line-height: 1.55; margin-top: 5px; font-weight: 400; }
.mode-count { display: block; margin-top: 6px; font-family: var(--mono); font-size: 11px; color: var(--accent); }

.pm thead th.is-current { background: linear-gradient(180deg, rgba(var(--accent-rgb), 0.1), rgba(var(--accent-rgb), 0.04)); }
.pm td.is-current { background: rgba(var(--accent-rgb), 0.035); }

.cap-cell { padding: 12px 14px; }
.cap-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cap-code {
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--muted);
  border: 1px solid var(--border);
  background: #fff;
  border-radius: 4px;
  padding: 1px 5px;
}
.cap-name { font-size: 13.5px; font-weight: 700; }
.cap-risk {
  font-size: 10px;
  color: var(--err);
  border: 1px solid rgba(220, 38, 38, 0.35);
  background: rgba(220, 38, 38, 0.06);
  border-radius: 4px;
  padding: 1px 5px;
  font-weight: 600;
}
.cap-desc { color: var(--muted); font-size: 12px; line-height: 1.6; margin-top: 5px; }
.cap-scope { color: var(--muted); font-size: 11px; margin-top: 5px; font-family: var(--mono); opacity: 0.85; }

.cell { padding: 12px 14px; }

/* ---------- 开关 ---------- */
.sw {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--border-strong);
  background: #fff;
  border-radius: 999px;
  padding: 4px 12px 4px 5px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.sw:hover { border-color: var(--accent); box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.12); }
.sw-track {
  width: 32px;
  height: 18px;
  border-radius: 999px;
  background: #e6e1f5;
  border: 1px solid var(--border-strong);
  position: relative;
  flex: 0 0 32px;
  transition: background 0.15s, border-color 0.15s;
}
.sw-dot {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  border: 1px solid var(--border-strong);
  transition: transform 0.15s, border-color 0.15s;
}
.sw-text { font-size: 12px; color: var(--muted); font-weight: 600; min-width: 26px; }
.sw.on { border-color: var(--accent); background: rgba(var(--accent-rgb), 0.06); }
.sw.on .sw-track { background: var(--accent); border-color: var(--accent); }
.sw.on .sw-dot { transform: translateX(14px); border-color: var(--accent); }
.sw.on .sw-text { color: var(--accent); }

/* ---------- 底部操作 ---------- */
.pm-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding-top: 4px;
}
.foot-btns { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.dirty-tag {
  font-size: 11.5px;
  color: var(--warn);
  border: 1px solid rgba(180, 83, 9, 0.3);
  background: rgba(180, 83, 9, 0.06);
  border-radius: 999px;
  padding: 3px 10px;
  font-weight: 600;
}
.foot-tip { font-size: 12.5px; }
.foot-tip.ok { color: var(--ok); }
.foot-tip.err { color: var(--err); }

.notice { padding: 12px 14px; border-radius: 8px; font-size: 13px; }
.notice.err { color: var(--err); background: rgba(220, 38, 38, 0.06); border: 1px solid rgba(220, 38, 38, 0.25); }
.loading { color: var(--muted); font-size: 13px; padding: 18px 4px; }

/* ---------- 生效规则 ---------- */
.notes { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
.note {
  display: flex;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel-2);
}
.note-no { font-family: var(--mono); font-size: 12px; color: var(--accent); font-weight: 700; }
.note-name { font-size: 13px; font-weight: 700; }
.note-text { color: var(--muted); font-size: 12px; line-height: 1.65; margin-top: 4px; }

@media (max-width: 900px) {
  .set-page { padding: 14px; }
  .set-head-panel { flex-direction: column; align-items: flex-start; }
  .head-meta { display: none; }
  .col-cap { width: 40%; }
  .mode-desc { display: none; }
}
</style>
