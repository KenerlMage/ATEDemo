<script setup>
/**
 * 元数据管理 · 装备树管理
 *  - 维护装备树三级节点: 产品 → 子系统 → 测试台类型（含类型属性）
 *  - 所有节点落盘到后端的 tree 文件夹 (default: backend/tree/tree.json)
 *  - 首次进入后端会自动把既有预设 (testresource/testbench_presets.json) 纳入管理
 *  - 这里的节点即「新建测试台」注册向导的级联选项来源
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()

const loading = ref(true)
const error = ref('')
const tip = ref('')
const tipErr = ref(false)
const busy = ref(false)

const overview = ref(null)
const tree = ref([])
const expandedProducts = reactive({})
const expandedSubs = reactive({})

// 选中节点: kind = product | subsystem | preset；'' = 未选中（右侧显示新建/说明）
const sel = reactive({ kind: '', id: '', product: '', subsystem: '' })
const mode = ref('')            // '' | 'new-product' | 'new-subsystem' | 'new-preset' | 'edit'
const formErr = ref([])

const form = reactive({
  id: '',
  name: '',
  note: '',
  products: [],
  category: '',
  description: '',
  typical_dut: '',
  recommended_cycle: '',
  product: '',
  subsystem: '',
  original_id: '',
  copy_bom: true,
  copy_bom_from: ''
})

const PRODUCT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/
const NODE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$/

const allProducts = computed(() => (tree.value || []).map((p) => ({ id: p.product, name: p.product_name })))
const allSubsystems = computed(() => {
  const out = []
  for (const p of tree.value || []) {
    for (const s of p.subsystems || []) {
      if (!out.some((x) => x.id === s.subsystem)) out.push({ id: s.subsystem, name: s.subsystem_name })
    }
  }
  return out
})
// 当前选中产品下的子系统（新建类型时用）
const subsystemsOfProduct = computed(() => {
  const node = (tree.value || []).find((p) => p.product === form.product)
  return node ? node.subsystems || [] : []
})
// 新建类型时可复制的 BOM 模板（同子系统内的其他类型）
const bomTemplates = computed(() => {
  const out = []
  for (const p of tree.value || []) {
    for (const s of p.subsystems || []) {
      for (const t of s.preset_types || []) {
        out.push({ id: t.id, label: `${t.name}（${t.id}）· BOM ${t.bom_count}` })
      }
    }
  }
  return out
})

const editorTitle = computed(() => {
  if (mode.value === 'new-product') return '新增产品节点'
  if (mode.value === 'new-subsystem') return '新增子系统节点'
  if (mode.value === 'new-preset') return '新增测试台类型节点'
  if (sel.kind === 'product') return `产品节点 · ${sel.id}`
  if (sel.kind === 'subsystem') return `子系统节点 · ${sel.id}`
  if (sel.kind === 'preset') return `测试台类型 · ${sel.id}`
  return '节点编辑'
})

async function load(keepSel = true) {
  loading.value = true
  error.value = ''
  try {
    const [ov, tr] = await Promise.all([api.getMetadataOverview(), api.getMetadataTree()])
    if (!tr.success) throw new Error(tr.message || '读取装备树失败')
    overview.value = ov && ov.success !== false ? ov : null
    tree.value = tr.tree || []
    // 默认展开全部产品，便于一览
    for (const p of tree.value) if (expandedProducts[p.product] === undefined) expandedProducts[p.product] = true
    if (keepSel && sel.kind === 'preset' && !findPreset(sel.id)) clearSel()
    if (keepSel && sel.kind === 'product' && !tree.value.some((p) => p.product === sel.id)) clearSel()
  } catch (e) {
    error.value = `无法连接后端：${e.message}`
  } finally {
    loading.value = false
  }
}

function flash(text, isErr = false) {
  tip.value = text
  tipErr.value = isErr
}

function clearSel() {
  sel.kind = ''
  sel.id = ''
  sel.product = ''
  sel.subsystem = ''
  mode.value = ''
  formErr.value = []
}

function findPreset(id) {
  for (const p of tree.value) {
    for (const s of p.subsystems || []) {
      const t = (s.preset_types || []).find((x) => x.id === id)
      if (t) return { preset: t, product: p, subsystem: s }
    }
  }
  return null
}

function toggleProduct(pid) {
  expandedProducts[pid] = !expandedProducts[pid]
}
function toggleSub(pid, sid) {
  const k = `${pid}/${sid}`
  expandedSubs[k] = !expandedSubs[k]
}

// ---------- 选中 / 打开表单 ----------

function selectProduct(p) {
  mode.value = 'edit'
  sel.kind = 'product'
  sel.id = p.product
  sel.product = p.product
  sel.subsystem = ''
  formErr.value = []
  Object.assign(form, {
    id: p.product,
    name: p.product_name,
    note: p.note || '',
    products: [],
    category: '',
    description: '',
    typical_dut: '',
    recommended_cycle: '',
    product: p.product,
    subsystem: '',
    original_id: p.product,
    copy_bom: true,
    copy_bom_from: ''
  })
}

function selectSubsystem(p, s) {
  mode.value = 'edit'
  sel.kind = 'subsystem'
  sel.id = s.subsystem
  sel.product = p.product
  sel.subsystem = s.subsystem
  formErr.value = []
  const declared = allSubsystems.value.find((x) => x.id === s.subsystem)
  void declared
  Object.assign(form, {
    id: s.subsystem,
    name: s.subsystem_name,
    note: s.note || '',
    products: declaredProducts(s.subsystem),
    category: '',
    description: '',
    typical_dut: '',
    recommended_cycle: '',
    product: p.product,
    subsystem: s.subsystem,
    original_id: s.subsystem,
    copy_bom: true,
    copy_bom_from: ''
  })
}

// 子系统声明的所属产品（tree 载荷只带当前分支，需从原始 store 补全）
const storeProducts = ref({})
function declaredProducts(sid) {
  const list = storeProducts.value[sid]
  return Array.isArray(list) ? [...list] : [sel.product].filter(Boolean)
}

function selectPreset(p, s, t) {
  mode.value = 'edit'
  sel.kind = 'preset'
  sel.id = t.id
  sel.product = p.product
  sel.subsystem = s.subsystem
  formErr.value = []
  Object.assign(form, {
    id: t.id,
    name: t.name,
    note: '',
    products: [],
    category: t.category || '',
    description: t.description || '',
    typical_dut: t.typical_dut || '',
    recommended_cycle: t.recommended_cycle || '',
    product: p.product,
    subsystem: s.subsystem,
    original_id: t.id,
    copy_bom: true,
    copy_bom_from: ''
  })
}

function newProduct() {
  clearSel()
  mode.value = 'new-product'
  Object.assign(form, { id: '', name: '', note: '', products: [], category: '', description: '', typical_dut: '', recommended_cycle: '', product: '', subsystem: '', original_id: '', copy_bom: true, copy_bom_from: '' })
}

function newSubsystem(productId = '') {
  const pid = productId || sel.product || (tree.value[0] && tree.value[0].product) || ''
  clearSel()
  mode.value = 'new-subsystem'
  Object.assign(form, { id: '', name: '', note: '', products: pid ? [pid] : [], category: '', description: '', typical_dut: '', recommended_cycle: '', product: pid, subsystem: '', original_id: '', copy_bom: true, copy_bom_from: '' })
}

function newPreset(productId = '', subsystemId = '') {
  clearSel()
  mode.value = 'new-preset'
  Object.assign(form, { id: '', name: '', note: '', products: [], category: '', description: '', typical_dut: '', recommended_cycle: '', product: productId || '', subsystem: subsystemId || '', original_id: '', copy_bom: true, copy_bom_from: '' })
}

// ---------- 保存 / 删除 ----------

function validate() {
  const errs = []
  if (mode.value === 'new-product' || sel.kind === 'product') {
    if (!form.id.trim()) errs.push('产品编号不能为空')
    else if (!PRODUCT_ID_RE.test(form.id.trim())) errs.push('产品编号仅允许字母/数字/._-，以字母或数字开头（如 3000-C）')
    if (!form.name.trim()) errs.push('产品名称不能为空')
  } else if (mode.value === 'new-subsystem' || sel.kind === 'subsystem') {
    if (!form.id.trim()) errs.push('子系统编号不能为空')
    else if (!PRODUCT_ID_RE.test(form.id.trim())) errs.push('子系统编号仅允许字母/数字/._-，以字母或数字开头（如 DSP）')
    if (!form.name.trim()) errs.push('子系统名称不能为空')
    if (!form.products.length) errs.push('请至少勾选一个所属产品（子系统挂在产品下）')
  } else if (mode.value === 'new-preset' || sel.kind === 'preset') {
    if (!form.id.trim()) errs.push('测试台类型编号不能为空')
    else if (!NODE_ID_RE.test(form.id.trim())) errs.push('类型编号仅允许字母/数字/._-，以字母或数字开头（如 PB-PWR-02）')
    if (!form.name.trim()) errs.push('测试台类型名称不能为空')
    if (!form.product) errs.push('请选择所属产品')
    if (!form.subsystem) errs.push('请选择所属子系统')
  }
  formErr.value = errs
  return errs.length === 0
}

async function save() {
  if (!validate()) {
    flash('请先修正表单中的问题', true)
    return
  }
  busy.value = true
  try {
    let res
    if (mode.value === 'new-product' || sel.kind === 'product') {
      res = await api.saveMetadataProduct({
        id: form.id.trim(),
        name: form.name.trim(),
        note: form.note,
        original_id: mode.value === 'edit' ? form.original_id : undefined
      })
    } else if (mode.value === 'new-subsystem' || sel.kind === 'subsystem') {
      res = await api.saveMetadataSubsystem({
        id: form.id.trim(),
        name: form.name.trim(),
        products: form.products,
        note: form.note,
        original_id: mode.value === 'edit' ? form.original_id : undefined
      })
    } else {
      res = await api.saveMetadataPreset({
        id: form.id.trim(),
        name: form.name.trim(),
        category: form.category,
        description: form.description,
        typical_dut: form.typical_dut,
        recommended_cycle: form.recommended_cycle,
        product: form.product,
        subsystem: form.subsystem,
        copy_bom: mode.value === 'new-preset' ? form.copy_bom : false,
        copy_bom_from: mode.value === 'new-preset' ? form.copy_bom_from : '',
        original_id: mode.value === 'edit' ? form.original_id : undefined
      })
    }
    if (!res.success) throw new Error(res.message || '保存失败')
    flash(res.message || '已保存')
    await load(false)
    // 保存后重新定位到该节点
    const kind = mode.value === 'new-product' || sel.kind === 'product' ? 'product' : mode.value === 'new-subsystem' || sel.kind === 'subsystem' ? 'subsystem' : 'preset'
    mode.value = 'edit'
    sel.kind = kind
    sel.id = form.id.trim()
    form.original_id = form.id.trim()
    if (kind === 'preset') await loadProductsOfSubsystem()
  } catch (e) {
    flash(`保存失败：${e.message}`, true)
  } finally {
    busy.value = false
  }
}

async function loadProductsOfSubsystem() {
  // 子系统归属需要原始 store 数据（tree 载荷按产品分支拆分）
  try {
    const res = await api.getMetadataStore()
    if (res.success) {
      const map = {}
      for (const s of res.store.subsystems || []) map[s.id] = s.products || []
      storeProducts.value = map
      if (sel.kind === 'subsystem' && storeProducts.value[sel.id]) form.products = [...storeProducts.value[sel.id]]
    }
  } catch {
    /* 静默 */
  }
}

