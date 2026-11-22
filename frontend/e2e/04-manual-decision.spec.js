import { test, expect } from '@playwright/test'
import { seedLogin } from './auth.setup.js'

/**
 * 人工确认弹窗（自备数据，顺序无关）
 *
 * 覆盖：导入一个只含 1 条 manual 步骤的 TPS → 在页面上选中并执行 →
 *      弹出 SOP 弹窗 → 判定失败并填备注 → 该步记失败、备注入库 → 本轮以「执行失败」收尾。
 *
 * 说明：不依赖内置 TPS，也不依赖其它 spec 的执行顺序；
 *      若当前环境无法离线导入（Controller 在线且拒绝），用例会给出明确跳过原因。
 */

const TPS_ID = 'e2e-manual-01'

function manualTpsZipBase64() {
  const tps = {
    schema: 'tps.v2',
    id: TPS_ID,
    name: 'E2E 人工判定 TPS',
    version: '1.0.0',
    testconfig: { TC_A: { label: 'A', min: 0, max: 10, unit: 'V' } },
    device_config: { psu: { role: 'psu' } },
    setup: [],
    cmd_suit: [
      { id: 'M1', name: '人工外观确认', type: 'manual', resource: 'resource/M1.md', timeout_s: 120 },
    ],
    teardown: [],
  }
  const sop = '# 请逐项确认\n\n1. 外壳无划痕\n2. 标签清晰\n'
  const py = 'def main(ctx, **params):\n    return {}\n'

  // 手工拼接 zip（store 模式，不压缩），避免引入额外依赖
  const enc = new TextEncoder()
  const files = [
    ['tps.json', enc.encode(JSON.stringify(tps))],
    ['resource/M1.md', enc.encode(sop)],
    ['testcase/impl.py', enc.encode(py)],
  ]

  const crcTable = (() => {
    const table = new Uint32Array(256)
    for (let n = 0; n < 256; n += 1) {
      let c = n
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
      table[n] = c >>> 0
    }
    return table
  })()

  const crc32 = (bytes) => {
    let c = 0xffffffff
    for (const b of bytes) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8)
    return (c ^ 0xffffffff) >>> 0
  }

  const chunks = []
  const central = []
  let offset = 0

  const u16 = (v) => [v & 0xff, (v >>> 8) & 0xff]
  const u32 = (v) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff]

  for (const [name, data] of files) {
    const nameBytes = enc.encode(name)
    const crc = crc32(data)
    const local = [
      ...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc), ...u32(data.length), ...u32(data.length),
      ...u16(nameBytes.length), ...u16(0), ...nameBytes,
    ]
    chunks.push(new Uint8Array(local), data)
    central.push([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc), ...u32(data.length), ...u32(data.length),
      ...u16(nameBytes.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0),
      ...u32(offset), ...nameBytes,
    ])
    offset += local.length + data.length
  }

  const centralBytes = central.flat()
  const end = [
    ...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length),
    ...u32(centralBytes.length), ...u32(offset), ...u16(0),
  ]

  const total = chunks.reduce((n, c) => n + c.length, 0) + centralBytes.length + end.length
  const out = new Uint8Array(total)
  let p = 0
  for (const c of chunks) {
    out.set(c, p)
    p += c.length
  }
  out.set(new Uint8Array(centralBytes), p)
  p += centralBytes.length
  out.set(new Uint8Array(end), p)

  let binary = ''
  for (const b of out) binary += String.fromCharCode(b)
  return btoa(binary)
}

test.describe('人工确认弹窗', () => {
  test('判定失败并带备注，备注写入步骤结果，本轮收尾为执行失败', async ({ page, request }) => {
    // 1) 先导入一个只含 manual 步骤的 TPS（离线时落到本机，同样可用）
    const imported = await request.post('/api/tps/import/zip', {
      data: { name: 'e2e-manual.zip', content_base64: manualTpsZipBase64(), overwrite: true },
    })
    const body = await imported.json().catch(() => ({}))
    test.skip(!body.success, `导入失败，跳过：${body.message || imported.status()}`)

    await seedLogin(page)
    await page.goto('/execute')

    // 2) 选中刚导入的 TPS：按可见性决定是否展开，不假设初始折叠状态
    const tree = page.getByTestId('equipment-tree')
    await expect(tree).toBeVisible()
    const tpsNode = tree.locator('.tree-node.tps', { hasText: 'E2E 人工判定 TPS' }).first()
    const isOpen = async () => tpsNode.isVisible().catch(() => false)

    if (!(await isOpen())) {
      await tree.locator('.tree-row', { hasText: '未分组项目' }).first().click()
    }
    if (!(await isOpen())) {
      await tree.locator('.tree-row', { hasText: '未分组 DUT' }).first().click()
    }
    await expect(tpsNode).toBeVisible({ timeout: 15_000 })
    await tpsNode.click()

    // 3) 填 UUT 并执行（调试模式 + 仿真）
    await page.getByPlaceholder('12 位英文与数字，如 ABC123DEF456').fill('E2E000000001')
    await page.getByRole('button', { name: /^执行 TPS/ }).click()

    // 4) 人工确认弹窗出现（SOP 来自 resource/M1.md）
    const card = page.getByTestId('manual-card')
    await expect(card).toBeVisible({ timeout: 45_000 })
    await expect(card.getByText('人工确认', { exact: true })).toBeVisible()
    await expect(card.locator('.manual-title')).toHaveText('人工外观确认')
    await expect(card.getByRole('heading', { name: '请逐项确认' })).toBeVisible()

    // 5) 判定失败 + 备注
    const note = 'e2e 自动化：外观确认不通过'
    await card.getByRole('button', { name: '判定失败', exact: true }).click()
    const noteInput = page.locator('#manual-fail-note')
    await expect(noteInput).toBeVisible()
    await noteInput.fill(note)
    await card.getByRole('button', { name: '确认判定失败' }).click()

    // 6) 弹窗关闭，步骤结果含判定失败与备注
    await expect(card).toBeHidden({ timeout: 45_000 })
    const manualStep = page.locator('.step-list .step-item').filter({ hasText: '人工外观确认' }).first()
    await expect(manualStep).toContainText('操作员判定失败', { timeout: 45_000 })
    await expect(manualStep).toContainText(note)

    // 7) 本轮收尾为执行失败
    await expect(page.locator('.task-status-row .badge')).toHaveText('执行失败', { timeout: 45_000 })
  })
})
