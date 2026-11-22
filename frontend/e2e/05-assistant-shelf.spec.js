import { test, expect } from '@playwright/test'
import { seedLogin } from './auth.setup.js'

/**
 * 装备助手货架
 * 覆盖：从装备助手页进入货架、清单来源 chip 三态之一、卡片或空态渲染、无 JS 运行时报错
 */
test.describe('装备助手货架', () => {
  test('从装备助手进入货架，来源 chip 可见且页面无 JS 报错', async ({ page }) => {
    const pageErrors = []
    page.on('pageerror', (err) => pageErrors.push(String(err)))

    await seedLogin(page)
    await page.goto('/assistant')

    await page.getByRole('button', { name: /装备货架/ }).click()
    await expect(page).toHaveURL(/\/assistant\/shelf$/)
    await expect(page.locator('.hd-title')).toHaveText('装备货架')

    // 清单来源：MockController 下发 / 本地缓存 / 本地兜底清单
    const chip = page.getByTestId('shelf-source-chip')
    await expect(chip).toBeVisible()
    await expect(chip).toHaveText(/MockController 下发|本地缓存|本地兜底清单/)

    await expect(page.getByText('正在读取货架清单…')).toBeHidden()
    const cards = page.getByTestId('assistant-card')
    if ((await cards.count()) > 0) {
      await expect(cards.first()).toBeVisible()
    } else {
      await expect(page.getByText('没有匹配的助手。')).toBeVisible()
    }

    expect(pageErrors, `页面出现 JS 报错：\n${pageErrors.join('\n')}`).toEqual([])
  })
})
