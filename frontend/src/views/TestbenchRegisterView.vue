<script setup>
/**
 * 新增测试台注册页 (三步流程)
 *  第一步: 选择预设测试台类型 -> 自动获取该类型标准 BOM 清单 -> 填写产线工位信息
 *  第二步: 填写 BOM 中可编程设备的配置信息 (IP / 端口 / 串口等) 并保存
 *  第三步: 注册并验证设备连通性 -> 输出自检清单
 * 支持带 ?id=xxx 进入编辑模式 (修改已注册测试台配置并重新自检)
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const router = useRouter()

const NET = ['LAN', 'ETHERNET', 'TCP', 'IP']
const SER = ['SERIAL', 'RS232', 'RS485', 'UART', 'COM']
const SERIAL_RE = /^SPMTS[0-9]{12}$/

const STEPS = [
  { n: 1, title: '选择类型与工位', desc: '产品 / 子系统 / 测试台类型级联选型，获取 BOM，填产线工位' },
  { n: 2, title: '填写设备配置', desc: '配置可编程设备的 IP / 端口 / 串口' },
  { n: 3, title: '注册并验证', desc: '提交注册，验证设备连通性，生成自检清单' }
]

const loading = ref(true)
const error = ref('')
const busy = ref(false)
const tip = ref('')
const tipErr = ref(false)

const step = ref(1)
const stepErr = ref([])
// 级联选型: 装备树结构「产品 (产品名称, 如 1000-A) → 子系统 (如 SPM/WA/SD) → 预设测试台类型」
const tree = ref([])
const productId = ref('')
const subsystemId = ref('')
const presetId = ref('')
const product = computed(() => tree.value.find((n) => n.product === productId.value) || null)
const subsystems = computed(() => (product.value ? product.value.subsystems : []))
const curSubsystem = computed(() => subsystems.value.find((s) => s.subsystem === subsystemId.value) || null)
const presetOptions = computed(() => (curSubsystem.value ? curSubsystem.value.presets : []))
const presetInfo = ref(null)        // { preset, summary }
const bom = ref([])
const devices = ref([])
const savedId = ref('')
const editing = ref(false)
const verifyMode = ref('real')
const verifyResult = ref(null)

const title = ref('')
const serial = ref('')
const station = reactive({ line: '', station: '', location: '', remark: '' })

const step1Done = computed(() => !!presetId.value && !!title.value.trim() && !!serial.value.trim())
const programmable = computed(() => devices.value.filter((d) => d.programmable))
const passive = computed(() => devices.value.filter((d) => !d.programmable))
const configuredCount = computed(() => programmable.value.filter((d) => isConfigured(d)).length)

const checkClass = (s) => (s === 'pass' ? 'c-pass' : s === 'fail' ? 'c-fail' : 'c-skip')
const checkText = (s) => (s === 'pass' ? '通过' : s === 'fail' ? '失败' : '跳过')

function isNet(d) { return NET.includes((d.interface || '').toUpperCase()) }
function isSer(d) { return SER.includes((d.interface || '').toUpperCase()) }

function isConfigured(d) {
  if (isNet(d)) return !!String(d.host || '').trim() && !!d.port
  if (isSer(d)) return !!String(d.serial_port || '').trim()
  // USB / GPIB 等: 以是否登记资源地址判定（连通性仍需人工确认）
  return !!String(d.address || '').trim()
}

function buildDevices(list) {
  return (list || []).map((d) => ({
    id: d.id || '',
    name: d.name || '',
    model: d.model || '',
    vendor: d.vendor || '',
    category: d.category || '',
    role: d.role || '',
    programmable: !!d.programmable,
    interface: (d.interface || 'NONE').toUpperCase(),
    host: d.host || d.default_host || '',
    port: d.port ?? d.default_port ?? null,
    protocol: d.protocol || '',
    serial_port: d.serial_port || d.default_serial_port || '',
    baudrate: d.baudrate ?? d.default_baudrate ?? null,
    address: d.address || d.default_address || '',
    channel: d.channel || d.default_channel || '',
    required: d.required !== false,
    note: d.note || ''
  }))
}

// ---- 数据加载 ----
async function loadTree() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.getTestbenchTree()
    if (!res.success) throw new Error(res.message || '读取预设测试台类型失败')
    tree.value = res.tree || []
  } catch (e) {
    error.value = `无法连接后端：${e.message}`
  } finally {
    loading.value = false
  }
}

// 由预设类型 id 反查它所属的产品 / 子系统 (编辑模式回填用)
function locatePreset(id) {
  for (const p of tree.value) {
    for (const s of p.subsystems) {
      if ((s.presets || []).some((x) => x.id === id)) {
        return { product: p.product, subsystem: s.subsystem }
      }
    }
  }
  return null
}

function clearPreset() {
  presetInfo.value = null
  bom.value = []
  devices.value = []
  tip.value = ''
}

function onProductChange() {
  subsystemId.value = ''
  presetId.value = ''
  clearPreset()
}

function onSubsystemChange() {
  presetId.value = ''
  clearPreset()
}

async function onPresetChange() {
  const id = presetId.value
  presetInfo.value = null
  bom.value = []
  devices.value = []
  stepErr.value = []
  if (!id) return
  try {
    const res = await api.getTestbenchPreset(id)
    if (!res.success) throw new Error(res.message || '读取预设详情失败')
    presetInfo.value = res.preset
    bom.value = res.preset.bom || []
    devices.value = buildDevices(bom.value)
    tip.value = `已获取「${res.preset.name}」标准 BOM：${bom.value.length} 台设备，其中可编程 ${devices.value.filter((d) => d.programmable).length} 台`
    tipErr.value = false
  } catch (e) {
    tipErr.value = true
    tip.value = `获取 BOM 失败：${e.message}`
  }
}

async function loadForEdit(id) {
  try {
    const res = await api.getTestbench(id)
    if (!res.success) throw new Error(res.message || '测试台不存在')
    const tb = res.testbench
    editing.value = true
    savedId.value = tb.id
    presetId.value = tb.preset_id
    title.value = tb.title || ''
    serial.value = tb.serial || ''
    Object.keys(station).forEach((k) => { station[k] = '' })
    Object.assign(station, tb.station || {})
    devices.value = buildDevices(tb.devices || [])
    verifyResult.value = tb.verification || null
    step.value = 2
    // 回填级联选型 (产品 -> 子系统 -> 类型)
    const loc = locatePreset(tb.preset_id)
    productId.value = loc ? loc.product : ''
    subsystemId.value = loc ? loc.subsystem : ''
    const p = await api.getTestbenchPreset(tb.preset_id)
    if (p.success) {
      presetInfo.value = p.preset
      bom.value = p.preset.bom || []
    }
    tip.value = `编辑模式：正在修改「${tb.title}」的设备配置`
  } catch (e) {
    error.value = `加载测试台失败：${e.message}`
    loading.value = false
  }
}

// ---- 校验 ----
function validateStep(n) {
  const errs = []
  if (n === 1) {
    if (!presetId.value) errs.push('请选择预设测试台类型')
    if (!title.value.trim()) errs.push('请填写测试台名称')
    if (!SERIAL_RE.test(serial.value.trim().toUpperCase())) errs.push('测试台编号需为 SPMTS + 12 位数字（如 SPMTS000000000123）')
    if (!station.line.trim()) errs.push('请填写产线')
    if (!station.station.trim()) errs.push('请填写工位')
    if (!devices.value.length) errs.push('BOM 设备清单为空，请重新选择测试台类型')
  }
  if (n === 2) {
    programmable.value.forEach((d) => {
      if (isNet(d)) {
        if (!String(d.host || '').trim()) errs.push(`${d.name}：缺少 IP 地址`)
        else if (!d.port) errs.push(`${d.name}：缺少端口号`)
      } else if (isSer(d)) {
        if (!String(d.serial_port || '').trim()) errs.push(`${d.name}：缺少串口号`)
      } else if (d.required && !String(d.address || '').trim()) {
        errs.push(`${d.name}：${d.interface} 接口需填写资源地址（连通性需人工确认）`)
      }
    })
    const seen = {}
    devices.value.forEach((d) => {
      if (isNet(d) && d.host) {
        const k = `${String(d.host).trim()}:${d.port}`
        seen[k] = (seen[k] || []).concat(d.name)
      }
    })
    Object.entries(seen).forEach(([k, names]) => {
      if (names.length > 1) errs.push(`设备地址冲突：${k} 被 ${names.join('、')} 同时占用`)
    })
    if (!presetId.value) errs.push('未选择测试台类型，请返回第一步')
  }
  return errs
}

function payload(status, st) {
  return {
    id: savedId.value || undefined,
    preset_id: presetId.value,
    preset_name: presetInfo.value && presetInfo.value.name ? presetInfo.value.name : '',
    title: title.value.trim(),
    serial: serial.value.trim().toUpperCase(),
    station: { ...station },
    devices: devices.value.map((d) => ({
      id: d.id,
      name: d.name,
      model: d.model,
      vendor: d.vendor,
      category: d.category,
      role: d.role,
      programmable: d.programmable,
      interface: d.interface,
      host: String(d.host || '').trim(),
      port: d.port === '' || d.port === null || d.port === undefined ? null : Number(d.port),
      protocol: d.protocol,
      serial_port: String(d.serial_port || '').trim(),
      baudrate: d.baudrate === '' || d.baudrate === null || d.baudrate === undefined ? null : Number(d.baudrate),
      address: d.address,
      channel: d.channel,
      required: d.required,
      note: d.note
    })),
    step: st,
    status
  }
}

async function saveDraft(silent = false) {
  busy.value = true
  try {
    const res = await api.saveTestbench(payload('draft', Math.max(1, Math.min(3, step.value))))
    if (!res.success) throw new Error(res.message || '保存失败')
    savedId.value = res.testbench.id
    if (!silent) {
      tipErr.value = false
      tip.value = `草稿已保存：${res.testbench.title}（${res.testbench.serial}）`
    }
    return true
  } catch (e) {
    tipErr.value = true
    tip.value = `保存失败：${e.message}`
    return false
  } finally {
    busy.value = false
  }
}

async function next() {
  stepErr.value = validateStep(step.value)
  if (stepErr.value.length) {
    tipErr.value = true
    tip.value = '请先修正以下问题'
    return
  }
  tip.value = ''
  tipErr.value = false
  if (step.value === 1) {
    if (!(await saveDraft(true))) return
  }
  if (step.value === 2) {
    if (!(await saveDraft(true))) return
  }
  step.value = Math.min(3, step.value + 1)
}

function prev() {
  stepErr.value = []
  step.value = Math.max(1, step.value - 1)
}

async function registerAndVerify() {
  const errs = [...validateStep(1), ...validateStep(2)]
  stepErr.value = errs
  if (errs.length) {
    tipErr.value = true
    tip.value = '注册前请先修正以下问题'
    return
  }
  busy.value = true
  verifyResult.value = null
  tip.value = ''
  tipErr.value = false
  try {
    const res = await api.saveTestbench(payload('registered', 3))
    if (!res.success) throw new Error(res.message || '注册失败')
    savedId.value = res.testbench.id
    const v = await api.verifyTestbench(savedId.value, verifyMode.value, verifyMode.value === 'simulate' ? 0.2 : 2)
    if (!v.success) throw new Error(v.message || '自检失败')
    verifyResult.value = v.result
    tipErr.value = v.result.overall === 'fail'
    tip.value =
      v.result.overall === 'fail'
        ? '注册已完成，但自检存在失败项，请修正设备配置或连接后重新自检'
        : `注册完成：${v.result.conclusion}`
  } catch (e) {
    tipErr.value = true
    tip.value = `注册失败：${e.message}`
  } finally {
    busy.value = false
  }
}

function resetAll() {
  productId.value = ''
  subsystemId.value = ''
  presetId.value = ''
  presetInfo.value = null
  bom.value = []
  devices.value = []
  savedId.value = ''
  editing.value = false
  verifyResult.value = null
  title.value = ''
  serial.value = ''
  Object.keys(station).forEach((k) => { station[k] = '' })
  step.value = 1
  stepErr.value = []
  tip.value = ''
}

onMounted(async () => {
  await loadTree()
  if (route.query.id) await loadForEdit(String(route.query.id))
})
</script>

<template>
  <div class="tbr-page">
    <!-- ============ 页头 + 步骤指示 ============ -->
    <section class="tbr-panel head-panel">
      <div class="panel-head">
        <div>
          <div class="panel-title">{{ editing ? '编辑测试台配置' : '新增测试台注册' }}</div>
          <div class="panel-sub">三步完成注册：选型与工位 → 设备配置 → 注册并验证连通性</div>
        </div>
        <router-link to="/testbenches" class="btn ghost">返回测试台导航</router-link>
      </div>

      <div class="stepper">
        <div v-for="s in STEPS" :key="s.n" class="step" :class="{ active: step === s.n, done: step > s.n }">
          <span class="step-marker">{{ step > s.n ? '✓' : s.n }}</span>
          <span class="step-body">
            <span class="step-title">{{ s.title }}</span>
            <span class="step-desc">{{ s.desc }}</span>
          </span>
        </div>
      </div>
    </section>

    <div v-if="loading" class="tip tip-loading">正在加载预设测试台类型…</div>
    <div v-else-if="error" class="tip tip-err">{{ error }}</div>

    <template v-else>
      <!-- ============ 第一步 ============ -->
      <section v-show="step === 1" class="tbr-panel">
        <div class="sub-title">
          ① 选择预设测试台类型
          <span class="sub-note">按装备树级联选择：产品 → 子系统 → 测试台类型</span>
        </div>

        <div class="cascade">
          <label class="field">
            <span class="field-label">产品名称（第一级）<em>*</em></span>
            <select v-model="productId" class="input sel" @change="onProductChange">
              <option value="">请选择产品</option>
              <option v-for="p in tree" :key="p.product" :value="p.product">
                {{ p.product_name }}（{{ p.product }}）
              </option>
            </select>
            <span class="field-hint">
              {{ product ? `含 ${product.subsystem_count} 个子系统 · ${product.preset_count} 个测试台类型` : '对应装备树第一级产品' }}
            </span>
          </label>

          <span class="cascade-arrow" :class="{ on: !!product }">›</span>

          <label class="field">
            <span class="field-label">子系统（第二级）<em>*</em></span>
            <select v-model="subsystemId" class="input sel" :disabled="!product" @change="onSubsystemChange">
              <option value="">{{ product ? '请选择子系统' : '请先选择产品' }}</option>
              <option v-for="s in subsystems" :key="s.subsystem" :value="s.subsystem">
                {{ s.subsystem_name }}（{{ s.subsystem }}）
              </option>
            </select>
            <span class="field-hint">
              {{ curSubsystem ? `含 ${curSubsystem.presets.length} 个预设测试台类型` : '如 SPM / WA / SD' }}
            </span>
          </label>

          <span class="cascade-arrow" :class="{ on: !!curSubsystem }">›</span>

          <label class="field">
            <span class="field-label">预设测试台类型（第三级）<em>*</em></span>
            <select v-model="presetId" class="input sel" :disabled="!curSubsystem" @change="onPresetChange">
              <option value="">{{ curSubsystem ? '请选择测试台类型' : '请先选择子系统' }}</option>
              <option v-for="p in presetOptions" :key="p.id" :value="p.id">
                {{ p.name }}（{{ p.id }}）
              </option>
            </select>
            <span class="field-hint">
              {{ presetInfo ? `BOM ${bom.length} 台 · 可编程 ${programmable.length} 台` : '选中后自动获取标准 BOM' }}
            </span>
          </label>
        </div>

        <!-- 选中类型摘要 -->
        <div v-if="presetInfo" class="preset-summary">
          <div class="ps-head">
            <span class="ps-path">{{ productId }} / {{ subsystemId }}</span>
            <span class="ps-name">{{ presetInfo.name }}</span>
            <span class="ps-cat">{{ presetInfo.category }}</span>
            <span class="ps-id mono">{{ presetInfo.id }}</span>
          </div>
          <div class="ps-desc">{{ presetInfo.description }}</div>
          <div class="ps-meta">
            <span>典型 DUT：{{ presetInfo.typical_dut || '—' }}</span>
            <span>推荐节拍：{{ presetInfo.recommended_cycle || '—' }}</span>
            <span>BOM {{ bom.length }} 台（可编程 {{ programmable.length }} 台）</span>
          </div>
        </div>

        <template v-if="presetInfo">
          <div class="sub-title">
            ② 该类型标准 BOM 清单
            <span class="sub-note">共 {{ bom.length }} 台设备，可编程 {{ devices.filter(d => d.programmable).length }} 台</span>
          </div>
          <div class="table-wrap">
            <table class="tbr-table">
              <thead>
                <tr><th>#</th><th>设备</th><th>型号 / 厂商</th><th>接口</th><th>用途</th><th>程控</th><th>BOM 默认配置</th></tr>
              </thead>
              <tbody>
                <tr v-for="(d, i) in bom" :key="d.id">
                  <td class="td-muted">{{ i + 1 }}</td>
                  <td class="td-name">{{ d.name }}</td>
                  <td>
                    <div>{{ d.model || '—' }}</div>
                    <div class="td-muted">{{ d.vendor || '—' }}</div>
                  </td>
                  <td><span class="iface">{{ (d.interface || 'NONE').toUpperCase() }}</span></td>
                  <td class="td-muted">{{ d.role || '—' }}</td>
                  <td>
                    <span class="badge" :class="d.programmable ? 'accent' : 'idle'">{{ d.programmable ? '可编程' : '手动' }}</span>
                  </td>
                  <td class="mono td-muted">
                    <template v-if="d.default_host">{{ d.default_host }}:{{ d.default_port }}</template>
                    <template v-else-if="d.default_serial_port">{{ d.default_serial_port }} @ {{ d.default_baudrate || 9600 }}</template>
                    <template v-else>{{ d.default_address || '—' }}</template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <div class="sub-title">③ 测试台与产线工位信息</div>
        <div class="form-grid">
          <label class="field">
            <span class="field-label">测试台名称 <em>*</em></span>
            <input v-model="title" class="input" placeholder="如：电源模块测试台 #1" />
          </label>
          <label class="field">
            <span class="field-label">测试台编号 <em>*</em></span>
            <input v-model="serial" class="input mono" placeholder="SPMTS + 12 位数字" />
          </label>
          <label class="field">
            <span class="field-label">产线 <em>*</em></span>
            <input v-model="station.line" class="input" placeholder="如：A3 装配线" />
          </label>
          <label class="field">
            <span class="field-label">工位 <em>*</em></span>
            <input v-model="station.station" class="input" placeholder="如：ST-07" />
          </label>
          <label class="field">
            <span class="field-label">物理位置</span>
            <input v-model="station.location" class="input" placeholder="如：一号厂房东侧" />
          </label>
          <label class="field field-wide">
            <span class="field-label">备注</span>
            <input v-model="station.remark" class="input" placeholder="选填，如工装/夹具变更说明" />
          </label>
        </div>
      </section>

      <!-- ============ 第二步 ============ -->
      <section v-show="step === 2" class="tbr-panel">
        <div class="sub-title">
          可编程设备配置
          <span class="sub-note">已配置 {{ configuredCount }} / {{ programmable.length }} 台</span>
          <button class="btn ghost mini" @click="devices = buildDevices(bom)">回填 BOM 默认值</button>
        </div>

        <div class="dev-list">
          <div v-for="d in programmable" :key="d.id" class="dev-card" :class="{ ok: isConfigured(d) }">
            <div class="dev-head">
              <div class="dev-name">
                {{ d.name }}
                <span class="iface">{{ d.interface }}</span>
                <span v-if="d.required" class="badge warn">必需</span>
              </div>
              <span class="dev-state" :class="isConfigured(d) ? 'ok' : 'pending'">
                {{ isConfigured(d) ? '已配置' : '待配置' }}
              </span>
            </div>
            <div class="dev-meta">{{ d.model || '—' }} · {{ d.vendor || '—' }}<span v-if="d.role"> · {{ d.role }}</span></div>

            <div class="dev-form">
              <template v-if="isNet(d)">
                <label class="field">
                  <span class="field-label">IP 地址 <em>*</em></span>
                  <input v-model="d.host" class="input mono" placeholder="192.168.10.21" />
                </label>
                <label class="field">
                  <span class="field-label">端口 <em>*</em></span>
                  <input v-model="d.port" class="input mono" type="number" placeholder="4000" />
                </label>
                <label class="field">
                  <span class="field-label">协议</span>
                  <input v-model="d.protocol" class="input" placeholder="VXI-11 / SCPI" />
                </label>
                <label class="field">
                  <span class="field-label">VISA 资源串</span>
                  <input v-model="d.address" class="input mono" placeholder="TCPIP0::…::INSTR" />
                </label>
                <label class="field">
                  <span class="field-label">通道</span>
                  <input v-model="d.channel" class="input mono" placeholder="CH1" />
                </label>
              </template>
              <template v-else-if="isSer(d)">
                <label class="field">
                  <span class="field-label">串口号 <em>*</em></span>
                  <input v-model="d.serial_port" class="input mono" placeholder="COM3" />
                </label>
                <label class="field">
                  <span class="field-label">波特率</span>
                  <input v-model="d.baudrate" class="input mono" type="number" placeholder="115200" />
                </label>
                <label class="field field-wide">
                  <span class="field-label">协议</span>
                  <input v-model="d.protocol" class="input" placeholder="自定义 ASCII / Modbus RTU" />
                </label>
              </template>
              <template v-else>
                <label class="field field-wide">
                  <span class="field-label">资源地址 ({{ d.interface }} 接口不支持自动探测)</span>
                  <input v-model="d.address" class="input mono" placeholder="USB0::0x1234::…::INSTR" />
                </label>
              </template>
            </div>
            <div v-if="d.note" class="dev-note">{{ d.note }}</div>
          </div>
        </div>

        <template v-if="passive.length">
          <div class="sub-title">非程控设备 / 工装（无需填写连接参数）</div>
          <div class="passive-list">
            <span v-for="d in passive" :key="d.id" class="passive-item">
              {{ d.name }} <span class="td-muted">{{ d.model }}</span>
            </span>
          </div>
        </template>
      </section>

      <!-- ============ 第三步 ============ -->
      <section v-show="step === 3" class="tbr-panel">
        <div class="sub-title">注册信息确认</div>
        <div class="info-grid">
          <div class="info-item"><span class="info-k">测试台名称</span><span class="info-v">{{ title || '—' }}</span></div>
          <div class="info-item"><span class="info-k">测试台编号</span><span class="info-v mono">{{ serial || '—' }}</span></div>
          <div class="info-item"><span class="info-k">预设类型</span><span class="info-v">{{ presetInfo ? presetInfo.name : '—' }}</span></div>
          <div class="info-item"><span class="info-k">产线 / 工位</span><span class="info-v">{{ station.line || '—' }} / {{ station.station || '—' }}</span></div>
          <div class="info-item"><span class="info-k">设备总数</span><span class="info-v">{{ devices.length }} 台（可编程 {{ programmable.length }}）</span></div>
          <div class="info-item"><span class="info-k">配置完成度</span><span class="info-v">{{ configuredCount }} / {{ programmable.length }}</span></div>
        </div>

        <div class="sub-title">连通性验证方式</div>
        <div class="mode-row">
          <label class="mode" :class="{ active: verifyMode === 'real' }">
            <input v-model="verifyMode" type="radio" value="real" />
            <span class="mode-body">
              <span class="mode-name">真实探测（推荐）</span>
              <span class="mode-desc">对网口设备执行 TCP 连通探测，对串口设备尝试打开串口并读取状态</span>
            </span>
          </label>
          <label class="mode" :class="{ active: verifyMode === 'simulate' }">
            <input v-model="verifyMode" type="radio" value="simulate" />
            <span class="mode-body">
              <span class="mode-name">模拟自检（离线演示）</span>
              <span class="mode-desc">不发起真实连接，用于现场无设备时演练注册流程，结果会标注“模拟”</span>
            </span>
          </label>
        </div>

        <div class="actions">
          <button class="btn primary" :disabled="busy" @click="registerAndVerify">
            {{ busy ? '正在注册并验证…' : '注册并验证连通性' }}
          </button>
          <button class="btn" :disabled="busy" @click="saveDraft(false)">仅保存草稿</button>
        </div>

        <!-- 自检清单 -->
        <template v-if="verifyResult">
          <div class="sub-title">
            自检清单
            <span class="sub-note">
              {{ verifyResult.mode === 'simulate' ? '离线模拟' : '真实探测' }} · {{ verifyResult.checked_at }} ·
              通过 {{ verifyResult.summary.pass }} / 失败 {{ verifyResult.summary.fail }} / 跳过 {{ verifyResult.summary.skip }}
            </span>
          </div>
          <div class="conclusion" :class="'v-' + verifyResult.overall">{{ verifyResult.conclusion }}</div>
          <div class="check-grid">
            <div v-for="c in verifyResult.checklist" :key="c.key" class="check-item" :class="checkClass(c.status)">
              <span class="check-dot"></span>
              <div class="check-body">
                <div class="check-label">
                  {{ c.label }}
                  <span v-if="c.kind === 'device' && c.interface" class="check-iface">{{ c.interface }}</span>
                </div>
                <div class="check-detail">
                  {{ c.target ? c.target + ' · ' : '' }}{{ c.detail }}
                  <span v-if="c.simulated" class="sim-tag">模拟</span>
                </div>
              </div>
              <span class="check-badge" :class="checkClass(c.status)">{{ checkText(c.status) }}</span>
            </div>
          </div>
          <div class="actions">
            <router-link to="/testbenches" class="btn primary">完成，返回测试台导航</router-link>
            <button class="btn" :disabled="busy" @click="registerAndVerify">重新自检</button>
          </div>
        </template>
      </section>

      <!-- ============ 底部操作栏 ============ -->
      <section class="tbr-panel foot-panel">
        <div v-if="stepErr.length" class="err-list">
          <div v-for="(e, i) in stepErr" :key="i" class="err-item">{{ e }}</div>
        </div>
        <div v-else-if="tip" class="tip" :class="tipErr ? 'tip-err' : 'tip-ok'">{{ tip }}</div>
        <div class="foot-actions">
          <button class="btn ghost" :disabled="step === 1 || busy" @click="prev">上一步</button>
          <button v-if="step < 3" class="btn primary" :disabled="busy" @click="next">下一步</button>
          <button v-if="step === 3" class="btn primary" :disabled="busy" @click="registerAndVerify">注册并验证</button>
          <button class="btn ghost" :disabled="busy" @click="resetAll">重置表单</button>
          <span class="foot-meta">步骤 {{ step }} / 3<span v-if="savedId"> · 已保存 ID: {{ savedId }}</span></span>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.tbr-page { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }
@media (max-width: 720px) { .tbr-page { padding: 14px; } }

.tbr-panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.head-panel { gap: 18px; }
.foot-panel { position: sticky; bottom: 0; z-index: 5; background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(6px); box-shadow: 0 -6px 18px rgba(var(--accent-rgb), 0.07); }
.panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.panel-title { font-size: 16px; font-weight: 800; }
.panel-sub { color: var(--muted); font-size: 12.5px; margin-top: 4px; }
.sub-title { font-size: 13.5px; font-weight: 700; color: var(--accent); display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sub-note { color: var(--muted); font-weight: 400; font-size: 12px; }

/* ---- 步骤条 ---- */
.stepper { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; }
.step {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 12px; border-radius: 10px;
  border: 1px solid var(--border); background: var(--panel-2);
}
.step.active { border-color: rgba(34, 211, 238, 0.5); background: rgba(34, 211, 238, 0.08); }
.step.done { border-color: rgba(52, 211, 153, 0.4); }
.step-marker {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border-radius: 8px; flex: 0 0 auto;
  background: var(--panel); border: 1px solid var(--border);
  font-family: var(--mono); font-weight: 700; font-size: 13px; color: var(--muted);
}
.step.active .step-marker { color: var(--accent); border-color: rgba(34, 211, 238, 0.5); }
.step.done .step-marker { color: var(--ok); border-color: rgba(52, 211, 153, 0.5); }
.step-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.step-title { font-size: 13.5px; font-weight: 700; }
.step-desc { color: var(--muted); font-size: 11.5px; line-height: 1.5; }