async function removeNode(kind, id, label) {
  const nm = label || id
  if (!window.confirm(`确认删除${kind}「${nm}」？该操作不可撤销。`)) return
  busy.value = true
  try {
    let res
    if (kind === '产品') res = await api.deleteMetadataProduct(id)
    else if (kind === '子系统') res = await api.deleteMetadataSubsystem(id)
    else res = await api.deleteMetadataPreset(id)
    if (!res.success) throw new Error(res.message || '删除失败')
    flash(res.message || '已删除')
    clearSel()
    await load(false)
  } catch (e) {
    flash(`删除失败：${e.message}`, true)
  } finally {
    busy.value = false
  }
}

async function reseed(mode) {
  const msg = mode === 'reset'
    ? '全量重建将以 testresource/testbench_presets.json 为准覆盖 tree 文件夹内容（手工新增的节点会丢失），确认继续？'
    : '从预设文件补齐缺失的产品 / 子系统 / 测试台类型与 BOM 文件，确认继续？'
  if (!window.confirm(msg)) return
  busy.value = true
  try {
    const res = await api.reseedMetadata(mode)
    if (!res.success) throw new Error(res.message || '导入失败')
    flash(res.message || '已导入')
    await load(false)
  } catch (e) {
    flash(`导入失败：${e.message}`, true)
  } finally {
    busy.value = false
  }
}

