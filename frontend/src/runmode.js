/**
 * 运行档位（全局状态，跨页面共享）
 *
 * 两种档位，互斥：
 *   production 生产模式 —— 必须输入 12 位工单号才能执行；设备走真机；
 *                        本轮结果在收尾后上报「云端数据库」。
 *   debug      调试模式 —— 不校验工单号；数据只落本地记录库，不上传云端；
 *                        可切换「仿真模式」（不连接真实设备）。
 *
 * 落地方式：模块级 ref + localStorage，任何页面 import 到的都是同一份状态；
 * 侧边栏的档位开关与「测试执行」页的工单/仿真控件读写的是同一份数据。
 */
import { computed, ref, watch } from 'vue'

const MODE_KEY = 'ate_run_mode'        // production | debug
const SIM_KEY = 'ate_simulate'         // '1' | '0'，仅调试模式有意义
const WO_KEY = 'ate_work_order'        // 生产模式工单号（本机记忆，方便连续测试）

/** 工单号规则：12 位数字或字母 */
export const WORK_ORDER_RE = /^[0-9A-Za-z]{12}$/
export const WORK_ORDER_LEN = 12

/** 档位元信息（供界面渲染说明文案） */
export const RUN_MODE_META = {
  production: {
    key: 'production',
    label: '生产模式',
    short: '生产',
    icon: '🏭',
    desc: '需 12 位工单 · 真机 · 上报云端'
  },
  debug: {
    key: 'debug',
    label: '调试模式',
    short: '调试',
    icon: '🛠',
    desc: '免工单 · 可切仿真 · 只落本地'
  }
}

/** 当前档位 */
export const runMode = ref(localStorage.getItem(MODE_KEY) === 'production' ? 'production' : 'debug')

/** 调试模式下的「仿真模式」开关（默认开：不连接真实设备） */
export const simulate = ref(localStorage.getItem(SIM_KEY) !== '0')

/** 生产模式工单号 */
export const workOrder = ref(localStorage.getItem(WO_KEY) || '')

watch(runMode, (v) => localStorage.setItem(MODE_KEY, v))
watch(simulate, (v) => localStorage.setItem(SIM_KEY, v ? '1' : '0'))
watch(workOrder, (v) => localStorage.setItem(WO_KEY, v))

/** 是否生产模式 */
export const isProduction = computed(() => runMode.value === 'production')
/** 是否调试模式 */
export const isDebug = computed(() => !isProduction.value)

/** 规范化后的工单号（去空格、转大写） */
export const normalizedWorkOrder = computed(() => (workOrder.value || '').trim().toUpperCase())

/** 工单号是否合法（生产模式执行的闸门） */
export const workOrderValid = computed(() => WORK_ORDER_RE.test(normalizedWorkOrder.value))

/** 设备连接方式：生产固定真机；调试取决仿真开关 */
export const deviceMode = computed(() => (isProduction.value ? 'real' : (simulate.value ? 'simulate' : 'real')))

/** 设备连接方式的中文说明 */
export const deviceModeText = computed(() => (deviceMode.value === 'real' ? '真机' : '仿真'))

/** 执行闸门：生产模式必须先填合法工单号 */
export const canExecute = computed(() => (isProduction.value ? workOrderValid.value : true))

/**
 * 提交给后端的运行参数
 * @param {{autoUpload?: boolean}} [opts]
 */
export function runOptions() {
  return {
    run_mode: runMode.value,
    work_order: isProduction.value ? normalizedWorkOrder.value : '',
    mode: deviceMode.value
  }
}

/** 切换到指定档位 */
export function setRunMode(mode) {
  runMode.value = mode === 'production' ? 'production' : 'debug'
}

/** 切换档位（生产 <-> 调试） */
export function toggleRunMode() {
  setRunMode(isProduction.value ? 'debug' : 'production')
}

/** 打开/关闭仿真模式（仅调试模式生效） */
export function toggleSimulate() {
  if (isProduction.value) return
  simulate.value = !simulate.value
}
