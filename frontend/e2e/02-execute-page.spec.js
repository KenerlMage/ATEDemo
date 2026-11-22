import { test, expect } from '@playwright/test'
import { seedLogin } from './auth.setup.js'

/**
 * 测试执行页核心区
 * 覆盖：装备树容器、执行 TPS 按钮、分级日志计数、测试报告区
 */
test.describe('测试执行页', () => {
  test.beforeEach(async ({ page }) => {
    await seedLogin(page)
    await page.goto('/execute')
  })

  test('装备树 / 执行 TPS 按钮 / 日志计数 / 测试报告区均渲染', async ({ page }) => {
    // 装备树：容器 + 标题 + 至少一个 TPS 节点（离线时来自 backend/testresource）
    const tree = page.getByTestId('equipment-tree')
    await expect(tree).toBeVisible()
    await expect(tree.getByText('装备树', { exact: true })).toBeVisible()
    await expect(tree.locator('.tree-node.tps').first()).toBeVisible()

    // 执行按钮：默认选中首个 TPS 并加载步骤后可用
    const runButton = page.getByRole('button', { name: /^执行 TPS/ })
    await expect(runButton).toBeVisible()
    await expect(runButton).toBeEnabled()

    // 日志区：ERROR / WARNING / INFO 三个分级计数
    await expect(page.getByTestId('log-count-error')).toHaveText(/^ERROR \d+$/)
    await expect(page.getByTestId('log-count-warning')).toHaveText(/^WARNING \d+$/)
    await expect(page.getByTestId('log-count-info')).toHaveText(/^INFO \d+$/)

    // 测试报告区
    const report = page.getByTestId('report-panel')
    await expect(report).toBeVisible()
    await expect(report.getByText('测试报告', { exact: true })).toBeVisible()
  })
})
