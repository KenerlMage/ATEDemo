import { createRouter, createWebHistory } from 'vue-router'
import LoginView from '../views/LoginView.vue'
import ExecuteView from '../views/ExecuteView.vue'
import EquipmentView from '../views/EquipmentView.vue'
import AssistantView from '../views/AssistantView.vue'
import RecordsView from '../views/RecordsView.vue'
import TestbenchNavView from '../views/TestbenchNavView.vue'
import TestbenchRegisterView from '../views/TestbenchRegisterView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/login' },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/execute', name: 'execute', component: ExecuteView, meta: { requiresAuth: true } },
    // 装备属性配置页 (设备来自测试台注册 BOM, 可编程设备支持右键改址)
    { path: '/equipment', name: 'equipment', component: EquipmentView, meta: { requiresAuth: true } },
    // 装备助手页 (控制装备的小工具集: 数字示波器等)
    { path: '/assistant', name: 'assistant', component: AssistantView, meta: { requiresAuth: true } },
    // 历史测试记录查询页 (与装备清单并列)
    { path: '/records', name: 'records', component: RecordsView, meta: { requiresAuth: true } },
    // 测试台导航页: 已注册测试台以独立标签展示 + 其他页面导航入口
    { path: '/testbenches', name: 'testbenches', component: TestbenchNavView, meta: { requiresAuth: true } },
    // 新增测试台注册页 (三步: 选类型+BOM/工位 -> 设备配置 -> 注册并验证)
    { path: '/testbenches/register', name: 'testbench-register', component: TestbenchRegisterView, meta: { requiresAuth: true } }
  ]
})

router.beforeEach((to) => {
  const username = localStorage.getItem('ate_username')
  if (to.meta.requiresAuth && !username) return { name: 'login' }
  if (to.name === 'login' && username) return { name: 'execute' }
})

export default router
