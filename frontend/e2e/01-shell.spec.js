import { test, expect } from '@playwright/test'
import { seedLogin, TEST_USER } from './auth.setup.js'

/**
 * 外壳与导航
 * 覆盖：左侧 6 个导航入口、点击跳转、当前登录用户名展示
 */
test.describe('应用外壳', () => {
  test.beforeEach(async ({ page }) => {
    await seedLogin(page)
    await page.goto('/execute')
    // 登录态生效：外壳底部用户标识为注入的用户名
    await expect(page.getByTestId('current-user')).toHaveText(TEST_USER)
  })

  test('左侧导航 6 个入口可见，点击「测试记录」进入记录页', async ({ page }) => {
    const nav = page.locator('nav.side-nav')
    await expect(nav.getByRole('link')).toHaveCount(6)

    for (const name of ['测试执行', '装备属性配置', '装备助手', '测试记录', '系统设置', '测试台导航']) {
      await expect(nav.getByRole('link', { name: new RegExp(name) })).toBeVisible()
    }

    await nav.getByRole('link', { name: /测试记录/ }).click()
    await expect(page).toHaveURL(/\/records$/)
    await expect(page.getByText('🗂 历史测试记录')).toBeVisible()
    // 跳转后仍处于登录态
    await expect(page.getByTestId('current-user')).toHaveText(TEST_USER)
  })
})
