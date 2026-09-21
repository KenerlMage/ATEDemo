<script setup>
/**
 * 应用外壳：左侧垂直导航（工业风紫色主题）+ 右侧内容区
 * 导航项顺序：测试执行 / 装备属性配置 / 装备助手 / 测试记录 / 测试台导航（最后一项）
 */
import { computed, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { api } from './api'

const router = useRouter()
const route = useRoute()

// 登录态: 用响应式 ref + 路由变化时同步, 避免 computed 读非响应式 localStorage 冻结
const username = ref(localStorage.getItem('ate_username') || '')
watch(
  () => route.fullPath,
  () => {
    username.value = localStorage.getItem('ate_username') || ''
  },
  { immediate: true }
)

// ---- 左侧导航项（最后一项为「测试台导航」，前面加分隔线强调） ----
const NAV = [
  {
    to: '/execute',
    icon: '▶',
    name: '测试执行',
    desc: '装备树 · 用例运行',
    code: 'RUN',
    match: (p) => p === '/execute' || p === '/'
  },
  {
    to: '/equipment',
    icon: '⚙',
    name: '装备属性配置',
    desc: '设备连接 · 右键改址',
    code: 'EQU',
    match: (p) => p.startsWith('/equipment')
  },
  {
    to: '/assistant',
    icon: '⚒',
    name: '装备助手',
    desc: '仪器控制小工具',
    code: 'AST',
    match: (p) => p.startsWith('/assistant')
  },
  {
    to: '/records',
    icon: '▤',
    name: '测试记录',
    desc: '历史记录查询',
    code: 'REC',
    match: (p) => p.startsWith('/records')
  },
  {
    to: '/metadata/tree',
    icon: '◈',
    name: '元数据管理',
    desc: '装备树 · 测试台BOM',
    code: 'MDA',
    match: (p) => p.startsWith('/metadata'),
    // 下挂两个子页面: 装备树管理 / 测试台BOM管理
    children: [
      { to: '/metadata/tree', name: '装备树管理', short: '装备树', code: 'TREE', match: (p) => p === '/metadata/tree' || p === '/metadata' },
      { to: '/metadata/bom', name: '测试台BOM管理', short: '测试台BOM', code: 'BOM', match: (p) => p.startsWith('/metadata/bom') }
    ]
  },
  {
    to: '/testbenches',
    icon: '▦',
    name: '测试台导航',
    desc: '测试台注册与自检',
    code: 'TBN',
    last: true,
    match: (p) => p.startsWith('/testbenches')
  }
]

// ---- 测试台信息（登录后获取，导航栏展示测试台名称与编号；失败静默） ----
const station = ref(null)   // { title, serial }
const stationTitle = computed(() => station.value?.title || '')
const stationSerial = computed(() => station.value?.serial || '')
const showStation = computed(() => !!username.value && !!stationSerial.value)

async function loadStation() {
  if (!username.value || station.value) return
  try {
    const res = await api.getEquipment()
    if (res && res.success !== false) station.value = res
  } catch {
    /* 静默：装备接口异常不阻塞页面 */
  }
}
// 登录跳转后（App 不重新挂载）也能触发获取
watch(() => route.fullPath, loadStation, { immediate: true })

function logout() {
  localStorage.removeItem('ate_username')
  username.value = ''
  station.value = null
  router.push('/login')
}
</script>

<template>
  <div class="app-shell" :class="{ 'is-login': !username }">
    <!-- 未登录：登录/注册页占满整屏（页面自带视觉） -->
    <main v-if="!username" class="app-main login-main">
      <router-view />
    </main>

    <!-- 已登录：左侧垂直导航 + 右侧内容 -->
    <template v-else>
      <aside class="sidebar">
        <div class="hazard-rule side-top-rule"></div>

        <!-- 品牌 -->
        <div class="brand">
          <span class="brand-mark">⚡</span>
          <span class="brand-text">
            <span class="brand-name">ATE <em>RUNNER</em></span>
            <span class="brand-sub">自动测试装备 · 控制台</span>
          </span>
        </div>

        <!-- 当前测试台（名称 + SPMTS 唯一编号） -->
        <div v-if="showStation" class="station-chip" :title="`测试台唯一编号: ${stationSerial}`">
          <span class="station-label">当前测试台</span>
          <span class="station-name">{{ stationTitle }}</span>
          <span class="station-serial">{{ stationSerial }}</span>
        </div>

        <!-- 垂直导航 -->
        <nav class="side-nav">
          <template v-for="item in NAV" :key="item.to">
            <router-link
              :to="item.to"
              class="side-item"
              :class="{ active: item.match(route.path), 'is-last': item.last }"
            >
              <span class="side-icon">{{ item.icon }}</span>
              <span class="side-body">
                <span class="side-name">{{ item.name }}</span>
                <span class="side-desc">{{ item.desc }}</span>
              </span>
              <span class="side-code">{{ item.code }}</span>
            </router-link>
            <!-- 有子页面的导航项 (元数据管理): 选中时展开两个子页面入口 -->
            <div v-if="item.children && item.match(route.path)" class="side-sub">
              <router-link
                v-for="child in item.children"
                :key="child.to"
                :to="child.to"
                class="side-sub-item"
                :class="{ active: child.match(route.path) }"
              >
                <span class="sub-dot"></span>
                <span class="sub-name">{{ child.short || child.name }}</span>
                <span class="sub-code">{{ child.code }}</span>
              </router-link>
            </div>
          </template>
        </nav>

        <!-- 底部：用户 + 退出 -->
        <div class="side-foot">
          <div class="side-user">
            <span class="user-chip">{{ username }}</span>
            <span class="side-version">ATE RUNNER v1.0</span>
          </div>
          <button class="btn ghost side-logout" @click="logout">退出登录</button>
        </div>
      </aside>

      <main class="app-main">
        <router-view />
      </main>
    </template>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100%; display: flex; align-items: stretch; }
