/**
 * 登录态注入
 *
 * Runner 的登录页在登录成功后把用户名写入 localStorage `ate_username`，
 * 路由守卫（router/index.js 的 beforeEach）据此放行 /execute 等受保护页面。
 * e2e 不走真实登录接口，直接在页面脚本执行前写入该键，等价于「已登录」。
 */
export const TEST_USER = 'e2e'

/**
 * 在页面任何脚本执行前写入登录态，必须在 page.goto 之前调用。
 * @param {import('@playwright/test').Page} page
 * @param {string} [username]
 */
export async function seedLogin(page, username = TEST_USER) {
  await page.addInitScript((name) => {
    window.localStorage.setItem('ate_username', name)
  }, username)
}
