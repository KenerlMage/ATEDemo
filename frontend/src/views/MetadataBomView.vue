<script setup>
/**
 * 元数据管理 · 测试台BOM管理
 *  - 参照「装备属性配置」中测试台 BOM 的口径（设备元信息 + 默认连接参数），把
 *    预设测试台类型的一张标准 BOM 以「可编辑」方式维护
 *  - 一个测试台类型 = 一份属性（名称/类别/典型DUT/推荐节拍/描述）+ 一张 BOM 设备清单
 *  - 保存后写入后端 tree 文件夹 (tree/tree.json + tree/bom/<类型编号>.json)
 *  - 「新建测试台」注册向导第一步即读取这里维护的 BOM（选中类型自动获取清单）
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const router = useRouter()

const NET = ['LAN', 'ETHERNET', 'TCP', 'IP']
const SER = ['SERIAL', 'RS232', 'RS485', 'UART', 'COM']
const IFACES = ['LAN', 'ETHERNET', 'TCP', 'IP', 'SERIAL', 'RS232', 'RS485', 'UART', 'COM', 'USB', 'GPIB', 'NONE']

const loading = ref(true)
const error = ref('')
const busy = ref(false)
const tip = ref('')
const tipErr = ref(false)

const tree = ref([])
const overview = ref(null)
const productId = ref('')
const subsystemId = ref('')
const presetId = ref('')

const attributes = reactive({
  name: '',
  category: '',
  description: '',
  typical_dut: '',
  recommended_cycle: ''
})
const items = ref([])
const bomFile = ref('')
const usedBy = ref(0)
const updatedAt = ref('')
const dirty = ref(false)
const errs = ref([])

const catalog = ref([])
const catalogOpen = ref(false)
const catalogKeyword = ref('')

const product = computed(() => tree.value.find((n) => n.product === productId.value) || null)
const subsystems = computed(() => (product.value ? product.value.subsystems : []))
const curSubsystem = computed(() => subsystems.value.find((s) => s.subsystem === subsystemId.value) || null)
const presetOptions = computed(() => (curSubsystem.value ? curSubsystem.value.preset_types : []))

const programmableCount = computed(() => items.value.filter((d) => d.programmable).length)
const netCount = computed(() => items.value.filter((d) => isNet(d)).length)
const serialCount = computed(() => items.value.filter((d) => isSer(d)).length)
const passiveCount = computed(() => items.value.length - netCount.value - serialCount.value)

const catalogFiltered = computed(() => {
  const k = catalogKeyword.value.trim().toLowerCase()
  if (!k) return catalog.value
  return catalog.value.filter((d) =>
    [d.name, d.model, d.vendor, d.category].some((v) => String(v || '').toLowerCase().includes(k))
  )
})

function isNet(d) { return NET.includes(String(d.interface || 'NONE').toUpperCase()) }
function isSer(d) { return SER.includes(String(d.interface || 'NONE').toUpperCase()) }
function ifaceLabel(d) { return String(d.interface || 'NONE').toUpperCase() }

function flash(text, isErr = false) {
  tip.value = text
  tipErr.value = isErr
}

function markDirty() { dirty.value = true }

// ---------- 数据加载 ----------

async function loadTree() {
  loading.value = true
  error.value = ''
  try {
    const [tr, ov] = await Promise.all([api.getMetadataTree(), api.getMetadataOverview()])
    if (!tr.success) throw new Error(tr.message || '读取装备树失败')
    tree.value = tr.tree || []
    overview.value = ov && ov.success !== false ? ov : null
    const want = String(route.query.preset || '')
    if (want) locatePreset(want)
    else if (!presetId.value) {
      const first = tree.value.find((p) => (p.subsystems || []).some((s) => (s.preset_types || []).length))
      const sub = first && (first.subsystems || []).find((s) => (s.preset_types || []).length)
      const t = sub && (sub.preset_types || [])[0]
      if (t) {
        productId.value = first.product
        subsystemId.value = sub.subsystem
        presetId.value = t.id
      }
    }
    if (presetId.value) await loadBom()
  } catch (e) {
    error.value = `无法连接后端：${e.message}`
  } finally {
    loading.value = false
  }
}

function locatePreset(id) {
  for (const p of tree.value) {
    for (const s of p.subsystems || []) {
      if ((s.preset_types || []).some((x) => x.id === id)) {
        productId.value = p.product
        subsystemId.value = s.subsystem
        presetId.value = id
        return true
      }
    }
  }
  return false
}

async function loadBom() {
  const id = presetId.value
  if (!id) return
  busy.value = true
  tip.value = ''
  try {
    const res = await api.getMetadataBom(id)
    if (!res.success) throw new Error(res.message || '读取 BOM 失败')
    items.value = (res.items || []).map((d) => ({ ...d }))
    bomFile.value = res.bom_file || ''
    updatedAt.value = res.updated_at || ''
    const a = res.attributes || {}
    attributes.name = a.name || ''
    attributes.category = a.category || ''
    attributes.description = a.description || ''
    attributes.typical_dut = a.typical_dut || ''
    attributes.recommended_cycle = a.recommended_cycle || ''
    const detail = await api.getMetadataPreset(id)
    usedBy.value = detail.success ? detail.used_by || 0 : 0
    dirty.value = false
    errs.value = []
  } catch (e) {
    flash(`读取 BOM 失败：${e.message}`, true)
  } finally {
    busy.value = false
  }
}

function onProductChange() {
  subsystemId.value = ''
  presetId.value = ''
  items.value = []
  dirty.value = false
}
function onSubsystemChange() {
  presetId.value = ''
  items.value = []
  dirty.value = false
}
async function onPresetChange() {
  if (!presetId.value) {
    items.value = []
    return
  }
  if (dirty.value && !window.confirm('当前 BOM 有未保存改动，切换测试台类型将丢弃这些改动，确认切换？')) {
    return
  }
  await loadBom()
}

// ---------- BOM 行操作 ----------

function newId() {
  return 'dev-' + Math.random().toString(16).slice(2, 8)
}

function blankItem() {
  return {
    id: newId(),
    name: '',
    model: '',
    vendor: '',
    category: '',
    role: '',
    programmable: false,
    interface: 'LAN',
    protocol: '',
    default_host: '',
    default_port: null,
    default_serial_port: '',
    default_baudrate: null,
    default_address: '',
    default_channel: '',
    required: true,
    note: ''
  }
}

function addRow() {
  items.value.push(blankItem())
  markDirty()
}

function duplicateRow(i) {
  const copy = { ...items.value[i], id: newId(), name: (items.value[i].name || '设备') + '（副本）' }
  items.value.splice(i + 1, 0, copy)
  markDirty()
}

function removeRow(i) {
  const d = items.value[i]
  if (!window.confirm(`确认从 BOM 中移除「${d.name || d.model || d.id}」？`)) return
  items.value.splice(i, 1)
  markDirty()
}

function moveRow(i, dir) {
  const j = i + dir
  if (j < 0 || j >= items.value.length) return
  const arr = items.value
  const tmp = arr[i]
  arr[i] = arr[j]
  arr[j] = tmp
  markDirty()
}

async function openCatalog() {
  catalogOpen.value = true
  if (catalog.value.length) return
  try {
    const res = await api.getMetadataDeviceCatalog()
    if (!res.success) throw new Error(res.message || '读取设备模板库失败')
    catalog.value = res.devices || []
  } catch (e) {
    flash(`读取设备模板库失败：${e.message}`, true)
  }
}

function addFromCatalog(dev) {
  const item = { ...blankItem(), ...dev, id: newId() }
  delete item.key
  delete item.used_in
  items.value.push(item)
  markDirty()
  flash(`已从模板库添加「${dev.model || dev.name}」到当前 BOM（记得保存）`)
}

async function removeFromServer(i) {
  const d = items.value[i]
  if (!presetId.value || !d.id) return
  if (!window.confirm(`确认从后端 BOM 文件删除设备「${d.name || d.model}」？`)) return
  busy.value = true
  try {
    const res = await api.deleteMetadataBomItem(presetId.value, d.id)
    if (!res.success) throw new Error(res.message || '删除失败')
    flash(res.message || '已删除')
    await loadBom()
  } catch (e) {
    flash(`删除失败：${e.message}`, true)
  } finally {
    busy.value = false
  }
}

// ---------- 校验 + 保存 ----------

const HOST_RE = /^(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9][A-Za-z0-9._-]{0,62})$/
const SERIAL_RE = /^(?:COM[0-9]{1,3}|\/dev\/tty[A-Za-z0-9._-]{1,40})$/

function validate() {
  const out = []
  const seen = {}
  items.value.forEach((d, i) => {
    const label = d.name || d.model || `第 ${i + 1} 行`
    if (!String(d.name || '').trim()) out.push(`${label}：设备名称不能为空`)
    if (!String(d.model || '').trim()) out.push(`${label}：型号不能为空`)
    seen[d.id] = (seen[d.id] || 0) + 1
    if (isNet(d)) {
      const host = String(d.default_host || '').trim()
      if (host && !HOST_RE.test(host)) out.push(`${label}：默认 IP/主机名格式非法（${host}）`)
      if (d.default_port !== null && d.default_port !== '' && (Number(d.default_port) < 1 || Number(d.default_port) > 65535))
        out.push(`${label}：默认端口需在 1-65535 之间`)
      if (d.programmable && !host) out.push(`${label}：可编程网络设备需填写默认 IP`)
      if (d.programmable && !d.default_port) out.push(`${label}：可编程网络设备需填写默认端口`)
    } else if (isSer(d)) {
      const sp = String(d.default_serial_port || '').trim()
      if (sp && !SERIAL_RE.test(sp)) out.push(`${label}：串口号格式非法（${sp}）`)
      if (d.default_baudrate && (Number(d.default_baudrate) < 300 || Number(d.default_baudrate) > 921600))
        out.push(`${label}：波特率需在 300-921600 之间`)
      if (d.programmable && !sp) out.push(`${label}：可编程串口设备需填写默认串口号`)
    }
  })
  Object.entries(seen).forEach(([id, n]) => {
    if (n > 1) out.push(`设备编号重复：${id}（${n} 次）`)
  })
  if (!presetId.value) out.push('请先选择测试台类型')
  errs.value = out
  return out.length === 0
}

function payloadItems() {
  return items.value.map((d) => ({
    id: d.id,
    name: d.name,
    model: d.model,
    vendor: d.vendor,
    category: d.category,
    role: d.role,
    programmable: !!d.programmable,
    interface: String(d.interface || 'NONE').toUpperCase(),
    protocol: d.protocol,
    default_host: d.default_host,
    default_port: d.default_port === '' || d.default_port === null || d.default_port === undefined ? null : Number(d.default_port),
    default_serial_port: d.default_serial_port,
    default_baudrate: d.default_baudrate === '' || d.default_baudrate === null || d.default_baudrate === undefined ? null : Number(d.default_baudrate),
    default_address: d.default_address,
    default_channel: d.default_channel,
    required: d.required !== false,
    note: d.note
  }))
}

async function save() {
  if (!presetId.value) {
    flash('请先选择测试台类型', true)
    return
  }
  if (!validate()) {
    flash('校验未通过，请修正标记的问题后再保存', true)
    return
  }
  busy.value = true
  try {
    const res = await api.saveMetadataBom(presetId.value, {
      attributes: {
        id: presetId.value,
        name: attributes.name.trim(),
        category: attributes.category,
        description: attributes.description,
        typical_dut: attributes.typical_dut,
        recommended_cycle: attributes.recommended_cycle,
        product: productId.value,
        subsystem: subsystemId.value
      },
      items: payloadItems()
    })
    if (!res.success) throw new Error(res.message || '保存失败')
    flash(res.message || 'BOM 已保存')
    dirty.value = false
    errs.value = []
    await loadBom()
  } catch (e) {
    flash(`保存失败：${e.message}`, true)
  } finally {
    busy.value = false
  }
}

async function resetBom() {
  if (dirty.value && !window.confirm('确认放弃未保存的改动，重新从后端加载？')) return
  await loadBom()
}

function goRegister() {
  if (dirty.value) flash('BOM 有未保存改动，保存后注册向导才会读到最新清单', true)
  router.push({ path: '/testbenches/register' })
}

onMounted(loadTree)
</script>

<template>
  <div class="mb-page">
    <!-- ============ 页头 + 类型选择 ============ -->
    <section class="mb-panel">
      <div class="panel-head">
        <div>
          <div class="panel-title">元数据管理 · 测试台BOM管理</div>
          <div class="panel-sub">
            为某个测试台类型编辑生成一张标准 BOM（设备元信息 + 默认连接参数），保存到后端 tree 文件夹；「新建测试台」注册向导第一步即读取这份 BOM
          </div>
        </div>
        <div class="head-actions">
          <router-link to="/metadata/tree" class="btn">装备树管理</router-link>
          <button class="btn" :disabled="busy" @click="loadTree">刷新</button>
        </div>
      </div>

      <div class="cascade">
        <label class="field">
          <span class="field-label">产品名称（第一级）</span>
          <select v-model="productId" class="input sel" @change="onProductChange">
            <option value="">请选择产品</option>
            <option v-for="p in tree" :key="p.product" :value="p.product">{{ p.product_name }}（{{ p.product }}）</option>
          </select>
        </label>
        <span class="cascade-arrow" :class="{ on: !!product }">›</span>
        <label class="field">
          <span class="field-label">子系统（第二级）</span>
          <select v-model="subsystemId" class="input sel" :disabled="!product" @change="onSubsystemChange">
            <option value="">{{ product ? '请选择子系统' : '请先选择产品' }}</option>
            <option v-for="s in subsystems" :key="s.subsystem" :value="s.subsystem">{{ s.subsystem_name }}（{{ s.subsystem }}）</option>
          </select>
        </label>
        <span class="cascade-arrow" :class="{ on: !!curSubsystem }">›</span>
        <label class="field">
          <span class="field-label">测试台类型（第三级）</span>
          <select v-model="presetId" class="input sel" :disabled="!curSubsystem" @change="onPresetChange">
            <option value="">{{ curSubsystem ? '请选择测试台类型' : '请先选择子系统' }}</option>
            <option v-for="t in presetOptions" :key="t.id" :value="t.id">{{ t.name }}（{{ t.id }}）</option>
          </select>
        </label>
      </div>

      <div v-if="presetId" class="store-bar">
        <span class="store-item"><span class="store-k">BOM 文件</span><span class="store-v mono">{{ bomFile || '—' }}</span></span>
        <span class="store-item"><span class="store-k">设备 / 可编程</span><span class="store-v mono">{{ items.length }} / {{ programmableCount }}</span></span>
        <span class="store-item"><span class="store-k">接口分布</span><span class="store-v">网络 {{ netCount }} · 串口 {{ serialCount }} · 其他 {{ passiveCount }}</span></span>
        <span class="store-item"><span class="store-k">最近保存</span><span class="store-v">{{ updatedAt || '—' }}</span></span>
        <span class="store-item"><span class="store-k">被引用</span><span class="store-v">{{ usedBy }} 台已注册测试台</span></span>
        <span v-if="dirty" class="badge warn">有未保存改动</span>
      </div>
    </section>

    <div v-if="loading" class="tip tip-loading">正在加载元数据…</div>
    <div v-else-if="error" class="tip tip-err">{{ error }}</div>
    <div v-else-if="!presetId" class="tip tip-loading">请在上方按「产品 → 子系统 → 测试台类型」选择要维护的类型；若类型还不存在，可到「装备树管理」新增节点。</div>

    <template v-else>
      <!-- ============ 测试台属性（纳入管理） ============ -->
      <section class="mb-panel">
        <div class="sub-title">
          测试台属性
          <span class="sub-note">与 BOM 一起保存到 tree 文件夹；注册向导的选型摘要（典型 DUT / 推荐节拍 / 描述）取自这里</span>
        </div>
        <div class="attr-grid">
          <label class="field">
            <span class="field-label">测试台类型名称 <em>*</em></span>
            <input v-model="attributes.name" class="input" @input="markDirty" />
          </label>
          <label class="field">
            <span class="field-label">类别</span>
            <input v-model="attributes.category" class="input" placeholder="如 电源类 / 射频类" @input="markDirty" />
          </label>
          <label class="field">
            <span class="field-label">典型 DUT</span>
            <input v-model="attributes.typical_dut" class="input" placeholder="如 DC-DC 模块、LDO 板" @input="markDirty" />
          </label>
          <label class="field">
            <span class="field-label">推荐节拍</span>
            <input v-model="attributes.recommended_cycle" class="input" placeholder="如 38 s / 件" @input="markDirty" />
          </label>
          <label class="field attr-desc">
            <span class="field-label">描述</span>
            <textarea v-model="attributes.description" class="input area" rows="2" @input="markDirty"></textarea>
          </label>
        </div>
      </section>

      <!-- ============ BOM 清单 ============ -->
      <section class="mb-panel">
        <div class="panel-head">
          <div class="panel-title">BOM 设备清单</div>
          <span class="panel-sub">{{ items.length }} 台设备（可编程 {{ programmableCount }} 台）· 默认连接参数供注册时预填，可在注册/装备属性配置页按现场改址</span>
        </div>

        <div class="bom-actions">
          <button class="btn sm primary" @click="addRow">＋ 新增设备行</button>
          <button class="btn sm" @click="openCatalog">▤ 从设备模板库添加</button>
          <button class="btn sm" :disabled="busy" @click="resetBom">↺ 重新加载</button>
          <span class="spacer"></span>
          <button class="btn sm" :disabled="busy" @click="validate">✓ 校验</button>
          <button class="btn sm primary" :disabled="busy" @click="save">保存 BOM 与属性</button>
        </div>

        <div v-if="!items.length" class="empty">
          <div class="empty-title">该类型还没有 BOM 设备</div>
          <div class="empty-text">用「＋ 新增设备行」逐台录入，或从「设备模板库」引用已有型号；保存后注册向导选中该类型时会自动带出这份清单。</div>
        </div>

        <div v-else class="table-wrap">
          <table class="bom-table">
            <thead>
              <tr>
                <th class="th-idx">#</th>
                <th>设备 / 编号</th>
                <th>型号 / 厂商</th>
                <th>类别 / 用途</th>
                <th>接口</th>
                <th>程控</th>
                <th>默认连接（IP:端口 / 串口@波特率 / 资源地址）</th>
                <th>必需</th>
                <th>备注</th>
                <th class="th-ops">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(d, i) in items" :key="d.id">
                <td class="td-idx">{{ i + 1 }}</td>
                <td>
                  <input v-model="d.name" class="input cell" placeholder="设备名称" @input="markDirty" />
                  <input v-model="d.id" class="input cell mono" placeholder="设备编号" @input="markDirty" />
                </td>
                <td>
                  <input v-model="d.model" class="input cell" placeholder="型号" @input="markDirty" />
                  <input v-model="d.vendor" class="input cell" placeholder="厂商" @input="markDirty" />
                </td>
                <td>
                  <input v-model="d.category" class="input cell" placeholder="类别" @input="markDirty" />
                  <input v-model="d.role" class="input cell" placeholder="用途" @input="markDirty" />
                </td>
                <td>
                  <select v-model="d.interface" class="input cell sel" @change="markDirty">
                    <option v-for="x in IFACES" :key="x" :value="x">{{ x }}</option>
                  </select>
                  <input v-model="d.protocol" class="input cell" placeholder="协议" @input="markDirty" />
                </td>
                <td class="td-center">
                  <label class="switch">
                    <input type="checkbox" v-model="d.programmable" @change="markDirty" />
                    <span>{{ d.programmable ? '可编程' : '手动' }}</span>
                  </label>
                </td>
                <td class="td-conn">
                  <template v-if="isNet(d)">
                    <input v-model="d.default_host" class="input cell mono" placeholder="IP / 主机名" @input="markDirty" />
                    <input v-model="d.default_port" class="input cell mono" placeholder="端口" @input="markDirty" />
                    <input v-model="d.default_channel" class="input cell mono" placeholder="通道（可选，如 CH1）" @input="markDirty" />
                  </template>
                  <template v-else-if="isSer(d)">
                    <input v-model="d.default_serial_port" class="input cell mono" placeholder="串口，如 COM6" @input="markDirty" />
                    <input v-model="d.default_baudrate" class="input cell mono" placeholder="波特率，如 115200" @input="markDirty" />
                  </template>
                  <template v-else>
                    <input v-model="d.default_address" class="input cell mono" placeholder="资源地址，如 USB0::0x2A8D::INSTR" @input="markDirty" />
                  </template>
                  <div class="conn-hint">
                    当前接口口径：{{ ifaceLabel(d) }}
                    <span v-if="isNet(d)">→ IP + 端口</span>
                    <span v-else-if="isSer(d)">→ 串口号 + 波特率</span>
                    <span v-else>→ 资源地址（连通性需人工确认）</span>
                  </div>
                </td>
                <td class="td-center">
                  <input type="checkbox" v-model="d.required" @change="markDirty" />
                </td>
                <td>
                  <input v-model="d.note" class="input cell" placeholder="备注" @input="markDirty" />
                </td>
                <td class="td-ops">
                  <button class="mini" title="上移" @click="moveRow(i, -1)">↑</button>
                  <button class="mini" title="下移" @click="moveRow(i, 1)">↓</button>
                  <button class="mini" title="复制一行" @click="duplicateRow(i)">复制</button>
                  <button class="mini danger" title="从本页移除（保存后生效）" @click="removeRow(i)">移除</button>
                  <button class="mini danger" title="立即从后端 BOM 文件删除" :disabled="busy" @click="removeFromServer(i)">删库</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="errs.length" class="tip tip-err">
          <div v-for="(e, i) in errs" :key="i">· {{ e }}</div>
        </div>

        <div class="bom-foot">
          <div class="foot-hint">
            提示：BOM 只描述「该类型标准装备组成」，已注册测试台的设备配置是各自独立的一份副本；修改 BOM 不会自动改写已注册测试台，但会影响之后新建的测试台。
          </div>
          <button class="btn primary" :disabled="busy" @click="save">保存 BOM 与属性</button>
        </div>
      </section>

      <!-- ============ 供新建测试台使用 ============ -->
      <section class="mb-panel">
        <div class="sub-title">供「新建测试台」使用</div>
        <div class="use-grid">
          <div class="use-card">
            <div class="use-k">当前类型</div>
            <div class="use-v">{{ attributes.name || '—' }} <span class="mono">{{ presetId }}</span></div>
            <div class="use-note">{{ productId }} / {{ subsystemId }} · BOM {{ items.length }} 台</div>
          </div>
          <div class="use-card">
            <div class="use-k">注册向导读取</div>
            <div class="use-v">第一步选中该类型 → 自动获取本 BOM</div>
            <div class="use-note">第二步按默认连接参数预填，第三步注册并自检</div>
          </div>
          <div class="use-card">
            <div class="use-k">已引用</div>
            <div class="use-v">{{ usedBy }} 台已注册/草稿测试台</div>
            <div class="use-note">被引用时该类型不可删除（防误删）</div>
          </div>
          <div class="use-card">
            <div class="use-k">状态</div>
            <div class="use-v" :class="dirty ? 'warn-text' : 'ok-text'">{{ dirty ? '有未保存改动' : '已与后端一致' }}</div>
            <div class="use-note">保存后写入 {{ bomFile || 'tree/bom/<类型编号>.json' }}</div>
          </div>
        </div>
        <div class="actions">
          <button class="btn primary" :disabled="busy" @click="goRegister">去新建测试台（注册向导）</button>
          <router-link to="/testbenches" class="btn">查看测试台导航</router-link>
          <router-link to="/equipment" class="btn ghost">查看装备属性配置</router-link>
        </div>
      </section>

      <div v-if="tip" class="tip" :class="tipErr ? 'tip-err' : 'tip-ok'">{{ tip }}</div>

      <!-- ============ 设备模板库 ============ -->
      <div v-if="catalogOpen" class="modal-mask" @click.self="catalogOpen = false">
        <div class="modal">
          <div class="modal-head">
            <div class="panel-title">设备模板库</div>
            <span class="panel-sub">从已纳管 BOM 去重汇总，共 {{ catalog.length }} 个型号</span>
            <button class="btn ghost" @click="catalogOpen = false">关闭</button>
          </div>
          <input v-model="catalogKeyword" class="input" placeholder="按名称 / 型号 / 厂商 / 类别筛选" />
          <div class="cat-list">
            <div v-for="d in catalogFiltered" :key="d.key" class="cat-row">
              <div class="cat-main">
                <div class="cat-name">{{ d.name || '—' }} <span class="cat-model">{{ d.model }}</span></div>
                <div class="cat-meta">
                  <span class="mono">{{ d.vendor || '—' }}</span> ·
                  <span>{{ d.category || '—' }}</span> ·
                  <span class="iface">{{ d.interface }}</span> ·
                  <span>{{ d.programmable ? '可编程' : '手动' }}</span> ·
                  默认：<span class="mono">{{ d.default_host ? d.default_host + ':' + (d.default_port || '') : (d.default_serial_port || d.default_address || '—') }}</span>
                </div>
                <div class="cat-used">已用于：{{ (d.used_in || []).join('、') || '—' }}</div>
              </div>
              <button class="btn sm" @click="addFromCatalog(d)">添加到 BOM</button>
            </div>
            <div v-if="!catalogFiltered.length" class="empty-text center">没有匹配的设备型号</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.mb-page { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; min-width: 0; }
.mb-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; display: flex; flex-direction: column; gap: 14px; }
.panel-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.panel-title { font-size: 15px; font-weight: 700; letter-spacing: 0.5px; }
.panel-sub { color: var(--muted); font-size: 12.5px; }
.head-actions { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }
.head-actions .btn { text-decoration: none; }
.sub-title { font-size: 13.5px; font-weight: 700; color: var(--accent); letter-spacing: 0.5px; }
.sub-note { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 8px; }

/* 级联 */
.cascade { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
.cascade .field { flex: 1 1 220px; min-width: 200px; }
.cascade-arrow { color: var(--border-strong); font-size: 20px; line-height: 1; padding-bottom: 10px; }
.cascade-arrow.on { color: var(--accent); }

/* 存储条 */
.store-bar {
  display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
  padding: 10px 12px; border-radius: 10px;
  background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.06), rgba(var(--accent-2-rgb), 0.04));
  border: 1px solid rgba(var(--accent-rgb), 0.2);
}
.store-item { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.store-k { color: var(--muted); font-size: 11px; letter-spacing: 0.4px; }
.store-v { font-size: 12.5px; font-weight: 600; word-break: break-all; }

/* 属性区 */
.attr-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.attr-desc { grid-column: 1 / -1; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field-label { font-size: 12.5px; font-weight: 600; }
.field-label em { color: var(--err); font-style: normal; }
.input.sel { appearance: auto; }
.input.area { resize: vertical; font-family: inherit; }

/* BOM 操作条 */
.bom-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.bom-actions .spacer { flex: 1; }
.btn.sm { padding: 6px 12px; font-size: 12.5px; }
.btn.sm { text-decoration: none; }

/* BOM 表格 */
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
.bom-table { width: 100%; border-collapse: collapse; font-size: 12.5px; min-width: 1180px; }
.bom-table th {
  text-align: left; padding: 8px 10px; font-size: 11.5px; font-weight: 600;
  color: var(--muted); background: var(--panel-2); border-bottom: 1px solid var(--border); white-space: nowrap;
}
.bom-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
.bom-table tr:last-child td { border-bottom: none; }
.th-idx, .td-idx { width: 34px; text-align: center; color: var(--muted); font-family: var(--mono); }
.th-ops, .td-ops { width: 150px; }
.td-center { text-align: center; }
.td-conn { min-width: 240px; }
.input.cell { padding: 5px 8px; font-size: 12.5px; border-radius: 6px; margin-bottom: 4px; }
.input.cell:last-child { margin-bottom: 0; }
.switch { display: flex; flex-direction: column; align-items: center; gap: 3px; font-size: 11px; color: var(--muted); }
.conn-hint { color: var(--muted); font-size: 11px; }
.iface { font-family: var(--mono); font-size: 11px; color: var(--accent); }
.td-ops { display: flex; flex-wrap: wrap; gap: 4px; }
.mini { padding: 2px 7px; font-size: 11.5px; border-radius: 6px; cursor: pointer; border: 1px solid var(--border); background: #fff; color: var(--muted); }
.mini:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.mini.danger:hover:not(:disabled) { border-color: var(--err); color: var(--err); }
.mini:disabled { opacity: 0.5; cursor: not-allowed; }

.bom-foot { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.foot-hint { flex: 1; min-width: 260px; color: var(--muted); font-size: 12px; line-height: 1.7; }

/* 使用信息卡 */
.use-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.use-card { background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.use-k { color: var(--muted); font-size: 11.5px; }
.use-v { font-size: 13.5px; font-weight: 700; }
.use-note { color: var(--muted); font-size: 11.5px; }
.ok-text { color: var(--ok); }
.warn-text { color: var(--warn); }

.actions { display: flex; gap: 10px; flex-wrap: wrap; }
.actions .btn { text-decoration: none; }

.badge { display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 999px; font-size: 11.5px; font-weight: 600; border: 1px solid var(--border); color: var(--muted); }
.badge.warn { color: var(--warn); border-color: rgba(180, 83, 9, 0.4); background: rgba(180, 83, 9, 0.1); }

.empty { display: flex; flex-direction: column; gap: 8px; padding: 22px 14px; text-align: center; }
.empty-title { font-size: 14px; font-weight: 700; }
.empty-text { color: var(--muted); font-size: 12.5px; line-height: 1.7; }
.empty-text.center { text-align: center; }

/* 模板库弹窗 */
.modal-mask { position: fixed; inset: 0; background: rgba(29, 23, 51, 0.45); display: flex; align-items: center; justify-content: center; z-index: 60; padding: 20px; }
.modal { background: #fff; border-radius: 12px; border: 1px solid var(--border); width: min(880px, 100%); max-height: 82vh; display: flex; flex-direction: column; gap: 12px; padding: 16px 18px; }
.modal-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.modal-head .btn { margin-left: auto; }
.cat-list { overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.cat-row { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; background: var(--panel-2); }
.cat-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.cat-name { font-size: 13px; font-weight: 700; }
.cat-model { font-family: var(--mono); font-size: 12px; color: var(--accent); margin-left: 6px; }
.cat-meta { color: var(--muted); font-size: 11.5px; }
.cat-used { color: var(--muted); font-size: 11px; }

/* 提示 */
.tip { padding: 10px 14px; border-radius: 9px; font-size: 13px; }
.tip-loading { color: var(--muted); background: var(--panel); border: 1px solid var(--border); }
.tip-err { color: var(--err); background: rgba(220, 38, 38, 0.1); border: 1px solid rgba(220, 38, 38, 0.3); }
.tip-ok { color: var(--ok); background: rgba(21, 128, 61, 0.1); border: 1px solid rgba(21, 128, 61, 0.3); }
.mono { font-family: var(--mono); }
</style>
