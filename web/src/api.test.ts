/// <reference types="vitest/globals" />

import { apiFetch, apiJson, trackEvent } from './api'

describe('apiFetch', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('sends Authorization header when token exists', async () => {
    localStorage.setItem('token', 'test-jwt')
    const mockResponse = new Response(JSON.stringify({ ok: true }), { status: 200 })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse)

    await apiFetch('/api/health')

    expect(fetch).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-jwt' }),
      }),
    )
  })

  it('does not send Authorization header when no token', async () => {
    const mockResponse = new Response('{}', { status: 200 })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse)

    await apiFetch('/api/health')

    const [, opts] = vi.mocked(fetch).mock.calls[0]
    const headers = opts?.headers as Record<string, string>
    expect(headers['Authorization']).toBeUndefined()
  })

  it('sets Content-Type to application/json by default', async () => {
    const mockResponse = new Response('{}', { status: 200 })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse)

    await apiFetch('/api/test')

    const [, opts] = vi.mocked(fetch).mock.calls[0]
    const headers = opts?.headers as Record<string, string>
    expect(headers['Content-Type']).toBe('application/json')
  })

  it('does not set Content-Type for FormData', async () => {
    const mockResponse = new Response('{}', { status: 200 })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse)

    await apiFetch('/api/upload', { method: 'POST', body: new FormData() })

    const [, opts] = vi.mocked(fetch).mock.calls[0]
    const headers = opts?.headers as Record<string, string>
    expect(headers['Content-Type']).toBeUndefined()
  })

  it('clears token and redirects on 401', async () => {
    localStorage.setItem('token', 'expired')
    const mockResponse = new Response('{}', { status: 401 })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse)

    // Mock window.location
    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...originalLocation, href: '' },
    })

    await expect(apiFetch('/api/profile')).rejects.toThrow('Unauthorized')
    expect(localStorage.getItem('token')).toBeNull()

    Object.defineProperty(window, 'location', {
      writable: true,
      value: originalLocation,
    })
  })

  it('does not redirect on 401 when suppressUnauthorizedRedirect is true', async () => {
    localStorage.setItem('token', 'expired')
    const mockResponse = new Response(JSON.stringify({ error: 'Invalid credentials' }), { status: 401 })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse)

    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...originalLocation, href: 'http://localhost:5173/login' },
    })

    const res = await apiFetch('/api/login', { suppressUnauthorizedRedirect: true })
    expect(res.status).toBe(401)
    expect(localStorage.getItem('token')).toBe('expired')

    Object.defineProperty(window, 'location', {
      writable: true,
      value: originalLocation,
    })
  })
})

describe('apiJson', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('returns parsed JSON on success', async () => {
    const data = { status: 'ok', timestamp: '2025-01-01' }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )

    const result = await apiJson('/api/health')
    expect(result).toEqual(data)
  })

  it('throws on non-ok with error message', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: 'Not found' }), { status: 404 }),
    )

    await expect(apiJson('/api/sessions/x')).rejects.toThrow('Not found')
  })

  it('throws generic error when no error field', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('not json', { status: 500 }),
    )

    await expect(apiJson('/api/fail')).rejects.toThrow('Request failed: 500')
  })
})

describe('trackEvent', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('sends POST to analytics endpoint', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 201 }))

    trackEvent('page_view', { page: '/dashboard' })

    expect(fetch).toHaveBeenCalledWith(
      '/api/analytics/event',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