/* ---- 级联选型（产品 → 子系统 → 测试台类型） ---- */
.cascade { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr; gap: 10px; align-items: start; }
.cascade-arrow { align-self: center; margin-top: 22px; color: var(--border-strong); font-size: 22px; font-weight: 700; line-height: 1; }
.cascade-arrow.on { color: var(--accent); }
.sel {
  appearance: none;
  -webkit-appearance: none;
  padding-right: 32px;
  background-image: linear-gradient(45deg, transparent 50%, var(--accent) 50%), linear-gradient(135deg, var(--accent) 50%, transparent 50%);
  background-position: calc(100% - 17px) 50%, calc(100% - 12px) 50%;
  background-size: 5px 5px, 5px 5px;
  background-repeat: no-repeat;
  cursor: pointer;
}
.sel:disabled { background-color: var(--panel-2); color: var(--muted); cursor: not-allowed; }
.field-hint { color: var(--muted); font-size: 11.5px; min-height: 16px; }

/* ---- 选中类型摘要 ---- */
.preset-summary {
  display: flex; flex-direction: column; gap: 8px;
  padding: 13px 15px; border-radius: 11px;
  border: 1px solid rgba(var(--accent-rgb), 0.28);
  border-left: 3px solid var(--accent);
  background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.07), rgba(var(--accent-2-rgb), 0.03));
}
.ps-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ps-path { font-family: var(--mono); font-size: 11.5px; font-weight: 700; color: var(--accent); background: #fff; border: 1px solid rgba(var(--accent-rgb), 0.25); border-radius: 5px; padding: 1px 7px; }
.ps-name { font-size: 15px; font-weight: 800; }
.ps-cat { padding: 1px 8px; border-radius: 999px; font-size: 11px; color: var(--accent-2); border: 1px solid rgba(var(--accent-2-rgb), 0.35); background: rgba(var(--accent-2-rgb), 0.1); }
.ps-id { font-size: 11.5px; color: var(--muted); }
.ps-desc { color: var(--muted); font-size: 12.5px; line-height: 1.6; }
.ps-meta { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 12px; color: var(--accent); }
@media (max-width: 900px) {
  .cascade { grid-template-columns: 1fr; }
  .cascade-arrow { display: none; }
}

/* ---- 表单 ---- */
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field-wide { grid-column: 1 / -1; }
.field-label { color: var(--muted); font-size: 12px; }
.field-label em { color: var(--err); font-style: normal; }

/* ---- 设备卡 ---- */
.dev-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 12px; }
.dev-card { border: 1px solid var(--border); border-radius: 11px; padding: 13px; background: var(--panel-2); display: flex; flex-direction: column; gap: 9px; }
.dev-card.ok { border-color: rgba(52, 211, 153, 0.35); }
.dev-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.dev-name { font-size: 14px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.dev-state { font-size: 11.5px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }
.dev-state.ok { color: var(--ok); border-color: rgba(52, 211, 153, 0.4); background: rgba(52, 211, 153, 0.1); }
.dev-meta { color: var(--muted); font-size: 12px; }
.dev-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.dev-note { color: var(--muted); font-size: 11.5px; border-top: 1px dashed var(--border); padding-top: 8px; }
.passive-list { display: flex; flex-wrap: wrap; gap: 8px; }
.passive-item { padding: 5px 11px; border-radius: 8px; background: var(--panel-2); border: 1px solid var(--border); font-size: 12.5px; }

/* ---- 验证方式 ---- */
.mode-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }
.mode { display: flex; gap: 10px; padding: 11px 13px; border-radius: 10px; border: 1px solid var(--border); background: var(--panel-2); cursor: pointer; }
.mode.active { border-color: rgba(34, 211, 238, 0.5); background: rgba(34, 211, 238, 0.08); }
.mode-body { display: flex; flex-direction: column; gap: 3px; }
.mode-name { font-size: 13.5px; font-weight: 700; }
.mode-desc { color: var(--muted); font-size: 11.5px; line-height: 1.55; }

