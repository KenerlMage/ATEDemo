import { test, expect } from '@playwright/test'
import { seedLogin } from './auth.setup.js'

/**
 * 07 人工确认与标定标识（SR02-AR04 / SR02-AR05）
 *
 * 覆盖：含 manual_check + calib 的 TPS 在页面上出现「标定」徽标与标定模式条；
 *      步骤行出现「确认回传」按钮；确认后显示已回传。
 * 前置：后端 testresource/mc-check-demo 存在（随仓库提供），离线模式亦可。
 */

const TPS_ID = 'mc-check-demo'
const TPS = {
  schema: 'tps.v2',
  id: TPS_ID,
  name: '人工确认与标定联调 TPS',
  version: '1.0.0',
  testconfig: { ANGLE: { min: 0, max: 360, unit: 'deg', label: '角度' } },
  device_config: { psu: { role: 'psu' } },
  setup: [],
  cmd_suit: [
    {
      id: 'C1',
      no: 1,
      name: '标定采集',
      type: 'calib',
      case: 'testcase.calib::run',
      description: '采集标定值',
      manual_check: { role: 'engineer', fields: ['ANGLE'] }
    },
    { id: 'C2', no: 2, name: '普通测试', case: 'testcase.basic::run', description: '普通用例' }
  ],
  teardown: []
}

test.describe('人工确认与标定标识', () => {
  test('标定步骤带徽标，manual_check 步骤出现确认按钮', async ({ page, request }) => {
    // 1) 确保 Demo TPS 存在（zip 内含 tps.json 与 testcase 实现）
    const zipped = await request.post('/api/tps/import', {
      data: {
        content: JSON.stringify(TPS),
        overwrite: true
      }
    })
    const body = await zipped.json().catch(() => ({}))
    test.skip(!body.success, `Demo TPS 导入失败：${body.message || zipped.status()}`)

    await seedLogin(page)
    await page.goto('/execute')

    // 2) 展开树并选中（按可见性展开，不假设初始状态）
    const tree = page.getByTestId('equipment-tree')
    const node = tree.locator('.tree-node.tps', { hasText: TPS.name }).first()
    const visible = async () => node.isVisible().catch(() => false)
    if (!(await visible())) await tree.locator('.tree-row', { hasText: '未分组项目' }).first().click()
    if (!(await visible())) await tree.locator('.tree-row', { hasText: '未分组 DUT' }).first().click()
    await expect(node).toBeVisible({ timeout: 15_000 })
    await node.click()

    // 3) 标定标识：步骤徽标 + 顶部标定模式条
    await expect(page.getByTestId('step-calib-badge').first()).toBeVisible()
    await expect(page.getByTestId('calib-bar')).toBeVisible()
    await expect(page.getByTestId('calib-bar')).toContainText('标定')

    // 4) 执行：manual_check 的确认按钮在该步执行完成后出现（未执行时不显示，避免误确认）
    await page.getByPlaceholder('12 位英文与数字，如 ABC123DEF456').fill('E2E000000003')
    await page.getByRole('button', { name: /^执行 TPS/ }).click()

    const row = page.getByTestId('manual-check-row').first()
    await expect(row).toContainText('待人工确认', { timeout: 45_000 })

    // 5) 确认回传
    const confirmBtn = page.getByTestId('manual-check-confirm').first()
    await expect(confirmBtn).toBeVisible({ timeout: 45_000 })
    await confirmBtn.click()
    await expect(page.getByTestId('manual-check-row').first()).toContainText('已人工确认并回传', {
      timeout: 45_000
    })
  })
})
