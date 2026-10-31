<script setup>
/**
 * 元数据管理 · 机器常数模型
 *
 * 常数模型 = 结构（字段 / 类型 / 单位 / 范围 / 默认值）；标定后才产生值。
 * 一个「版本」是一次全量导入，同版本再次导入为覆盖（默认保留仍存在字段的标定值）。
 * 归属按装备树组织：产品 -> 子系统 -> PN -> 常数模型。
 * 数据源：/api/constants/*（落库 backend/ate.db 的 constant_version / constant_model / constant_value）
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const loading = ref(true)
const err = ref('')
const versions = ref([])
const activeVersion = ref('')
const version = ref('')
const tree = ref([])
const totals = ref({ pn_count: 0, model_count: 0, field_count: 0, filled: 0 })
const selected = ref(null)      // { product, subsystem, pn }
const models = ref([])
const expanded = ref('')
const valueDraft = ref({})
const savingValues = ref(false)
const valueMsg = ref('')

// 导入向导
const showImport = ref(false)
const importMode = ref('overwrite')
const importText = ref('')
const importFile = ref(null)     // { name, size, base64 }
const preview = ref(null)
const previewing = ref(false)
const importing = ref(false)
const importMsg = ref('')
const importErr = ref('')

const fillRate = computed(() => {
  const total = totals.value.field_count || 0
  if (!total) return '—'
  return Math.round(((totals.value.filled || 0) / total) * 100) + '%'
})

const selectedLabel = computed(() => {
  const s = selected.value
  if (!s) return ''
  return [s.product, s.subsystem, s.pn].filter(Boolean).join(' / ')
})

async function loadTree(v = '') {
  loading.value = true
  err.value = ''
  try {
    const res = await api.getConstantsOverview(v)
    if (!res.success) throw new Error(res.message || '读取失败')
    tree.value = res.tree || []
    totals.value = res.totals || { pn_count: 0, model_count: 0, field_count: 0, filled: 0 }
    versions.value = res.versions || []
    activeVersion.value = res.active || ''
    version.value = res.version || res.active || ''
    if (selected.value) {
      const still = findPn(selected.value.pn)
      if (!still) {
        selected.value = null
        models.value = []
        expanded.value = ''
      }
    }
  } catch (e) {
    err.value = '读取常数模型失败：' + e.message
  } finally {
    loading.value = false
  }
}

function findPn(pn) {
  for (const p of tree.value) {
    for (const s of p.subsystems || []) {
      for (const n of s.pns || []) {
        if (n.pn === pn) return { product: p.id, product_name: p.name, subsystem: s.id, subsystem_name: s.name, ...n }
      }
    }
  }
  return null
}

async function pickVersion() {
  selected.value = null
  models.value = []
  expanded.value = ''
  await loadTree(version.value)
}

async function selectPn(product, subsystem, pn) {
  selected.value = { product, subsystem, pn }
  expanded.value = ''
  valueMsg.value = ''
  await loadModels()
}

async function loadModels() {
  if (!selected.value) return
  models.value = []
  try {
    const res = await api.getConstantsByPn(selected.value.pn, version.value)
    if (!res.success) throw new Error(res.message || '读取失败')
    models.value = res.models || []
    const draft = {}
    for (const m of models.value) {
      draft[m.model_key] = {}
      for (const f of m.fields || []) {
        const hit = (m.values || {})[f.key]
        draft[m.model_key][f.key] = hit ? hit.value : ''
      }
    }
    valueDraft.value = draft
  } catch (e) {
    err.value = '读取 PN 常数模型失败：' + e.message
  }
}

function toggleModel(m) {
  expanded.value = expanded.value === m.model_key ? '' : m.model_key
  valueMsg.value = ''
}

function fieldRange(f) {
  const lo = f.min === null || f.min === undefined ? '' : f.min
  const hi = f.max === null || f.max === undefined ? '' : f.max
  if (lo === '' && hi === '') return '—'
  return `${lo === '' ? '-∞' : lo} ~ ${hi === '' ? '+∞' : hi}`
}

function defaultValue(f) {
  return f.default === null || f.default === undefined || f.default === '' ? '—' : f.default
}

async function saveValues(m) {
  savingValues.value = true
  valueMsg.value = ''
  try {
    const values = {}
    for (const f of m.fields || []) {
      const raw = valueDraft.value[m.model_key][f.key]
      values[f.key] = raw === '' || raw === null ? null : raw
    }
    const res = await api.saveConstantValues({
      version: version.value,
      pn: selected.value.pn,
      model_key: m.model_key,
      values,
      source: 'manual'
    })
    if (!res.success) throw new Error(res.message || '保存失败')
    valueMsg.value = `${m.model_key} 已保存标定值（${res.filled} 项）`
    await loadModels()
    await loadTree(version.value)
  } catch (e) {
    valueMsg.value = '保存失败：' + e.message
  } finally {
    savingValues.value = false
  }
}

async function makeActive(v) {
  try {
    const res = await api.setActiveConstantVersion(v)
    if (!res.success) throw new Error(res.message || '切换失败')
    await loadTree(v)
  } catch (e) {
    err.value = '切换生效版本失败：' + e.message
  }
}

// ---------- 导入向导 ----------

function openImport() {
  showImport.value = true
  preview.value = null
  importMsg.value = ''
  importErr.value = ''
  importFile.value = null
  importText.value = ''
  importMode.value = 'overwrite'
}

function closeImport() {
  showImport.value = false
}

function onFile(ev) {
  const file = ev.target.files && ev.target.files[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    const bytes = new Uint8Array(reader.result)
    let bin = ''
    for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i])
    importFile.value = { name: file.name, size: file.size, base64: btoa(bin) }
    importText.value = ''
    preview.value = null
    importErr.value = ''
  }
  reader.readAsArrayBuffer(file)
}

async function doPreview() {
  previewing.value = true
  importErr.value = ''
  importMsg.value = ''
  preview.value = null
  try {
    const payload = importFile.value
      ? { content_base64: importFile.value.base64, filename: importFile.value.name }
      : { content: importText.value, filename: 'pasted.json' }
    const res = await api.previewConstants(payload)
    if (!res.success) throw new Error(res.message || '解析失败')
    preview.value = res
  } catch (e) {
    importErr.value = '解析失败：' + e.message
  } finally {
    previewing.value = false
  }
}

async function doImport() {
  if (!preview.value) return
  importing.value = true
  importErr.value = ''
  try {
    const payload = importFile.value
      ? {
          content_base64: importFile.value.base64,
          filename: importFile.value.name,
          mode: importMode.value,
          keep_values: true
        }
      : {
          content: importText.value,
          filename: 'pasted.json',
          mode: importMode.value,
          keep_values: true
        }
    const res = await api.importConstants(payload)
    if (!res.success) throw new Error(res.message || '导入失败')
    importMsg.value = res.message
    preview.value = null
    await loadTree(res.version || version.value)
  } catch (e) {
    importErr.value = '导入失败：' + e.message
  } finally {
    importing.value = false
  }
}

onMounted(() => loadTree(''))
</script>

<template>
  <div class="cn-page">
    <section class="cn-panel cn-head">
      <div class="head-left">
        <div class="cn-title">机器常数模型</div>
        <div class="cn-sub">
          按 PN 号管理常数结构；一次全量导入为一个版本，同版本再次导入即覆盖（保留仍存在字段的标定值）
        </div>
      </div>
      <div class="head-right">
        <label class="ver-label">
          版本
          <select v-model="version" class="sel" @change="pickVersion">
            <option v-for="v in versions" :key="v.version" :value="v.version">
              {{ v.version }}{{ v.is_active ? '（生效）' : '' }}
            </option>
            <option v-if="!versions.length" value="">— 暂无版本 —</option>
          </select>
        </label>
        <button
          v-if="version && version !== activeVersion"
          class="btn"
          type="button"
          @click="makeActive(version)"
        >设为生效</button>
        <span v-if="version && version === activeVersion" class="badge-on">当前生效版本</span>
        <button class="btn primary" type="button" @click="openImport">全量导入</button>
      </div>
    </section>

    <section v-if="err" class="cn-panel notice err">{{ err }}</section>

    <section class="metrics">
      <div class="metric">
        <span class="metric-label">PN 数</span>
        <span class="metric-value">{{ totals.pn_count }}</span>
        <span class="metric-foot">当前版本 {{ version || '—' }}</span>
      </div>
      <div class="metric">
        <span class="metric-label">常数模型</span>
        <span class="metric-value">{{ totals.model_count }}</span>
        <span class="metric-foot">结构条目</span>
      </div>
      <div class="metric">
        <span class="metric-label">字段总数</span>
        <span class="metric-value">{{ totals.field_count }}</span>
        <span class="metric-foot">待标定项</span>
      </div>
      <div class="metric">
        <span class="metric-label">已标定</span>
        <span class="metric-value accent">{{ totals.filled }}</span>
        <span class="metric-foot">填充率 {{ fillRate }}</span>
      </div>
    </section>

    <div class="cn-body">
      <!-- 左：PN 装备树 -->
      <aside class="cn-panel cn-side">
        <div class="side-head">
          <span class="panel-title">装备树（PN 维度）</span>
          <span class="panel-sub">{{ tree.length }} 个产品节点</span>
        </div>
        <div v-if="loading" class="loading">正在读取…</div>
        <div v-else-if="!tree.length" class="empty">
          暂无常数模型。点击「全量导入」导入某个标定版本的常数结构包。
        </div>
        <div v-else class="tree">
          <div v-for="p in tree" :key="p.id" class="tree-prod">
            <div class="node-prod">
              <span class="node-name">{{ p.name }}</span>
              <span class="node-meta">{{ p.model_count }} 模型 / {{ p.field_count }} 字段</span>
            </div>
            <div v-for="s in p.subsystems || []" :key="s.id" class="tree-sub">
              <div class="node-sub">
                <span class="node-name">{{ s.name }}</span>
                <span class="node-meta">{{ s.pn_count }} PN</span>
              </div>
              <button
                v-for="n in s.pns || []"
                :key="n.pn"
                type="button"
                class="node-pn"
                :class="{ active: selected && selected.pn === n.pn }"
                @click="selectPn(p.id, s.id, n.pn)"
              >
                <span class="pn-code">{{ n.pn }}</span>
                <span class="pn-meta">{{ n.model_count }} 模型 · 已标定 {{ n.filled }}/{{ n.field_count }}</span>
              </button>
            </div>
          </div>
        </div>
      </aside>

      <!-- 右：模型与字段 -->
      <section class="cn-panel cn-main">
        <div class="side-head">
          <span class="panel-title">{{ selected ? selected.pn : '常数模型' }}</span>
          <span v-if="selected" class="panel-sub">{{ selectedLabel }} · {{ models.length }} 个模型</span>
          <span v-else class="panel-sub">从左侧选择 PN 查看其常数模型结构</span>
        </div>

        <div v-if="!selected" class="empty">
          常数模型以 PN 为维度管理。选择左侧任一 PN，可查看该 PN 下的模型结构、标定值与填充情况。
        </div>

        <div v-else-if="!models.length" class="empty">该 PN 在当前版本下没有常数模型。</div>

        <div v-else class="model-list">
          <div v-for="m in models" :key="m.model_key" class="model">
            <button type="button" class="model-head" @click="toggleModel(m)">
              <span class="m-key">{{ m.model_key }}</span>
              <span class="m-name">{{ m.name }}</span>
              <span class="m-tag" v-if="m.category">{{ m.category }}</span>
              <span class="m-stat">字段 {{ m.field_count }} · 已标定 {{ m.filled }}</span>
              <span class="m-time">{{ m.updated_at }}</span>
              <span class="m-arrow">{{ expanded === m.model_key ? '−' : '+' }}</span>
            </button>

            <div v-if="expanded === m.model_key" class="model-body">
              <div v-if="m.description" class="m-desc">{{ m.description }}</div>
              <table class="fields">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>名称</th>
                    <th>类型</th>
                    <th>单位</th>
                    <th>范围</th>
                    <th>默认值</th>
                    <th>标定值</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="f in m.fields" :key="f.key">
                    <td class="mono">{{ f.key }}</td>
                    <td>{{ f.label }}</td>
                    <td class="mono">{{ f.type }}</td>
                    <td class="mono">{{ f.unit || '—' }}</td>
                    <td class="mono">{{ fieldRange(f) }}</td>
                    <td class="mono">{{ defaultValue(f) }}</td>
                    <td>
                      <input
                        v-model="valueDraft[m.model_key][f.key]"
                        class="val-input"
                        :placeholder="(m.values && m.values[f.key]) ? '' : '未标定'"
                      />
                    </td>
                  </tr>
                </tbody>
              </table>
              <div class="model-foot">
                <span class="model-note">
                  结构校验和 {{ m.struct_checksum }} · 标定值仅用于运行期填充，不改变结构
                </span>
                <button class="btn primary sm" type="button" :disabled="savingValues" @click="saveValues(m)">
                  {{ savingValues ? '保存中…' : '保存标定值' }}
                </button>
              </div>
              <div v-if="valueMsg" class="ok-line">{{ valueMsg }}</div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- 全量导入向导 -->
    <div v-if="showImport" class="mask" @click.self="closeImport">
      <div class="dialog">
        <div class="dlg-head">
          <span class="dlg-title">全量导入常数模型</span>
          <button class="dlg-close" type="button" @click="closeImport">×</button>
        </div>

        <div class="dlg-body">
          <div class="dlg-step">
            <span class="step-no">1</span>
            <div class="step-main">
              <div class="step-name">选择常数结构包</div>
              <div class="step-hint">
                支持 JSON 全量包（含 version + groups/models）或 ZIP（包内 constants.json / manifest.json）。
                一个包对应一个版本；重复导入同一版本即覆盖。
              </div>
              <div class="pick-row">
                <input type="file" accept=".json,.zip" class="file" @change="onFile" />
                <span v-if="importFile" class="picked">{{ importFile.name }}（{{ Math.round(importFile.size / 1024) }} KB）</span>
                <span v-else class="picked muted">未选择文件，也可在下方粘贴 JSON</span>
              </div>
              <textarea
                v-model="importText"
                class="ta"
                rows="7"
                placeholder='{"version":"V2.1.0","label":"2026Q4 整机标定","groups":[{"product":"1000-A","subsystem":"SPM","pn":"SPM-3200-A1","models":[{"model_key":"SPM_PWR_TRIM","name":"电源纹波补偿","fields":[{"key":"k_gain","label":"增益系数","type":"float","min":0.8,"max":1.2,"default":1.0}]}]}]}'
                :disabled="!!importFile"
              ></textarea>
            </div>
          </div>

          <div class="dlg-step">
            <span class="step-no">2</span>
            <div class="step-main">
              <div class="step-name">解析预览</div>
              <div class="pick-row">
                <button class="btn" type="button" :disabled="previewing || (!importFile && !importText.trim())" @click="doPreview">
                  {{ previewing ? '解析中…' : '解析并预览' }}
                </button>
                <span class="step-hint">只解析不落库，先确认版本与结构范围。</span>
              </div>
              <div v-if="importErr" class="notice err mt8">{{ importErr }}</div>
              <div v-if="preview" class="preview">
                <div class="pv-head">
                  <span class="pv-ver">{{ preview.version }}</span>
                  <span class="pv-label">{{ preview.label || '未命名版本' }}</span>
                  <span v-if="preview.source" class="pv-src">来源 {{ preview.source }}</span>
                </div>
                <div class="pv-warn" v-if="preview.conflict">
                  该版本已存在（现有 {{ preview.existing_models }} 个模型）。导入将覆盖结构，
                  仍存在字段的标定值默认保留。
                </div>
                <div class="pv-warn ok" v-else>该版本为新版本，导入后自动设为生效版本。</div>
                <div class="pv-stats">
                  <span>PN {{ preview.stats.pn_count }}</span>
                  <span>模型 {{ preview.stats.model_count }}</span>
                  <span>字段 {{ preview.stats.field_count }}</span>
                </div>
                <table class="pv-table">
                  <thead>
                    <tr><th>PN</th><th>归属</th><th>模型</th><th>字段</th><th>模型清单</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="p in preview.pns" :key="p.pn">
                      <td class="mono">{{ p.pn }}</td>
                      <td>{{ [p.product, p.subsystem].filter(Boolean).join(' / ') || '未归类' }}</td>
                      <td class="mono">{{ p.model_count }}</td>
                      <td class="mono">{{ p.field_count }}</td>
                      <td class="mono small">{{ p.models.map((m) => m.model_key).join(', ') }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="dlg-step">
            <span class="step-no">3</span>
            <div class="step-main">
              <div class="step-name">覆盖策略</div>
              <div class="mode-row">
                <label class="mode" :class="{ on: importMode === 'overwrite' }">
                  <input v-model="importMode" type="radio" value="overwrite" />
                  <span>
                    <b>覆盖导入</b>
                    <em>同版本已存在时替换结构，保留仍存在字段的标定值</em>
                  </span>
                </label>
                <label class="mode" :class="{ on: importMode === 'reject' }">
                  <input v-model="importMode" type="radio" value="reject" />
                  <span>
                    <b>拒绝重复</b>
                    <em>版本已存在则导入失败，需先调整版本号</em>
                  </span>
                </label>
              </div>
              <div v-if="importMsg" class="notice ok mt8">{{ importMsg }}</div>
            </div>
          </div>
        </div>

        <div class="dlg-foot">
          <span class="foot-hint">导入后可在左侧树按 PN 查看结构，并在模型内补录标定值。</span>
          <div class="foot-btns">
            <button class="btn" type="button" @click="closeImport">关闭</button>
            <button class="btn primary" type="button" :disabled="!preview || importing" @click="doImport">
              {{ importing ? '导入中…' : '确认导入' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cn-page { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; min-width: 0; }
.cn-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }

.cn-head { flex-direction: row; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.head-left { display: flex; flex-direction: column; gap: 5px; }
.cn-title { font-size: 18px; font-weight: 800; letter-spacing: 0.6px; }
.cn-sub { color: var(--muted); font-size: 12.5px; max-width: 760px; line-height: 1.6; }
.head-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ver-label { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--muted); }
.sel { background: #fff; border: 1px solid var(--border-strong); border-radius: 7px; padding: 6px 10px; font-size: 13px; color: var(--text); font-family: var(--mono); }
.badge-on { font-size: 11.5px; color: var(--ok); border: 1px solid rgba(21, 128, 61, 0.3); background: rgba(21, 128, 61, 0.07); border-radius: 999px; padding: 3px 10px; font-weight: 600; }

.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.metric { background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 3px; }
.metric-label { color: var(--muted); font-size: 12px; }
.metric-value { font-size: 22px; font-weight: 800; font-family: var(--mono); }
.metric-value.accent { color: var(--accent); }
.metric-foot { color: var(--muted); font-size: 11.5px; }

.cn-body { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 16px; align-items: start; }
.cn-side { max-height: calc(100vh - 320px); overflow-y: auto; }
.side-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.panel-title { font-size: 15px; font-weight: 700; letter-spacing: 0.4px; }
.panel-sub { color: var(--muted); font-size: 12px; }

.tree { display: flex; flex-direction: column; gap: 10px; }
.tree-prod { border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; background: #fff; }
.node-prod { display: flex; flex-direction: column; gap: 2px; padding: 2px 0 6px; border-bottom: 1px dashed var(--border); }
.node-name { font-size: 13px; font-weight: 700; }
.node-meta { color: var(--muted); font-size: 11px; font-family: var(--mono); }
.tree-sub { margin-top: 6px; }
.node-sub { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 2px 0; }
.node-sub .node-name { font-size: 12.5px; color: var(--text); font-weight: 600; }
.node-pn { display: flex; flex-direction: column; gap: 2px; width: 100%; text-align: left; border: 1px solid transparent; background: var(--panel-2); border-radius: 8px; padding: 6px 9px; margin-top: 5px; cursor: pointer; }
.node-pn:hover { border-color: var(--border-strong); }
.node-pn.active { border-color: var(--accent); background: rgba(var(--accent-rgb), 0.08); }
.pn-code { font-family: var(--mono); font-size: 12.5px; font-weight: 700; }
.pn-meta { color: var(--muted); font-size: 11px; }

.cn-main { max-height: calc(100vh - 240px); overflow-y: auto; }
.model-list { display: flex; flex-direction: column; gap: 10px; }
.model { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.model-head { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; background: var(--panel-2); border: none; padding: 10px 12px; cursor: pointer; }
.model-head:hover { background: var(--panel-3); }
.m-key { font-family: var(--mono); font-size: 13px; font-weight: 700; }
.m-name { font-size: 12.5px; color: var(--muted); }
.m-tag { font-size: 11px; color: var(--accent); border: 1px solid rgba(var(--accent-rgb), 0.3); border-radius: 4px; padding: 1px 6px; }
.m-stat { margin-left: auto; font-size: 11.5px; font-family: var(--mono); color: var(--muted); }
.m-time { font-size: 11px; color: var(--muted); font-family: var(--mono); }
.m-arrow { font-size: 15px; font-weight: 700; color: var(--accent); width: 16px; text-align: center; }

.model-body { padding: 12px; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; }
.m-desc { color: var(--muted); font-size: 12px; line-height: 1.6; }
.fields { width: 100%; border-collapse: collapse; }
.fields th { text-align: left; font-size: 11.5px; color: var(--muted); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--panel-2); }
.fields td { padding: 6px 8px; font-size: 12.5px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.fields tr:last-child td { border-bottom: none; }
.mono { font-family: var(--mono); }
.small { font-size: 11.5px; }
.val-input { width: 120px; background: #fff; border: 1px solid var(--border-strong); border-radius: 6px; padding: 4px 8px; font-size: 12.5px; font-family: var(--mono); color: var(--text); }
.val-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px rgba(var(--accent-rgb), 0.12); }
.model-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.model-note { color: var(--muted); font-size: 11.5px; font-family: var(--mono); }
.ok-line { color: var(--ok); font-size: 12.5px; }

.empty { color: var(--muted); font-size: 13px; padding: 18px 4px; line-height: 1.7; }
.loading { color: var(--muted); font-size: 13px; padding: 12px 2px; }
.notice { padding: 10px 12px; border-radius: 8px; font-size: 12.5px; line-height: 1.6; }
.notice.err { color: var(--err); background: rgba(220, 38, 38, 0.06); border: 1px solid rgba(220, 38, 38, 0.25); }
.notice.ok { color: var(--ok); background: rgba(21, 128, 61, 0.06); border: 1px solid rgba(21, 128, 61, 0.25); }
.mt8 { margin-top: 8px; }

/* ---------- 导入向导 ---------- */
.mask { position: fixed; inset: 0; background: rgba(17, 12, 30, 0.55); display: flex; align-items: center; justify-content: center; padding: 24px; z-index: 60; }
.dialog { width: min(900px, 96vw); max-height: 92vh; background: #fff; border-radius: 14px; border: 1px solid var(--border-strong); display: flex; flex-direction: column; overflow: hidden; }
.dlg-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--border); }
.dlg-title { font-size: 15.5px; font-weight: 800; letter-spacing: 0.4px; }
.dlg-close { border: none; background: transparent; font-size: 22px; line-height: 1; color: var(--muted); cursor: pointer; }
.dlg-body { padding: 14px 18px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }
.dlg-step { display: flex; gap: 12px; }
.step-no { flex: 0 0 22px; height: 22px; border-radius: 50%; background: var(--accent); color: #fff; font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; }
.step-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.step-name { font-size: 13.5px; font-weight: 700; }
.step-hint { color: var(--muted); font-size: 11.5px; line-height: 1.6; }
.pick-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.file { font-size: 12px; }
.picked { font-size: 12px; font-family: var(--mono); color: var(--text); }
.picked.muted { color: var(--muted); font-family: inherit; }
.ta { width: 100%; border: 1px solid var(--border-strong); border-radius: 8px; padding: 10px; font-family: var(--mono); font-size: 12px; line-height: 1.55; color: var(--text); resize: vertical; }
.ta:disabled { background: var(--panel-2); color: var(--muted); }

.preview { border: 1px solid var(--border); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; background: var(--panel-2); }
.pv-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.pv-ver { font-family: var(--mono); font-size: 14px; font-weight: 800; color: var(--accent); }
.pv-label { font-size: 12.5px; font-weight: 600; }
.pv-src { color: var(--muted); font-size: 11.5px; }
.pv-warn { font-size: 12px; color: var(--warn); background: rgba(180, 83, 9, 0.07); border: 1px solid rgba(180, 83, 9, 0.25); border-radius: 7px; padding: 7px 10px; line-height: 1.6; }
.pv-warn.ok { color: var(--ok); background: rgba(21, 128, 61, 0.06); border-color: rgba(21, 128, 61, 0.25); }
.pv-stats { display: flex; gap: 16px; font-family: var(--mono); font-size: 12px; color: var(--text); }
.pv-table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; }
.pv-table th { text-align: left; font-size: 11px; color: var(--muted); padding: 6px 8px; background: var(--panel-3); font-weight: 600; }
.pv-table td { padding: 6px 8px; font-size: 12px; border-top: 1px solid var(--border); }

.mode-row { display: flex; gap: 10px; flex-wrap: wrap; }
.mode { flex: 1 1 260px; display: flex; gap: 8px; align-items: flex-start; border: 1px solid var(--border); border-radius: 9px; padding: 10px 12px; cursor: pointer; background: #fff; }
.mode.on { border-color: var(--accent); background: rgba(var(--accent-rgb), 0.05); }
.mode input { margin-top: 3px; }
.mode b { display: block; font-size: 12.5px; }
.mode em { display: block; font-style: normal; color: var(--muted); font-size: 11.5px; line-height: 1.6; margin-top: 2px; }

.dlg-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 18px; border-top: 1px solid var(--border); flex-wrap: wrap; }
.foot-hint { color: var(--muted); font-size: 11.5px; }
.foot-btns { display: flex; gap: 10px; }
.btn.sm { padding: 5px 12px; font-size: 12.5px; }

@media (max-width: 1100px) {
  .cn-body { grid-template-columns: 1fr; }
  .cn-side, .cn-main { max-height: none; }
}
</style>
