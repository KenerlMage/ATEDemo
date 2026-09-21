<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const loading = ref(true)
const error = ref('')
const records = ref([])
const querying = ref(false)
const UUT_RE = /^[A-Za-z0-9]{12}$/

// ---- 筛选条件 ----
const filterDate = ref('')      // YYYY-MM-DD
const filterProject = ref('')   // 项目 / TPS 名称 (模糊)
const filterBatch = ref('')     // 批次 (模糊)
const filterUut = ref('')       // UUT (12 位英数)

// 通过/不通过: 颜色差异化 (OK 绿 / NOK 红)
const resultBadge = (r) => {
  const v = (r.result || (r.status === 'completed' ? 'OK' : 'NOK')).toUpperCase()
  return { text: v, cls: v === 'OK' ? 'st-ok' : 'st-nok' }
}

function reportHref(r) {
  if (!r.report_file) return ''
  return '/api/reports/' + r.report_file.split('/').pop()
}

async function load() {
  loading.value = true
  error.value = ''
  querying.value = true
  try {
    const uut = filterUut.value.trim()
    if (uut && !UUT_RE.test(uut)) {
      error.value = 'UUT 名称必须为 12 位英文与数字组合（留空查询全部）'
      return
    }
    const filters = {
      uut: uut || undefined,
      date: filterDate.value || undefined,
      project: filterProject.value.trim() || undefined,
      batch: filterBatch.value.trim() || undefined
    }
    const res = await api.getRecords(filters)
    if (!res.success) throw new Error(res.message || '查询失败')
    records.value = res.records || []
  } catch (e) {
    error.value = '查询失败：' + e.message
  } finally {
    loading.value = false
    querying.value = false
  }
}

function resetFilters() {
  filterDate.value = ''
  filterProject.value = ''
  filterBatch.value = ''
  filterUut.value = ''
  load()
}

onMounted(load)
</script>

<template>
  <div class="records-page">
    <!-- 页头 -->
    <section class="panel records-head">
      <div class="head-left">
        <div class="panel-title">🗂 历史测试记录</div>
        <div class="head-sub" v-if="!loading && !error">
          共 {{ records.length }} 条记录
          <span v-if="filterDate || filterProject || filterBatch || filterUut" class="filter-tag">已筛选</span>
        </div>
      </div>
      <!-- 筛选表单: 日期 / 项目 / 批次 / UUT -->
      <div class="filter-row">
        <label class="filter-field">
          <span class="filter-label">日期</span>
          <input v-model="filterDate" type="date" class="input" :disabled="querying" @keyup.enter="load" />
        </label>
        <label class="filter-field">
          <span class="filter-label">项目</span>
          <input v-model="filterProject" class="input" placeholder="项目 / TPS 名称" :disabled="querying" @keyup.enter="load" />
        </label>
        <label class="filter-field">
          <span class="filter-label">批次</span>
          <input v-model="filterBatch" class="input" placeholder="批次号" :disabled="querying" @keyup.enter="load" />
        </label>
        <label class="filter-field">
          <span class="filter-label">UUT</span>
          <input v-model="filterUut" class="input mono" placeholder="12 位英数" maxlength="12" :disabled="querying" @keyup.enter="load" />
        </label>
        <div class="filter-actions">
          <button class="btn primary" :disabled="querying" @click="load">
            {{ querying ? '查询中…' : '查询' }}
          </button>
          <button class="btn ghost" :disabled="querying" @click="resetFilters">重置</button>
        </div>
      </div>
    </section>

    <!-- 加载中 -->
    <div v-if="loading" class="tip tip-loading">⏳ 正在加载历史测试记录…</div>
    <!-- 错误 -->
    <div v-else-if="error" class="tip tip-err">⚠ {{ error }}</div>
    <!-- 空态 -->
    <div v-else-if="!records.length" class="tip tip-empty">暂无测试记录，去「测试执行」页运行一次 TPS 吧</div>
    <!-- 记录表格 -->
    <section v-else class="panel records-table-wrap">
      <table class="records-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>批次</th>
            <th>测试项目（TPS）</th>
            <th>项目（用例）</th>
            <th>设备编号</th>
            <th>UUT</th>
            <th>UUT 类型（PN）</th>
            <th>操作员工号</th>
            <th>结果</th>
            <th>耗时(s)</th>
            <th>报告</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in records" :key="r.id">
            <td class="td-date">{{ (r.start_time || '').slice(0, 10) }}</td>
            <td class="td-mono">{{ r.batch || '—' }}</td>
            <td>
              <div class="td-main">{{ r.tps_name }}</div>
              <div class="td-sub">{{ r.tps_id }}</div>
            </td>
            <td class="td-num">通过 {{ r.passed }}/{{ r.total }}</td>
            <td class="td-mono">{{ r.equipment_serial || '—' }}</td>
            <td class="td-mono td-uut">{{ r.uut }}</td>
            <td class="td-mono">{{ r.part_no || r.uut.slice(0, 10) }}</td>
            <td class="td-mono">{{ r.operator || '—' }}</td>
            <td>
              <span class="st-badge" :class="resultBadge(r).cls">{{ resultBadge(r).text }}</span>
            </td>
            <td class="td-num">{{ (r.duration ?? 0).toFixed(2) }}</td>
            <td>
              <a
                v-if="reportHref(r)"
                :href="reportHref(r)"
                target="_blank"
                rel="noopener"
                class="report-link"
                title="打开测试报告（新窗口）"
              >📄 查看</a>
              <span v-else class="td-muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.records-page { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }

.records-head { display: flex; flex-direction: column; align-items: stretch; gap: 12px; }
.head-left { display: flex; flex-direction: column; gap: 4px; }
.head-sub { color: var(--muted); font-size: 13px; }
.filter-tag {
  margin-left: 8px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: rgba(var(--accent-rgb), 0.12);
  border: 1px solid rgba(var(--accent-rgb), 0.3);
}

/* ---- 筛选表单 ---- */
.filter-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}
.filter-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 130px;
}
.filter-field .input { width: 100%; }
.filter-label { color: var(--muted); font-size: 11px; letter-spacing: 0.5px; }
.filter-actions { display: flex; gap: 8px; margin-left: auto; }

.query-row { display: flex; gap: 8px; }
.query-row .input { width: 300px; max-width: 60vw; }

.records-table-wrap { padding: 0; overflow-x: auto; }
.records-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 1080px; }
.records-table th, .records-table td {
  text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.records-table th { color: var(--muted); font-weight: 600; font-size: 12px; background: var(--panel-2); }
.records-table tbody tr:hover { background: rgba(var(--accent-rgb), 0.05); }
.td-mono { font-family: var(--mono); }
.td-uut { font-weight: 700; }
.td-main { color: var(--text); font-weight: 600; }
.td-sub { color: var(--muted); font-size: 11px; font-family: var(--mono); }
.td-num { font-family: var(--mono); color: var(--text); }
.td-muted { color: var(--muted); }
.td-date { color: var(--muted); }

.st-badge { padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }
/* 通过/不通过: 字体颜色差异化 (OK 绿色加粗 / NOK 红色加粗) */
.st-ok { color: var(--ok, #15803d); background: rgba(21, 128, 61, 0.12); border: 1px solid rgba(21, 128, 61, 0.35); }
.st-nok { color: var(--err, #dc2626); background: rgba(220, 38, 38, 0.12); border: 1px solid rgba(220, 38, 38, 0.35); }

.report-link {
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 6px;
  border: 1px solid rgba(var(--accent-rgb), 0.3);
  transition: all 0.15s ease;
}
.report-link:hover { background: rgba(var(--accent-rgb), 0.12); }

.tip { padding: 12px 16px; border-radius: 10px; font-size: 14px; }
.tip-loading { color: var(--muted); background: var(--panel); border: 1px solid var(--border); }
.tip-err { color: var(--err, #dc2626); background: rgba(220, 38, 38, 0.1); border: 1px solid rgba(220, 38, 38, 0.3); }
.tip-empty { color: var(--muted); background: var(--panel); border: 1px dashed var(--border); text-align: center; padding: 32px; }
</style>