.is-login { display: block; }
.login-main { min-height: 100vh; }

/* ================= 左侧导航（工业风） ================= */
.sidebar {
  flex: 0 0 228px;
  width: 228px;
  position: sticky;
  top: 0;
  align-self: flex-start;
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 0 14px 14px;
  background: linear-gradient(180deg, #fbfaff 0%, #ffffff 42%, #faf9ff 100%);
  border-right: 1px solid var(--border);
}
.side-top-rule { margin: 0 -14px 2px; }

/* 品牌 */
.brand { display: flex; align-items: center; gap: 10px; padding: 6px 4px 0; }
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border-radius: 9px;
  background: linear-gradient(135deg, #8b5cf6, #5b21b6);
  color: #fff;
  font-size: 18px;
  box-shadow: 0 3px 12px rgba(var(--accent-rgb), 0.3);
}
.brand-text { display: flex; flex-direction: column; min-width: 0; }
.brand-name {
  font-size: 17px;
  font-weight: 900;
  letter-spacing: 1.4px;
  background: linear-gradient(180deg, #4c1d95 0%, #6d28d9 46%, #8b5cf6 78%, #6d28d9 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  font-family: 'Arial Black', 'Segoe UI', sans-serif;
  line-height: 1.15;
}
.brand-name em { font-style: normal; color: #7c3aed; -webkit-text-fill-color: #7c3aed; }
.brand-sub { color: var(--muted); font-size: 11px; letter-spacing: 0.6px; }

/* 当前测试台 */
.station-chip {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.09), rgba(var(--accent-2-rgb), 0.06));
  border: 1px solid rgba(var(--accent-rgb), 0.22);
  border-left: 3px solid var(--accent);
}
.station-label { font-size: 10px; letter-spacing: 1px; color: var(--accent); font-weight: 700; }
.station-name { font-size: 12.5px; font-weight: 600; color: var(--text); }
.station-serial { font-family: var(--mono); font-size: 11.5px; color: var(--muted); letter-spacing: 0.4px; }

/* 垂直导航项 */
.side-nav { display: flex; flex-direction: column; gap: 6px; flex: 1; min-height: 0; overflow-y: auto; }
.side-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  text-decoration: none;
  color: var(--text);
  border: 1px solid transparent;
  border-left: 3px solid transparent;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.side-item:hover { background: var(--panel-2); border-color: var(--border); }
.side-item.active {
  background: linear-gradient(90deg, rgba(var(--accent-rgb), 0.12), rgba(var(--accent-rgb), 0.03));
  border-color: rgba(var(--accent-rgb), 0.3);
  border-left-color: var(--accent);
}
.side-icon {
  flex: 0 0 24px;
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--accent);
}
.side-item.active .side-icon { background: var(--accent); border-color: var(--accent); color: #fff; }
.side-body { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.side-name { font-size: 14px; font-weight: 600; }
.side-desc { font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.side-item.active .side-name { color: var(--accent); }
.side-code {
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 1px;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 4px;
  background: #fff;
}
/* 最后一项「测试台导航」：上分隔线强调 */
.side-item.is-last { margin-top: 12px; position: relative; }
.side-item.is-last::before {
  content: '';
  position: absolute;
  left: 3px;
  right: 3px;
  top: -7px;
  height: 1px;
  background: linear-gradient(90deg, var(--border-strong), transparent);
}

/* 子页面入口（元数据管理 → 装备树管理 / 测试台BOM管理） */
.side-sub {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 2px 0 4px 12px;
  padding-left: 10px;
  border-left: 2px solid rgba(var(--accent-rgb), 0.25);
}
.side-sub-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  text-decoration: none;
  color: var(--muted);
  font-size: 12.5px;
  border: 1px solid transparent;
}
.side-sub-item:hover { color: var(--accent); background: var(--panel-2); border-color: var(--border); }
.side-sub-item.active {
  color: var(--accent);
  font-weight: 700;
  background: linear-gradient(90deg, rgba(var(--accent-rgb), 0.12), rgba(var(--accent-rgb), 0.02));
  border-color: rgba(var(--accent-rgb), 0.28);
}
.sub-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--border-strong); flex: 0 0 auto; }
.side-sub-item.active .sub-dot { background: var(--accent); box-shadow: 0 0 6px rgba(var(--accent-rgb), 0.6); }
.sub-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub-code { font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.5px; color: var(--muted); }

