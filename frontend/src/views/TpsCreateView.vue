<script setup>
/**
 * TPS 创建
 *
 * 三种创建方式：文本编辑（就地改 JSON 并保存）/ 压缩包导入（zip 含 tps.json + testcase/）/ 空白模板。
 * 保存走 /api/tps/import（v1 / v2 自动分流），压缩包走 /api/tps/import/zip。
 * 页面下半部分是「机器常数关联」：为当前 TPS 手动选择版本 / PN / 模型，
 * 关联写入 TPS 清单的 constant_model 字段；执行时按该绑定填充常数，结束后产出 XML。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const tab = ref('text')          // text | zip | template
const tpsList = ref([])
const listLoading = ref(false)
const listErr = ref('')
const keyword = ref('')
const currentTpsId = ref('')

// 文本编辑
const editorText = ref('')
const editorErr = ref('')
const editorOk = ref('')
const saving = ref(false)

// 压缩包
const zipFile = ref(null)
const zipMsg = ref('')
const zipErr = ref('')
const zipBusy = ref(false)
const zipOverwrite = ref(true)

// 空白模板
const form = ref({ id: '', name: '', version: '1.0.0', project: '', dut: '', schema: 'tps.v2' })

// 常数关联
const constVersions = ref([])
const constPns = ref([])         // [{pn, product, subsystem, label}]
const binding = ref({ version: '', pn: '', models: [] })
const pnModels = ref([])
const resolved = ref(null)
const bindingMsg = ref('')
const bindingErr = ref('')
const xmlPreview = ref('')
const bindingBusy = ref(false)

const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (!k) return tpsList.value
  return tpsList.value.filter((t) =>
    [t.id, t.name, t.version, t.project].filter(Boolean).some((v) => String(v).toLowerCase().includes(k))
  )
})

const prettyValid = computed(() => {
  if (!editorText.value.trim()) return null
  try {
    JSON.parse(editorText.value)
    return true
  } catch (e) {
    return false
  }
})

async function loadList() {
  listLoading.value = true
  listErr.value = ''
  try {
    const res = await api.getTpsList()
    tpsList.value = res.tps || []
  } catch (e) {
    listErr.value = '读取 TPS 列表失败：' + e.message
  } finally {
    listLoading.value = false
  }
}

async function loadConstantsIndex() {
  try {
    const [vers, tree] = await Promise.all([api.getConstantVersions(), api.getConstantsOverview('')])
    constVersions.value = vers.versions || []
    const flat = []
    for (const p of tree.tree || []) {
      for (const s of p.subsystems || []) {
        for (const n of s.pns || []) {
          flat.push({
            pn: n.pn,
            product: p.id,
            subsystem: s.id,
            label: [p.name, s.name].filter(Boolean).join(' / '),
            model_count: n.model_count
          })
        }
      }
    }
    constPns.value = flat
    if (!binding.value.version) binding.value.version = vers.active || ''
  } catch (e) {
    /* 常数模块不可用时静默：TPS 创建不依赖它 */
  }
}

/**
 * 后端返回的是「运行期视图」：v2 清单会被补出 steps / _raw / 各类计数等派生字段。
 * 编辑器只应展示可回写的原始清单，否则保存时会因「steps 与 cmd_suit 同时存在」被拒。
 */
function toEditable(tps) {
  if (!tps) return {}
  const base = tps._raw && Object.keys(tps._raw).length ? tps._raw : tps
  const clean = JSON.parse(JSON.stringify(base))
  for (const k of ['_raw', '_source', 'step_count', 'testconfig_count', 'device_alias_count', 'cmd_suit_count']) {
    delete clean[k]
  }
  if (clean.cmd_suit) delete clean.steps
  return clean
}

