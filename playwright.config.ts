import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
  reporter: [['html', { open: 'never' }], ['list']],
  webServer: [
    {
      command: 'cd api && ../.venv/bin/python -c "from app import app; app.run(port=5001)"',
      port: 5001,
      reuseExistingServer: true,
      timeout: 15_000,
    },
    {
      command: 'cd web && npx vite --port 5173',
      port: 5173,
      reuseExistingServer: true,
      timeout: 15_000,
    },
  ],
})