/* ---- 信息块 ---- */
.info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
.info-item { display: flex; flex-direction: column; gap: 3px; padding: 9px 12px; border-radius: 9px; background: var(--panel-2); border: 1px solid var(--border); }
.info-k { color: var(--muted); font-size: 11.5px; }
.info-v { font-size: 13.5px; font-weight: 600; word-break: break-all; }

/* ---- 表格 ---- */
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
.tbr-table { width: 100%; border-collapse: collapse; font-size: 12.5px; min-width: 780px; }
.tbr-table th { text-align: left; padding: 9px 12px; color: var(--muted); font-size: 12px; font-weight: 600; background: var(--panel-2); border-bottom: 1px solid var(--border); white-space: nowrap; }
.tbr-table td { padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
.tbr-table tr:last-child td { border-bottom: none; }
.td-name { font-weight: 600; }
.td-muted { color: var(--muted); font-size: 12px; }
.mono { font-family: var(--mono); }
.iface { padding: 1px 8px; border-radius: 6px; font-size: 11px; font-family: var(--mono); background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.3); color: var(--accent-2); }

/* ---- 徽标 ---- */
.badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; border: 1px solid var(--border); color: var(--muted); white-space: nowrap; }
.badge.accent { color: var(--accent); border-color: rgba(34, 211, 238, 0.4); background: rgba(34, 211, 238, 0.1); }
.badge.warn { color: var(--warn); border-color: rgba(251, 191, 36, 0.4); background: rgba(251, 191, 36, 0.1); }
.badge.idle { color: var(--muted); background: var(--panel); }