async function openTps(t) {
  currentTpsId.value = t.id
  try {
    const res = await api.getTpsDetail(t.id)
    if (!res.success || !res.tps) throw new Error(res.message || '读取失败')
    editorText.value = JSON.stringify(toEditable(res.tps), null, 2)
    editorErr.value = ''
    editorOk.value = `已载入 ${t.id}（${res.source === 'remote' ? '远端' : '本地'}）`
    tab.value = 'text'
    await loadBinding(t.id)
  } catch (e) {
    editorErr.value = '载入 TPS 失败：' + e.message
  }
}

function formatJson() {
  try {
    editorText.value = JSON.stringify(JSON.parse(editorText.value), null, 2)
    editorErr.value = ''
    editorOk.value = '已格式化'
  } catch (e) {
    editorErr.value = 'JSON 语法错误：' + e.message
  }
}

async function saveEditor() {
  saving.value = true
  editorErr.value = ''
  editorOk.value = ''
  try {
    const res = await api.importTps(editorText.value, currentTpsId.value || 'editor.json')
    if (!res.success) throw new Error(res.message || '保存失败')
    editorOk.value = res.message
    const id = (res.tps && res.tps.id) || JSON.parse(editorText.value).id
    currentTpsId.value = id
    await loadList()
    await loadBinding(id)
  } catch (e) {
    editorErr.value = '保存失败：' + e.message
  } finally {
    saving.value = false
  }
}

function onZip(ev) {
  const file = ev.target.files && ev.target.files[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    const bytes = new Uint8Array(reader.result)
    let bin = ''
    for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i])
    zipFile.value = { name: file.name, size: file.size, base64: btoa(bin) }
    zipMsg.value = ''
    zipErr.value = ''
  }
  reader.readAsArrayBuffer(file)
}

async function importZip() {
  if (!zipFile.value) return
  zipBusy.value = true
  zipErr.value = ''
  zipMsg.value = ''
  try {
    const res = await api.importTpsZip({
      name: zipFile.value.name,
      content_base64: zipFile.value.base64,
      overwrite: zipOverwrite.value
    })
    if (!res.success) throw new Error(res.message || '导入失败')
    zipMsg.value = res.message
    await loadList()
    if (res.tps && res.tps.id) {
      currentTpsId.value = res.tps.id
      await loadBinding(res.tps.id)
    }
  } catch (e) {
    zipErr.value = '导入失败：' + e.message
  } finally {
    zipBusy.value = false
  }
}

function buildV1() {
  const f = form.value
  return {
    id: f.id || 'new_tps_v1',
    name: f.name || '新建 TPS',
    version: f.version || '1.0.0',
    project: f.project || '',
    dut: f.dut || '',
    description: '',
    steps: [
      { type: 'init', name: '测试环境初始化', script: 'ops/init_env.py' },
      { type: 'test', name: '用例一', testcase_id: 'test_cases/test_demo.py::test_case_one' },
      { type: 'teardown', name: '测试环境终止', script: 'ops/finish_env.py' }
    ]
  }
}

function buildV2() {
  const f = form.value
  return {
    schema: 'tps.v2',
    id: f.id || 'new_tps_v2',
    name: f.name || '新建 TPS',
    version: f.version || '1.0.0',
    description: '',
    project: f.project || '',
    dut: f.dut || '',
    testconfig: {
      TC_VOUT: { label: '输出电压', min: 3.135, max: 3.465, unit: 'V' }
    },
    device_config: {
      psu: { device_id: '', role: 'psu', note: 'DUT 供电' }
    },
    setup: [{ id: 'SETUP01', name: '环境初始化', case: 'testcase.env_setup::init_environment' }],
    cmd_suit: [
      {
        id: 'C001',
        name: '输出电压测试',
        type: 'test',
        case: 'testcase.power::power_on_voltage',
        checks: { vout: 'TC_VOUT' }
      }
    ],
    teardown: [{ id: 'TD01', name: '环境终止', case: 'testcase.env_setup::finish_environment' }]
  }
}