function gotoBom(presetId) {
  router.push({ path: '/metadata/bom', query: { preset: presetId } })
}

onMounted(async () => {
  await load(false)
  await loadProductsOfSubsystem()
})
</script>

<template>
  <div class="md-page">
    <!-- ============ 页头 ============ -->
    <section class="md-panel">
      <div class="panel-head">
        <div>
          <div class="panel-title">元数据管理 · 装备树管理</div>
          <div class="panel-sub">
            维护装备树三级节点：产品 → 子系统 → 测试台类型；节点保存到后端 tree 文件夹，并作为「新建测试台」注册向导的级联选项来源
          </div>
        </div>
        <div class="head-actions">
          <router-link to="/metadata/bom" class="btn">测试台BOM管理</router-link>
          <button class="btn" :disabled="busy" @click="load(true)">刷新</button>
        </div>
      </div>

      <div class="metrics">
        <div class="metric">
          <div class="metric-label">产品节点</div>
          <div class="metric-value">{{ overview ? overview.product_count : '—' }}</div>
          <div class="metric-foot">装备树第一级</div>
        </div>
        <div class="metric">
          <div class="metric-label">子系统节点</div>
          <div class="metric-value">{{ overview ? overview.subsystem_count : '—' }}</div>
          <div class="metric-foot">挂在产品下共 {{ overview ? overview.subsystem_branch_count : '—' }} 处分支</div>
        </div>
        <div class="metric">
          <div class="metric-label">测试台类型</div>
          <div class="metric-value accent">{{ overview ? overview.preset_type_count : '—' }}</div>
          <div class="metric-foot">第三级 · 每类一张 BOM</div>
        </div>
        <div class="metric">
          <div class="metric-label">BOM 设备</div>
          <div class="metric-value">{{ overview ? overview.bom_device_total : '—' }}</div>
          <div class="metric-foot">可编程 {{ overview ? overview.programmable_total : '—' }} 台</div>
        </div>
        <div class="metric">
          <div class="metric-label">被引用类型</div>
          <div class="metric-value">{{ overview ? overview.used_type_count : '—' }}</div>
          <div class="metric-foot">已注册测试台 {{ overview ? overview.registered_testbench_total : '—' }} 台</div>
        </div>
      </div>

      <!-- 存储位置 -->
      <div class="store-bar">
        <span class="store-item">
          <span class="store-k">tree 文件夹</span>
          <span class="store-v mono">{{ overview ? overview.tree_dir : '—' }}</span>
        </span>
        <span class="store-item">
          <span class="store-k">装备树文件</span>
          <span class="store-v mono">tree.json</span>
        </span>
        <span class="store-item">
          <span class="store-k">BOM 目录</span>
          <span class="store-v mono">bom/&lt;类型编号&gt;.json</span>
        </span>
        <span class="store-item">
          <span class="store-k">最近更新</span>
          <span class="store-v">{{ overview ? overview.updated_at : '—' }}</span>
        </span>
        <span class="store-actions">
          <button class="btn sm" :disabled="busy" @click="reseed('merge')">从预设文件补齐</button>
          <button class="btn sm ghost" :disabled="busy" @click="reseed('reset')">全量重建</button>
        </span>
      </div>
      <div v-if="overview && overview.seed_source" class="seed-note">
        既有预设已纳入管理（导入来源：<span class="mono">{{ overview.seed_source }}</span>）：测试台属性与 BOM 均可在此维护，注册向导与测试台导航随即读取新版本。
      </div>
    </section>

    <div v-if="loading" class="tip tip-loading">正在加载元数据…</div>
    <div v-else-if="error" class="tip tip-err">{{ error }}</div>

    <template v-else>
      <div class="tree-grid">
        <!-- ============ 左：装备树 ============ -->
        <section class="md-panel tree-panel">
          <div class="panel-head">
            <div class="panel-title">装备树</div>
            <span class="panel-sub">{{ tree.length }} 个产品</span>
          </div>
          <div class="tree-actions">
            <button class="btn sm primary" @click="newProduct">＋ 新增产品</button>
            <button class="btn sm" @click="newSubsystem()">＋ 新增子系统</button>
            <button class="btn sm" @click="newPreset(sel.product, sel.subsystem)">＋ 新增测试台类型</button>
          </div>

          <div v-if="!tree.length" class="empty">
            <div class="empty-title">装备树为空</div>
            <div class="empty-text">先新增产品节点，再挂子系统，最后在子系统下新增测试台类型。</div>
          </div>

          <div v-else class="tree">
            <div v-for="p in tree" :key="p.product" class="tree-product">
              <div class="node-row prod" :class="{ active: sel.kind === 'product' && sel.id === p.product }" @click="selectProduct(p)">
                <button class="twist" @click.stop="toggleProduct(p.product)">{{ expandedProducts[p.product] ? '▾' : '▸' }}</button>
                <span class="node-name">{{ p.product_name }}</span>
                <span class="node-id mono">{{ p.product }}</span>
                <span class="node-count">{{ p.subsystem_count }} 子系统 · {{ p.preset_count }} 类型</span>
                <span class="node-ops">
                  <button class="mini" @click.stop="newSubsystem(p.product)">＋子系统</button>
                  <button class="mini" @click.stop="newPreset(p.product, (p.subsystems[0] || {}).subsystem)">＋类型</button>
                  <button class="mini danger" @click.stop="removeNode('产品', p.product, p.product_name)">删除</button>
                </span>
              </div>

              <template v-if="expandedProducts[p.product]">
                <div v-for="s in p.subsystems" :key="p.product + '/' + s.subsystem" class="tree-subsystem">
                  <div class="node-row sub" :class="{ active: sel.kind === 'subsystem' && sel.id === s.subsystem && sel.product === p.product }" @click="selectSubsystem(p, s)">
                    <button class="twist" @click.stop="toggleSub(p.product, s.subsystem)">{{ expandedSubs[p.product + '/' + s.subsystem] !== false ? '▾' : '▸' }}</button>
                    <span class="node-name">{{ s.subsystem_name }}</span>
                    <span class="node-id mono">{{ s.subsystem }}</span>
                    <span class="node-count">{{ (s.preset_types || []).length }} 个类型</span>
                    <span class="node-ops">
                      <button class="mini" @click.stop="newPreset(p.product, s.subsystem)">＋类型</button>
                      <button class="mini danger" @click.stop="removeNode('子系统', s.subsystem, s.subsystem_name)">删除</button>
                    </span>
                  </div>

                  <div v-if="expandedSubs[p.product + '/' + s.subsystem] !== false" class="tree-types">
                    <div v-if="!(s.preset_types || []).length" class="tree-empty">该子系统下暂无测试台类型，点「＋类型」新增</div>
                    <div
                      v-for="t in s.preset_types"
                      :key="t.id"
                      class="node-row preset"
                      :class="{ active: sel.kind === 'preset' && sel.id === t.id }"
                      @click="selectPreset(p, s, t)"
                    >
                      <span class="leaf-dot"></span>
                      <span class="node-name">{{ t.name }}</span>
                      <span class="node-id mono">{{ t.id }}</span>
                      <span class="node-count">BOM {{ t.bom_count }}（可编程 {{ t.programmable_count }}）</span>
                      <span v-if="t.used_by" class="badge accent">已被 {{ t.used_by }} 台引用</span>
                      <span class="node-ops">
                        <button class="mini" @click.stop="gotoBom(t.id)">BOM</button>
                        <button class="mini danger" @click.stop="removeNode('测试台类型', t.id, t.name)">删除</button>
                      </span>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </section>

        <!-- ============ 右：节点编辑 ============ -->
        <section class="md-panel editor-panel">
          <div class="panel-head">
            <div class="panel-title">{{ editorTitle }}</div>
            <span v-if="mode === 'new-product' || mode === 'new-subsystem' || mode === 'new-preset'" class="badge accent">新增</span>
            <span v-else-if="sel.kind" class="badge">编辑</span>
          </div>

          <div v-if="!mode" class="empty">
            <div class="empty-title">未选中节点</div>
            <div class="empty-text">
              在左侧装备树点击节点即可编辑；也可用「＋ 新增产品 / 新增子系统 / 新增测试台类型」创建新节点。测试台类型的设备清单在「测试台BOM管理」子页面维护。
            </div>
          </div>

          <template v-else>
            <div class="field">
              <span class="field-label">{{ sel.kind === 'preset' || mode === 'new-preset' ? '类型编号' : sel.kind === 'subsystem' || mode === 'new-subsystem' ? '子系统编号' : '产品编号' }} <em>*</em></span>
              <input v-model="form.id" class="input mono" placeholder="如 3000-C / DSP / DSP-XXX-01" />
              <span class="field-hint">编号是唯一标识，保存后写入 tree 文件夹并用于 BOM 文件名</span>
            </div>
            <div class="field">
              <span class="field-label">名称 <em>*</em></span>
              <input v-model="form.name" class="input" placeholder="如 3000-C 地面保障系统" />
            </div>

            <!-- 子系统: 所属产品多选 -->
            <template v-if="sel.kind === 'subsystem' || mode === 'new-subsystem'">
              <div class="field">
                <span class="field-label">所属产品 <em>*</em></span>
                <div class="check-grid">
                  <label v-for="p in allProducts" :key="p.id" class="check-item">
                    <input type="checkbox" :value="p.id" v-model="form.products" />
                    <span>{{ p.name }}（{{ p.id }}）</span>
                  </label>
                </div>
                <span class="field-hint">同一个子系统可同时挂在多个产品下（如 SPM 同时属于 1000-A / 2000-B）</span>
              </div>
            </template>

            <!-- 测试台类型: 归属 + 属性 -->
            <template v-if="sel.kind === 'preset' || mode === 'new-preset'">
              <div class="field-row">
                <label class="field">
                  <span class="field-label">所属产品 <em>*</em></span>
                  <select v-model="form.product" class="input sel">
                    <option value="">请选择产品</option>
                    <option v-for="p in allProducts" :key="p.id" :value="p.id">{{ p.name }}（{{ p.id }}）</option>
                  </select>
                </label>
                <label class="field">
                  <span class="field-label">所属子系统 <em>*</em></span>
                  <select v-model="form.subsystem" class="input sel">
                    <option value="">请选择子系统</option>
                    <option v-for="s in subsystemsOfProduct" :key="s.subsystem" :value="s.subsystem">{{ s.subsystem_name }}（{{ s.subsystem }}）</option>
                  </select>
                </label>
              </div>
              <div class="field-row">
                <label class="field">
                  <span class="field-label">类别</span>
                  <input v-model="form.category" class="input" placeholder="如 电源类 / 射频类" />
                </label>
                <label class="field">
                  <span class="field-label">推荐节拍</span>
                  <input v-model="form.recommended_cycle" class="input" placeholder="如 38 s / 件" />
                </label>
              </div>
              <div class="field">
                <span class="field-label">典型 DUT</span>
                <input v-model="form.typical_dut" class="input" placeholder="如 DC-DC 模块、LDO 板" />
              </div>
              <div class="field">
                <span class="field-label">描述</span>
                <textarea v-model="form.description" class="input area" rows="3" placeholder="该测试台类型的测试范围与用途"></textarea>
              </div>
              <template v-if="mode === 'new-preset'">
                <div class="field">
                  <span class="field-label">BOM 初值</span>
                  <label class="inline-check">
                    <input type="checkbox" v-model="form.copy_bom" />
                    <span>从同子系统内 BOM 最全的类型自动复制一份（推荐，避免空白 BOM）</span>
                  </label>
                  <select v-if="form.copy_bom" v-model="form.copy_bom_from" class="input sel">
                    <option value="">自动选择（同子系统内 BOM 最多的类型）</option>
                    <option v-for="t in bomTemplates" :key="t.id" :value="t.id">指定：{{ t.label }}</option>
                  </select>
                </div>
              </template>
              <div v-if="mode === 'edit'" class="inline-note">
                设备清单在 BOM 子页面维护：
                <button class="link-btn" @click="gotoBom(form.original_id || form.id)">打开「测试台BOM管理」→</button>
              </div>
            </template>

            <!-- 产品/子系统 备注 -->
            <div v-if="sel.kind !== 'preset' || mode === 'new-preset'" class="field">
              <span class="field-label">备注</span>
              <input v-model="form.note" class="input" placeholder="可选，说明该节点的范围或责任部门" />
            </div>

            <div v-if="formErr.length" class="tip tip-err">
              <div v-for="(e, i) in formErr" :key="i">· {{ e }}</div>
            </div>

            <div class="actions">
              <button class="btn primary" :disabled="busy" @click="save">保存到 tree 文件夹</button>
              <button class="btn ghost" @click="clearSel">取消</button>
              <button
                v-if="mode === 'edit'"
                class="btn ghost danger"
                :disabled="busy"
                @click="removeNode(sel.kind === 'product' ? '产品' : sel.kind === 'subsystem' ? '子系统' : '测试台类型', sel.id, form.name)"
              >
                删除该节点
              </button>
            </div>
          </template>
        </section>
      </div>

      <div v-if="tip" class="tip" :class="tipErr ? 'tip-err' : 'tip-ok'">{{ tip }}</div>
    </template>
  </div>
