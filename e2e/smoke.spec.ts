import { test, expect } from '@playwright/test'

test.describe('Smoke tests', () => {
  test('health endpoint returns ok', async ({ request }) => {
    const res = await request.get('/api/health')
    expect(res.ok()).toBe(true)
    const body = await res.json()
    expect(body.status).toBe('ok')
  })

  test('login page loads', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByText('LessonLens')).toBeVisible()
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible()
  })

  test('unauthenticated redirect to login', async ({ page }) => {
    await page.goto('/')
    // Should either show login page or redirect to /login
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible({ timeout: 5000 })
  })

  test('login form accepts input and submits', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input[type="email"]').fill('wrong@email.com')
    await page.locator('input[type="password"]').fill('wrongpassword1234')
    await page.getByRole('button', { name: /sign in/i }).click()
    // After failed login, user stays on login page (error shown or redirect back)
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible({ timeout: 5000 })
  })
})