function generateTemplate() {
  const data = form.value.schema === 'tps.v1' ? buildV1() : buildV2()
  editorText.value = JSON.stringify(data, null, 2)
  currentTpsId.value = ''
  tab.value = 'text'
  editorErr.value = ''
  editorOk.value = '模板已生成，确认内容后保存'
}

// ---------- 常数关联 ----------

async function loadBinding(tpsId) {
  bindingMsg.value = ''
  bindingErr.value = ''
  resolved.value = null
  xmlPreview.value = ''
  try {
    const res = await api.getTpsConstants(tpsId)
    if (!res.success) return
    binding.value = {
      version: (res.binding && res.binding.version) || constVersions.value.find((v) => v.is_active)?.version || '',
      pn: (res.binding && res.binding.pn) || '',
      models: (res.binding && res.binding.models) || []
    }
    resolved.value = res.resolved || null
    if (binding.value.pn) await loadPnModels()
  } catch (e) {
    /* 静默 */
  }
}

async function loadPnModels() {
  pnModels.value = []
  if (!binding.value.pn) return
  try {
    const res = await api.getConstantsByPn(binding.value.pn, binding.value.version)
    if (!res.success) return
    pnModels.value = res.models || []
    const all = pnModels.value.map((m) => m.model_key)
    if (!binding.value.models || !binding.value.models.length) binding.value.models = all
    else binding.value.models = binding.value.models.filter((k) => all.includes(k))
  } catch (e) {
    /* 静默 */
  }
}

function toggleModelKey(key) {
  const list = binding.value.models || []
  const i = list.indexOf(key)
  if (i >= 0) list.splice(i, 1)
  else list.push(key)
  binding.value.models = [...list]
}

async function saveBinding() {
  if (!currentTpsId.value) {
    bindingErr.value = '先保存 TPS，再关联常数模型'
    return
  }
  bindingBusy.value = true
  bindingErr.value = ''
  bindingMsg.value = ''
  try {
    const res = await api.saveTpsConstants(currentTpsId.value, binding.value)
    if (!res.success) throw new Error(res.message || '保存失败')
    bindingMsg.value = res.message
    const chk = await api.getTpsConstants(currentTpsId.value)
    resolved.value = chk.resolved || null
  } catch (e) {
    bindingErr.value = '保存关联失败：' + e.message
  } finally {
    bindingBusy.value = false
  }
}

async function clearBinding() {
  if (!currentTpsId.value) return
  bindingBusy.value = true
  bindingErr.value = ''
  try {
    const res = await api.saveTpsConstants(currentTpsId.value, {})
    bindingMsg.value = res.message || '已解除关联'
    binding.value = { version: binding.value.version, pn: '', models: [] }
    pnModels.value = []
    resolved.value = null
    xmlPreview.value = ''
  } catch (e) {
    bindingErr.value = '解除关联失败：' + e.message
  } finally {
    bindingBusy.value = false
  }
}

async function buildXmlPreview() {
  if (!binding.value.pn) return
  try {
    const res = await api.constantsXml({
      binding: binding.value,
      header: {
        tpsId: currentTpsId.value,
        uut: 'ABC123DEF456',
        result: 'OK',
        tpsName: (tpsList.value.find((t) => t.id === currentTpsId.value) || {}).name || ''
      }
    })
    if (!res.success) throw new Error(res.message || '生成失败')
    xmlPreview.value = res.xml
  } catch (e) {
    bindingErr.value = '生成 XML 失败：' + e.message
  }
}

onMounted(async () => {
  await Promise.all([loadList(), loadConstantsIndex()])
})
</script>

