import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { seedLogin } from './auth.setup.js'

/**
 * TPS 导入入口
 * 覆盖：导入按钮唤出文件选择、accept 限制、上传合法 tps.v2 清单后的成功提示与树内定位
 *
 * 离线（Controller 不可达）时后端走本地导入，提示「已导入本地（仅本机可见）」。
 * 用例使用固定 id，便于重复运行时覆盖同一条 TPS。
 */
const TPS_ID = 'e2e-import-smoke'
const TPS_NAME = 'E2E 导入冒烟 TPS'
const HERE = path.dirname(fileURLToPath(import.meta.url))
// 本机后端把导入的 TPS 落到 backend/testresource/<id>/
const LOCAL_TPS_DIR = path.resolve(HERE, '..', '..', 'backend', 'testresource', TPS_ID)

function manifest() {
  return {
    schema: 'tps.v2',
    id: TPS_ID,
    name: TPS_NAME,
    version: '1.0.0',
    description: 'Playwright 导入用例生成的临时 TPS（每次运行覆盖同一条）',
    testconfig: {
      TC_MAIN: { label: '主判据', min: 0, max: 1, unit: '' }
    },
    device_config: {
      psu: { device_id: 'e2e-psu', role: 'psu' }
    },
    cmd_suit: [
      {
        id: 'TC001',
        name: '导入冒烟用例',
        case: 'testcase.e2e_smoke::main',
        checks: { value: 'TC_MAIN' }
      }
    ]
  }
}

test.describe('TPS 导入', () => {
  test.afterAll(() => {
    // 清掉本机后端留下的临时 TPS，保持工作树干净；远端服务（ATE_RUNNER_URL）时路径不存在，忽略
    try {
      const manifestPath = path.join(LOCAL_TPS_DIR, 'tps.json')
      if (!fs.existsSync(manifestPath)) return
      const saved = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'))
      if (saved && saved.id === TPS_ID) fs.rmSync(LOCAL_TPS_DIR, { recursive: true, force: true })
    } catch {
      /* 清理失败不影响用例结论 */
    }
  })

  test('点击「导入 TPS」唤出文件选择，上传合法 tps.v2 清单后提示成功', async ({ page }) => {
    await seedLogin(page)
    await page.goto('/execute')

    const input = page.getByTestId('tps-file-input')
    await expect(input).toHaveAttribute('accept', /\.json/)
    await expect(input).toHaveAttribute('accept', /\.zip/)

    // 点击按钮应唤出文件选择框（input[type=file] 被隐藏，由按钮代理点击）
    const chooserPromise = page.waitForEvent('filechooser')
    await page.getByRole('button', { name: /导入 TPS/ }).click()
    const chooser = await chooserPromise
    expect(chooser.isMultiple()).toBe(false)

    const tmpFile = path.join(os.tmpdir(), `ate-e2e-tps-${Date.now()}.json`)
    fs.writeFileSync(tmpFile, JSON.stringify(manifest(), null, 2), 'utf-8')
    try {
      await chooser.setFiles(tmpFile)

      // 成功提示（离线时文案为「Controller 不可达，已导入本地（仅本机可见）」）
      await expect(page.getByText(/^✅ /)).toBeVisible({ timeout: 30_000 })
      // 导入后装备树刷新并定位到新 TPS
      await expect(page.getByTestId('equipment-tree').getByText(TPS_NAME)).toBeVisible({
        timeout: 30_000
      })
    } finally {
      fs.rmSync(tmpFile, { force: true })
    }
  })
})