/* ---- 自检清单 ---- */
.conclusion { padding: 10px 14px; border-radius: 9px; font-size: 13px; font-weight: 600; border: 1px solid var(--border); }
.conclusion.v-pass { color: var(--ok); background: rgba(52, 211, 153, 0.1); border-color: rgba(52, 211, 153, 0.35); }
.conclusion.v-warn { color: var(--warn); background: rgba(251, 191, 36, 0.1); border-color: rgba(251, 191, 36, 0.35); }
.conclusion.v-fail { color: var(--err); background: rgba(248, 113, 113, 0.1); border-color: rgba(248, 113, 113, 0.35); }
.check-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 10px; }
.check-item { display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; border-radius: 9px; background: var(--panel-2); border: 1px solid var(--border); }
.check-item.c-pass { border-color: rgba(52, 211, 153, 0.3); }
.check-item.c-fail { border-color: rgba(248, 113, 113, 0.4); background: rgba(248, 113, 113, 0.07); }
.check-item.c-skip { border-color: rgba(251, 191, 36, 0.35); background: rgba(251, 191, 36, 0.06); }
.check-dot { width: 9px; height: 9px; border-radius: 50%; margin-top: 5px; flex: 0 0 auto; background: var(--muted); }
.c-pass .check-dot { background: var(--ok); }
.c-fail .check-dot { background: var(--err); }
.c-skip .check-dot { background: var(--warn); }
.check-body { flex: 1; min-width: 0; }
.check-label { font-size: 13px; font-weight: 600; }
.check-iface { margin-left: 6px; font-family: var(--mono); font-size: 11px; color: var(--muted); }
.check-detail { color: var(--muted); font-size: 12px; margin-top: 3px; word-break: break-all; }
.sim-tag { margin-left: 6px; padding: 0 6px; border-radius: 5px; font-size: 11px; color: var(--warn); border: 1px solid rgba(251, 191, 36, 0.4); }
.check-badge { padding: 1px 8px; border-radius: 6px; font-size: 11.5px; font-weight: 700; white-space: nowrap; }
.check-badge.c-pass { color: var(--ok); background: rgba(52, 211, 153, 0.12); border: 1px solid rgba(52, 211, 153, 0.35); }
.check-badge.c-fail { color: var(--err); background: rgba(248, 113, 113, 0.12); border: 1px solid rgba(248, 113, 113, 0.35); }
.check-badge.c-skip { color: var(--warn); background: rgba(251, 191, 36, 0.12); border: 1px solid rgba(251, 191, 36, 0.35); }

/* ---- 操作区 ---- */
.actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.actions a.btn { text-decoration: none; }
.foot-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.foot-meta { color: var(--muted); font-size: 12px; margin-left: auto; }
.btn.mini { padding: 4px 10px; font-size: 12px; }
.err-list { display: flex; flex-direction: column; gap: 5px; }
.err-item { color: var(--err); font-size: 12.5px; padding: 7px 11px; border-radius: 8px; background: rgba(248, 113, 113, 0.08); border: 1px solid rgba(248, 113, 113, 0.3); }
.tip { padding: 10px 14px; border-radius: 9px; font-size: 13px; }
.tip-loading { color: var(--muted); background: var(--panel); border: 1px solid var(--border); }
.tip-err { color: var(--err); background: rgba(248, 113, 113, 0.1); border: 1px solid rgba(248, 113, 113, 0.3); }
.tip-ok { color: var(--ok); background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.3); }
</style>