/* 底部 */
.side-foot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.side-user { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.user-chip {
  padding: 3px 10px;
  border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.1);
  color: var(--accent);
  border: 1px solid rgba(var(--accent-rgb), 0.25);
  font-size: 12.5px;
  font-weight: 600;
}
.side-version { font-family: var(--mono); font-size: 10px; color: var(--muted); letter-spacing: 0.5px; }
.btn.ghost.side-logout { width: 100%; }

/* 右侧内容区 */
.app-main { flex: 1; min-width: 0; display: flex; }

/* ================= 响应式：窄屏改为顶部横向导航 ================= */
@media (max-width: 900px) {
  .app-shell { flex-direction: column; }
  .sidebar {
    width: auto;
    flex: none;
    height: auto;
    position: sticky;
    top: 0;
    z-index: 20;
    flex-direction: row;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-right: none;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    background: rgba(255, 255, 255, 0.96);
    backdrop-filter: blur(6px);
  }
  .side-top-rule { display: none; }
  .brand-sub, .station-chip, .side-desc, .side-code, .side-version { display: none; }
  .brand-name { font-size: 15px; }
  .brand-mark { width: 30px; height: 30px; flex: 0 0 30px; font-size: 15px; }
  .side-nav { flex-direction: row; gap: 4px; overflow: visible; }
  .side-sub { flex-direction: row; margin: 0 0 0 4px; padding-left: 6px; }
  .side-sub-item { padding: 6px 8px; }
  .sub-code { display: none; }
  .side-item { padding: 7px 10px; gap: 6px; white-space: nowrap; }
  .side-item.is-last { margin-top: 0; }
  .side-item.is-last::before { display: none; }
  .side-item.is-last { border-left: 1px solid var(--border-strong); }
  .side-foot {
    flex-direction: row;
    align-items: center;
    padding-top: 0;
    border-top: none;
    margin-left: auto;
  }
  .btn.ghost.side-logout { width: auto; padding: 6px 10px; }
}
</style>
