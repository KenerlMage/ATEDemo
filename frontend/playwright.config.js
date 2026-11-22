import { defineConfig } from '@playwright/test'

/**
 * ATE Runner 端到端测试配置
 *
 * - 只使用系统已安装的 Microsoft Edge（channel: 'msedge'），不下载 Playwright 自带浏览器
 * - 被测服务由外部启动（后端 8020 端口托管 frontend/dist），地址可用 ATE_RUNNER_URL 覆盖
 * - 串行执行：Runner 的 TPS 任务、记录库与 workspace 都是单实例状态，并行会互相干扰
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.ATE_RUNNER_URL || 'http://127.0.0.1:8020',
    channel: 'msedge',
    headless: true,
    viewport: { width: 1600, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  }
})