<template>
  <div class="tc-page">
    <section class="tc-panel tc-head">
      <div class="head-left">
        <div class="tc-title">TPS 创建</div>
        <div class="tc-sub">
          在本地新建 / 编辑测试程序集：可直接编辑 JSON 清单保存，也可导入压缩包（含 tps.json 与 testcase/）
        </div>
      </div>
      <div class="head-right">
        <button class="btn" type="button" @click="loadList">刷新列表</button>
        <button class="btn primary" type="button" @click="tab = 'template'">新建 TPS</button>
      </div>
    </section>

    <div class="tc-body">
      <!-- 左：已有 TPS -->
      <aside class="tc-panel tc-side">
        <div class="side-head">
          <span class="panel-title">已有 TPS</span>
          <span class="panel-sub">{{ tpsList.length }} 个</span>
        </div>
        <input v-model="keyword" class="search" placeholder="按名称 / 编号 / 版本搜索" />
        <div v-if="listErr" class="notice err">{{ listErr }}</div>
        <div v-else-if="listLoading" class="loading">正在读取…</div>
        <div v-else-if="!filtered.length" class="empty">没有匹配的 TPS</div>
        <div v-else class="tps-list">
          <button
            v-for="t in filtered"
            :key="t.id"
            type="button"
            class="tps-item"
            :class="{ active: currentTpsId === t.id }"
            @click="openTps(t)"
          >
            <span class="t-name">{{ t.name }}</span>
            <span class="t-id">{{ t.id }}</span>
            <span class="t-meta">
              v{{ t.version || '—' }} ·
              <template v-if="t.cmd_suit_count">用例 {{ t.cmd_suit_count }}</template>
              <template v-else>步骤 {{ t.step_count }}</template>
              <template v-if="t.bench && t.bench.preset_id"> · {{ t.bench.preset_id }}</template>
            </span>
          </button>
        </div>
      </aside>

      <!-- 右：创建 / 编辑 -->
      <section class="tc-panel tc-main">
        <div class="tabs">
          <button class="tab" :class="{ active: tab === 'text' }" type="button" @click="tab = 'text'">文本编辑</button>
          <button class="tab" :class="{ active: tab === 'zip' }" type="button" @click="tab = 'zip'">压缩包导入</button>
          <button class="tab" :class="{ active: tab === 'template' }" type="button" @click="tab = 'template'">空白模板</button>
          <span class="tab-note">
            当前 TPS：<b>{{ currentTpsId || '未保存' }}</b>
          </span>
        </div>

        <!-- 文本编辑 -->
        <template v-if="tab === 'text'">
          <div class="toolbar">
            <button class="btn sm" type="button" @click="formatJson">格式化</button>
            <button class="btn sm" type="button" :disabled="!editorText.trim()" @click="saveEditor">
              {{ saving ? '保存中…' : '校验并保存' }}
            </button>
            <span class="json-state" :class="{ ok: prettyValid === true, err: prettyValid === false }">
              <template v-if="prettyValid === true">JSON 结构合法</template>
              <template v-else-if="prettyValid === false">JSON 语法有误</template>
              <template v-else>等待输入</template>
            </span>
          </div>
          <textarea
            v-model="editorText"
            class="editor"
            spellcheck="false"
            placeholder='在这里编辑 TPS 清单（v1: steps；v2: testconfig + device_config + cmd_suit）'
          ></textarea>
          <div v-if="editorErr" class="notice err">{{ editorErr }}</div>
          <div v-if="editorOk" class="notice ok">{{ editorOk }}</div>
          <div class="hint-box">
            <div class="hint-line"><b>v1 清单</b>：init / test / teardown 三段式 steps，用例指向 pytest 节点。</div>
            <div class="hint-line"><b>v2 清单</b>：testconfig 定义阈值，device_config 声明设备别名，cmd_suit 为执行序列，用例实现放 testcase/ 包。</div>
            <div class="hint-line">保存后立即生效：写入 testresource 目录，测试执行页即可选择运行。</div>
          </div>
        </template>

        <!-- 压缩包导入 -->
        <template v-else-if="tab === 'zip'">
          <div class="zip-box">
            <div class="zip-title">压缩包结构要求</div>
            <pre class="tree-code">tps_pack.zip
