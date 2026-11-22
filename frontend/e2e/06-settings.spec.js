import { test, expect } from '@playwright/test'
import { seedLogin } from './auth.setup.js'

/**
 * 系统设置页
 * 覆盖：页面正常渲染权限矩阵、无 JS 运行时报错
 */
test.describe('系统设置', () => {
  test('系统设置页渲染权限矩阵且无 JS 报错', async ({ page }) => {
    const pageErrors = []
    page.on('pageerror', (err) => pageErrors.push(String(err)))

    await seedLogin(page)
    await page.goto('/settings')

    await expect(page.locator('.set-title')).toHaveText('系统设置')
    await expect(page.locator('.pm-title').filter({ hasText: '权限矩阵' })).toBeVisible()

    expect(pageErrors, `页面出现 JS 报错：\n${pageErrors.join('\n')}`).toEqual([])
  })
})
