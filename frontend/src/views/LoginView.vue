<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()

// ---- License 状态 ----
const licChecking = ref(true)
const licValid = ref(false)
const licInfo = ref(null)
const licState = ref('') // not_found / invalid / valid

// ---- License 导入 ----
const importing = ref(false)
const importMsg = ref('')
const importErr = ref('')
const fileInput = ref(null)

// ---- 登录 ----
const username = ref('')
const loading = ref(false)
const error = ref('')

async function checkLicense() {
  licChecking.value = true
  try {
    const res = await api.getLicenseStatus()
    licValid.value = !!res.valid
    licState.value = res.state || ''
    licInfo.value = res.license || null
  } catch {
    licState.value = 'offline'
    licValid.value = false
  } finally {
    licChecking.value = false
  }
}

async function pickLicenseFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  importErr.value = ''
  importMsg.value = ''
  try {
    const text = await file.text()
    importing.value = true
    const res = await api.importLicense(text)
    if (res.success) {
      importMsg.value = '✅ ' + (res.message || 'License 导入成功')
      await checkLicense()
    } else {
      importErr.value = '❌ ' + (res.message || 'License 导入失败')
    }
  } catch (err) {
    importErr.value = '❌ 导入失败：' + err.message
  } finally {
    importing.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function handleLogin() {
  const name = username.value.trim()
  if (!name) {
    error.value = '请输入用户名'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await api.login(name)
    if (res.success) {
      localStorage.setItem('ate_username', res.username)
      router.push('/execute')
    } else if (res.license_required) {
      licValid.value = false
      licState.value = res.license?.state || 'invalid'
      error.value = res.message || '未授权'
    } else {
      error.value = res.message || '登录失败'
    }
  } catch {
    error.value = '无法连接后端服务，请先启动后端 (python -m uvicorn main:app --port 8000)'
  } finally {
    loading.value = false
  }
}

function fmtDate(s) {
  if (!s) return '-'
  const d = new Date(s)
  return isNaN(d) ? s : d.toLocaleString('zh-CN', { hour12: false })
}

onMounted(checkLicense)
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <!-- 工业风艺术字 -->
      <div class="hero-title">
        <span class="ht-main">ATE RUNNER</span>
        <span class="ht-sub">自动测试装备 · 测试程序集执行平台</span>
      </div>

      <!-- 检查中 -->
      <div v-if="licChecking" class="state-box">
        <p class="state-loading">正在检测授权状态…</p>
      </div>

      <!-- 后端离线 -->
      <div v-else-if="licState === 'offline'" class="state-box">
        <p class="state-err">无法连接后端服务</p>
        <p class="state-desc">请确认后端已启动：<code>python -m uvicorn main:app --port 8000</code></p>
        <button class="btn primary block" @click="checkLicense">重新检测</button>
      </div>

      <!-- 未授权: 注册导入页 -->
      <div v-else-if="!licValid" class="state-box">
        <div class="state-icon">🔒</div>
        <p class="state-title">软件未授权</p>
        <p class="state-desc">
          当前设备未检测到有效 License。<br />
          请选择本机 <b>License 授权文件（.lic）</b> 导入后继续使用。
        </p>
        <button class="btn primary block" :disabled="importing" @click="fileInput?.click()">
          {{ importing ? '导入中…' : '📂 选择 License 文件导入' }}
        </button>
        <input ref="fileInput" type="file" accept=".lic,application/json,text/plain" class="hidden-input" @change="pickLicenseFile" />
        <p v-if="importMsg" class="ok-msg">{{ importMsg }}</p>
        <p v-if="importErr" class="form-error">{{ importErr }}</p>
        <p class="state-tip">提示：授权文件由供应商签发，存放在部署目录（如 D:\ATE_ENV\ate_runner.lic）</p>
      </div>

      <!-- 已授权: 登录表单 -->
      <form v-else class="login-form" @submit.prevent="handleLogin">
        <div class="lic-banner">
          <span class="lic-dot"></span>
          <span>已授权 · {{ licInfo?.customer || '-' }}</span>
          <span class="lic-exp">有效期至 {{ fmtDate(licInfo?.expires_at) }}</span>
        </div>
        <label class="field-label" for="username">用户名</label>
        <input
          id="username"
          v-model="username"
          class="input"
          type="text"
          placeholder="请输入用户名"
          autocomplete="username"
          autofocus
        />
        <p v-if="error" class="form-error">{{ error }}</p>
        <button class="btn primary block" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    radial-gradient(1200px 500px at 50% -10%, rgba(var(--accent-rgb), 0.08), transparent),
    var(--bg);
}
.login-card {
  width: 100%;
  max-width: 420px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 36px 34px 32px;
  box-shadow: 0 20px 60px rgba(76, 29, 149, 0.16);
}

/* 工业风艺术字 */
.hero-title {
  text-align: center;
  margin-bottom: 26px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
  position: relative;
}
.hero-title::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: -2px;
  transform: translateX(-50%);
  width: 64px;
  height: 3px;
  background: linear-gradient(90deg, transparent, var(--accent), #7c3aed, transparent);
}
.ht-main {
  display: block;
  font-size: 42px;
  font-weight: 900;
  letter-spacing: 6px;
  line-height: 1.1;
  background: linear-gradient(180deg, #ddd6fe 0%, #a78bfa 32%, #6d28d9 62%, #8b5cf6 80%, #a78bfa 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  text-shadow: 0 0 28px rgba(var(--accent-2-rgb), 0.35);
  filter: drop-shadow(0 2px 6px rgba(76, 29, 149, 0.18));
  font-family: 'Arial Black', 'Segoe UI', 'Microsoft YaHei', sans-serif;
}
.ht-sub {
  display: block;
  margin-top: 8px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 3px;
}

/* 状态区 */
.state-box { text-align: center; padding: 10px 0 4px; }
.state-icon { font-size: 40px; margin-bottom: 10px; }
.state-title { font-size: 17px; font-weight: 700; margin: 0 0 8px; }
.state-desc { color: var(--muted); font-size: 13px; line-height: 1.7; margin: 0 0 18px; }
.state-tip { color: var(--muted); font-size: 12px; margin: 16px 0 0; line-height: 1.6; }
.state-loading { color: var(--muted); font-size: 13px; padding: 30px 0; }
.state-err { color: var(--err); font-weight: 600; margin: 0 0 6px; }
.hidden-input { display: none; }
.ok-msg {
  margin: 14px 0 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(21, 128, 61, 0.1);
  border: 1px solid rgba(21, 128, 61, 0.35);
  color: var(--ok);
  font-size: 13px;
}

/* 登录表单 */
.login-form { display: flex; flex-direction: column; gap: 0; }
.lic-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(21, 128, 61, 0.08);
  border: 1px solid rgba(21, 128, 61, 0.3);
  color: var(--ok);
  font-size: 12px;
  margin-bottom: 20px;
}
.lic-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 8px var(--ok);
  animation: blink 1.6s infinite;
}
@keyframes blink { 50% { opacity: 0.4; } }
.lic-exp { margin-left: auto; color: var(--muted); }
.field-label { display: block; margin-bottom: 8px; color: var(--muted); font-size: 13px; }
.form-error { color: var(--err); font-size: 13px; margin: 10px 0 0; }

.btn.primary {
  margin-top: 18px;
}
code {
  background: var(--panel-2);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--accent-2);
}
</style>