├── tps.json          # TPS 清单（必须有 id）
├── testcase/         # 用例实现（v2）
│   ├── __init__.py
│   └── power.py
└── resource/         # 人工操作提示 / 说明文件（可选）</pre>
            <div class="hint-line">
              允许 zip 自带一层根目录；v1 单文件清单也可打包导入（仅取 tps.json）。
              覆盖同名 TPS 需勾选下方的覆盖选项。
            </div>
          </div>
          <div class="zip-row">
            <input type="file" accept=".zip" class="file" @change="onZip" />
            <span v-if="zipFile" class="picked">{{ zipFile.name }}（{{ Math.round(zipFile.size / 1024) }} KB）</span>
            <label class="chk">
              <input v-model="zipOverwrite" type="checkbox" />
              覆盖同名 TPS
            </label>
            <button class="btn primary" type="button" :disabled="!zipFile || zipBusy" @click="importZip">
              {{ zipBusy ? '导入中…' : '导入压缩包' }}
            </button>
          </div>
          <div v-if="zipErr" class="notice err">{{ zipErr }}</div>
          <div v-if="zipMsg" class="notice ok">{{ zipMsg }}</div>
        </template>

        <!-- 空白模板 -->
        <template v-else>
          <div class="grid">
            <label class="field">
              <span class="f-label">TPS 编号</span>
              <input v-model="form.id" class="input mono" placeholder="如 spm_rh_dyn_2" />
            </label>
            <label class="field">
              <span class="f-label">名称</span>
              <input v-model="form.name" class="input" placeholder="如 SPM 读头动态测试" />
            </label>
            <label class="field">
              <span class="f-label">版本</span>
              <input v-model="form.version" class="input mono" placeholder="1.0.0" />
            </label>
            <label class="field">
              <span class="f-label">项目</span>
              <input v-model="form.project" class="input" placeholder="如 SPM 项目" />
            </label>
            <label class="field">
              <span class="f-label">被测件</span>
              <input v-model="form.dut" class="input" placeholder="如 SPM 读头" />
            </label>
            <label class="field">
              <span class="f-label">清单格式</span>
              <select v-model="form.schema" class="input">
                <option value="tps.v2">tps.v2（推荐：阈值 + 设备别名 + 用例序列）</option>
                <option value="tps.v1">tps.v1（steps 三段式）</option>
              </select>
            </label>
          </div>
          <div class="tpl-actions">
            <button class="btn primary" type="button" @click="generateTemplate">生成模板并编辑</button>
            <span class="hint-line">模板只含最小可运行结构，生成后在「文本编辑」里补全阈值、设备与用例。</span>
          </div>
        </template>

        <!-- 机器常数关联 -->
        <div class="bind-block">
          <div class="bind-head">
            <span class="bind-title">机器常数关联</span>
            <span class="bind-sub">
              关联后，执行时按 PN 的常数模型填充值，结束后在 reports/constants 下生成 XML
            </span>
          </div>

          <div v-if="!currentTpsId" class="bind-empty">先在左侧选择或保存一个 TPS，再关联常数模型。</div>

          <template v-else>
            <div class="bind-row">
              <label class="field small">
                <span class="f-label">常数版本</span>
                <select v-model="binding.version" class="input" @change="loadPnModels">
                  <option v-for="v in constVersions" :key="v.version" :value="v.version">
                    {{ v.version }}{{ v.is_active ? '（生效）' : '' }}
                  </option>
                  <option v-if="!constVersions.length" value="">— 暂无版本 —</option>
                </select>
              </label>
              <label class="field small">
                <span class="f-label">PN 号</span>
                <select v-model="binding.pn" class="input mono" @change="loadPnModels">
                  <option value="">— 未关联 —</option>
                  <option v-for="p in constPns" :key="p.pn" :value="p.pn">
                    {{ p.pn }}（{{ p.label }}）
                  </option>
                </select>
              </label>
              <div class="bind-btns">
                <button class="btn sm" type="button" :disabled="bindingBusy || !binding.pn" @click="saveBinding">
                  {{ bindingBusy ? '处理中…' : '保存关联' }}
                </button>
                <button class="btn sm" type="button" :disabled="bindingBusy || !binding.pn" @click="clearBinding">解除关联</button>
              </div>
            </div>

            <div v-if="pnModels.length" class="model-pick">
              <span class="mp-label">关联模型</span>
              <label v-for="m in pnModels" :key="m.model_key" class="mp-item">
                <input
                  type="checkbox"
                  :checked="(binding.models || []).includes(m.model_key)"
                  @change="toggleModelKey(m.model_key)"
                />
                <span class="mp-key">{{ m.model_key }}</span>
                <span class="mp-meta">{{ m.name }} · {{ m.field_count }} 字段 / 已标定 {{ m.filled }}</span>
              </label>
            </div>

            <div v-if="resolved" class="resolved">
              <span class="rs-item">模型 {{ resolved.totals.model_count }}</span>
              <span class="rs-item">字段 {{ resolved.totals.field_count }}</span>
              <span class="rs-item">已填 {{ resolved.totals.filled }}</span>
              <span class="rs-item warn" v-if="(resolved.missing || []).length">
                缺值 {{ resolved.missing.length }} 项（按默认值兜底）
              </span>
              <button class="btn sm ghost" type="button" @click="buildXmlPreview">预览 XML</button>
            </div>

            <div v-if="bindingErr" class="notice err">{{ bindingErr }}</div>
            <div v-if="bindingMsg" class="notice ok">{{ bindingMsg }}</div>
            <pre v-if="xmlPreview" class="xml">{{ xmlPreview }}</pre>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tc-page { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; min-width: 0; }
