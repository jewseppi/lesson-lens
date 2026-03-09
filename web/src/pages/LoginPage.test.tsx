/// <reference types="vitest/globals" />

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthProvider } from '../AuthContext'
import LoginPage from '../pages/LoginPage'

function renderWithAuth() {
  return render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>,
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
    // Mock fetch for AuthProvider's profile check
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: 'Unauthorized' }), { status: 401 }),
    )
    // Mock window.location for 401 redirect
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...window.location, href: '' },
    })
  })

  it('renders the login form', () => {
    renderWithAuth()
    expect(screen.getByText('LessonLens')).toBeInTheDocument()
    expect(screen.getByText(/email/i)).toBeInTheDocument()
    expect(screen.getByText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows error on failed login', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: 'Invalid credentials' }), { status: 401 }),
    )

    renderWithAuth()
    const user = userEvent.setup()

    const emailInput = screen.getByRole('textbox')
    const passwordInput = document.querySelector('input[type="password"]')!
    await user.clear(emailInput)
    await user.clear(passwordInput as HTMLElement)
    await user.type(emailInput, 'test@test.com')
    await user.type(passwordInput as HTMLElement, 'wrongpassword')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    // The error message should appear after failed login attempt
    expect(await screen.findByText(/invalid credentials|unauthorized|login failed/i)).toBeInTheDocument()
  })

  it('disables button while loading', async () => {
    // Make fetch hang to keep loading state
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise(() => {}), // never resolves
    )

    renderWithAuth()
    const user = userEvent.setup()

    const emailInput = screen.getByRole('textbox')
    const passwordInput = document.querySelector('input[type="password"]')!
    await user.clear(emailInput)
    await user.clear(passwordInput as HTMLElement)
    await user.type(emailInput, 'test@test.com')
    await user.type(passwordInput as HTMLElement, 'password')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(screen.getByRole('button')).toBeDisabled()
    expect(screen.getByText(/signing in/i)).toBeInTheDocument()
  })
})