</template>

<style scoped>
.md-page { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; min-width: 0; }

.md-panel {
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
.head-actions { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }
.head-actions .btn { text-decoration: none; }

/* 指标块 */
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.metric { background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 3px; }
.metric-label { color: var(--muted); font-size: 12px; }
.metric-value { font-size: 22px; font-weight: 800; font-family: var(--mono); }
.metric-value.accent { color: var(--accent); }
.metric-foot { color: var(--muted); font-size: 11.5px; }

/* 存储位置条 */
.store-bar {
  display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
  padding: 10px 12px; border-radius: 10px;
  background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.06), rgba(var(--accent-2-rgb), 0.04));
  border: 1px solid rgba(var(--accent-rgb), 0.2);
}
.store-item { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.store-k { color: var(--muted); font-size: 11px; letter-spacing: 0.4px; }
.store-v { font-size: 12.5px; font-weight: 600; word-break: break-all; }
.store-actions { margin-left: auto; display: flex; gap: 8px; }
.btn.sm { padding: 6px 12px; font-size: 12.5px; }
.seed-note { color: var(--muted); font-size: 12.5px; line-height: 1.7; }

/* 左右分栏 */
.tree-grid { display: grid; grid-template-columns: minmax(360px, 1.15fr) minmax(340px, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1100px) { .tree-grid { grid-template-columns: 1fr; } }

.tree-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.tree { display: flex; flex-direction: column; gap: 8px; max-height: 62vh; overflow-y: auto; padding-right: 4px; }

.tree-product, .tree-subsystem { display: flex; flex-direction: column; gap: 2px; }
.tree-subsystem { margin-left: 16px; border-left: 2px solid var(--border); padding-left: 8px; }
.tree-types { margin-left: 18px; display: flex; flex-direction: column; gap: 2px; }
.tree-empty { color: var(--muted); font-size: 12px; padding: 6px 8px; }

.node-row {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; border-radius: 8px;
  border: 1px solid transparent; cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.node-row:hover { background: var(--panel-2); border-color: var(--border); }
.node-row.active { background: rgba(var(--accent-rgb), 0.1); border-color: rgba(var(--accent-rgb), 0.35); }
.node-row.prod .node-name { font-weight: 800; font-size: 13.5px; }
.node-row.sub .node-name { font-weight: 700; font-size: 13px; }
.node-row.preset .node-name { font-weight: 500; font-size: 12.5px; }
.twist {
  width: 18px; height: 18px; flex: 0 0 18px; padding: 0; line-height: 1;
  border: 1px solid var(--border); border-radius: 4px; background: #fff; color: var(--muted);
  font-size: 10px; cursor: pointer;
}
.twist:hover { border-color: var(--accent); color: var(--accent); }
.leaf-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent-3); flex: 0 0 auto; margin-left: 4px; }
.node-id { font-size: 11.5px; color: var(--accent); background: rgba(var(--accent-rgb), 0.08); border: 1px solid rgba(var(--accent-rgb), 0.22); border-radius: 5px; padding: 0 6px; }
.node-count { color: var(--muted); font-size: 11.5px; white-space: nowrap; }
.node-ops { margin-left: auto; display: flex; gap: 6px; }
.mini {
  padding: 2px 8px; font-size: 11.5px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: #fff; color: var(--muted);
}
.mini:hover { border-color: var(--accent); color: var(--accent); }
.mini.danger:hover { border-color: var(--err); color: var(--err); }

/* 编辑表单 */
.editor-panel { position: sticky; top: 12px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 620px) { .field-row { grid-template-columns: 1fr; } }
.field-label { font-size: 12.5px; font-weight: 600; }
.field-label em { color: var(--err); font-style: normal; }
.field-hint { color: var(--muted); font-size: 11.5px; }
.input.sel { appearance: auto; }
.input.area { resize: vertical; font-family: inherit; }
.check-grid { display: flex; flex-direction: column; gap: 6px; }
.check-item { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text); }
.inline-check { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--muted); }
.inline-note { font-size: 12.5px; color: var(--muted); }
.link-btn { border: none; background: none; color: var(--accent); cursor: pointer; font-size: 12.5px; padding: 0; text-decoration: underline; }

.actions { display: flex; gap: 10px; flex-wrap: wrap; padding-top: 4px; }
.actions .btn.danger { color: var(--err); }
.actions .btn.danger:hover:not(:disabled) { border-color: var(--err); }

.badge {
  display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 999px;
  font-size: 11.5px; font-weight: 600; border: 1px solid var(--border); color: var(--muted); white-space: nowrap;
}
.badge.accent { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.4); background: rgba(var(--accent-rgb), 0.1); }

.empty { display: flex; flex-direction: column; gap: 8px; padding: 22px 14px; text-align: center; }
.empty-title { font-size: 14px; font-weight: 700; }
.empty-text { color: var(--muted); font-size: 12.5px; line-height: 1.7; }

.tip { padding: 10px 14px; border-radius: 9px; font-size: 13px; }
.tip-loading { color: var(--muted); background: var(--panel); border: 1px solid var(--border); }
.tip-err { color: var(--err); background: rgba(220, 38, 38, 0.1); border: 1px solid rgba(220, 38, 38, 0.3); }
.tip-ok { color: var(--ok); background: rgba(21, 128, 61, 0.1); border: 1px solid rgba(21, 128, 61, 0.3); }
.mono { font-family: var(--mono); }
</style>