.tc-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.tc-head { flex-direction: row; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.head-left { display: flex; flex-direction: column; gap: 5px; }
.tc-title { font-size: 18px; font-weight: 800; letter-spacing: 0.6px; }
.tc-sub { color: var(--muted); font-size: 12.5px; max-width: 780px; line-height: 1.6; }
.head-right { display: flex; gap: 10px; }

.tc-body { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 16px; align-items: start; }
.tc-side { max-height: calc(100vh - 260px); overflow-y: auto; }
.side-head { display: flex; align-items: baseline; gap: 10px; }
.panel-title { font-size: 15px; font-weight: 700; }
.panel-sub { color: var(--muted); font-size: 12px; }
.search { width: 100%; background: #fff; border: 1px solid var(--border-strong); border-radius: 8px; padding: 7px 10px; font-size: 12.5px; color: var(--text); }
.search:focus { outline: none; border-color: var(--accent); }
.tps-list { display: flex; flex-direction: column; gap: 8px; }
.tps-item { display: flex; flex-direction: column; gap: 2px; text-align: left; border: 1px solid var(--border); background: #fff; border-radius: 9px; padding: 9px 11px; cursor: pointer; }
.tps-item:hover { border-color: var(--border-strong); background: var(--panel-2); }
.tps-item.active { border-color: var(--accent); background: rgba(var(--accent-rgb), 0.07); }
.t-name { font-size: 13px; font-weight: 700; }
.t-id { font-family: var(--mono); font-size: 11.5px; color: var(--accent); }
.t-meta { color: var(--muted); font-size: 11px; font-family: var(--mono); }

.tc-main { max-height: calc(100vh - 260px); overflow-y: auto; gap: 14px; }
.tabs { display: flex; align-items: center; gap: 8px; border-bottom: 1px solid var(--border); padding-bottom: 10px; flex-wrap: wrap; }
.tab { border: 1px solid var(--border-strong); background: #fff; color: var(--muted); padding: 6px 14px; border-radius: 7px; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.tab.active { background: linear-gradient(135deg, #7c3aed, #6d28d9); border-color: transparent; color: #fff; }
.tab-note { margin-left: auto; color: var(--muted); font-size: 11.5px; }
.tab-note b { font-family: var(--mono); color: var(--text); }

.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.json-state { font-size: 11.5px; color: var(--muted); margin-left: auto; }
.json-state.ok { color: var(--ok); }
.json-state.err { color: var(--err); }
.editor { width: 100%; min-height: 340px; border: 1px solid var(--border-strong); border-radius: 10px; padding: 12px; font-family: var(--mono); font-size: 12px; line-height: 1.6; color: var(--text); background: #fcfbff; resize: vertical; }
.editor:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px rgba(var(--accent-rgb), 0.12); }

.hint-box { border: 1px solid var(--border); border-radius: 9px; background: var(--panel-2); padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; }
.hint-line { color: var(--muted); font-size: 11.5px; line-height: 1.65; }
.hint-line b { color: var(--text); }

.zip-box { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--panel-2); display: flex; flex-direction: column; gap: 8px; }
.zip-title { font-size: 13px; font-weight: 700; }
.tree-code { margin: 0; font-family: var(--mono); font-size: 11.5px; line-height: 1.7; color: var(--text); background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; overflow-x: auto; }
.zip-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.file { font-size: 12px; }
.picked { font-family: var(--mono); font-size: 12px; }
.chk { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--text); }

.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.f-label { font-size: 12px; color: var(--muted); }
.input { background: #fff; border: 1px solid var(--border-strong); border-radius: 8px; padding: 7px 10px; font-size: 13px; color: var(--text); }
.input:focus { outline: none; border-color: var(--accent); }
.mono { font-family: var(--mono); }
.tpl-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

.bind-block { border-top: 1px dashed var(--border); padding-top: 14px; display: flex; flex-direction: column; gap: 10px; }
.bind-head { display: flex; flex-direction: column; gap: 3px; }
.bind-title { font-size: 14px; font-weight: 700; }
.bind-sub { color: var(--muted); font-size: 11.5px; line-height: 1.6; }
.bind-empty { color: var(--muted); font-size: 12.5px; }
.bind-row { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
.field.small { min-width: 220px; flex: 1 1 220px; }
.bind-btns { display: flex; gap: 8px; padding-bottom: 1px; }

.model-pick { border: 1px solid var(--border); border-radius: 9px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; background: var(--panel-2); }
.mp-label { font-size: 11.5px; color: var(--muted); }
.mp-item { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.mp-key { font-family: var(--mono); font-weight: 700; }
.mp-meta { color: var(--muted); font-size: 11.5px; }

.resolved { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.rs-item { font-family: var(--mono); font-size: 12px; color: var(--text); }
.rs-item.warn { color: var(--warn); }
.xml { margin: 0; max-height: 260px; overflow: auto; background: #fbfaff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-family: var(--mono); font-size: 11px; line-height: 1.6; }

.notice { padding: 9px 12px; border-radius: 8px; font-size: 12.5px; line-height: 1.6; }
.notice.err { color: var(--err); background: rgba(220, 38, 38, 0.06); border: 1px solid rgba(220, 38, 38, 0.25); }
.notice.ok { color: var(--ok); background: rgba(21, 128, 61, 0.06); border: 1px solid rgba(21, 128, 61, 0.25); }
.empty { color: var(--muted); font-size: 12.5px; padding: 12px 2px; }
.loading { color: var(--muted); font-size: 12.5px; padding: 10px 2px; }
.btn.sm { padding: 5px 12px; font-size: 12.5px; }
.btn.ghost { background: transparent; border: 1px solid var(--border-strong); }

@media (max-width: 1100px) {
  .tc-body { grid-template-columns: 1fr; }
  .tc-side, .tc-main { max-height: none; }
}
</style>
