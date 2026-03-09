const API_BASE = import.meta.env.VITE_API_BASE ?? '';
const PREVIEW_MODE = import.meta.env.VITE_PREVIEW_MODE === 'true';

type ApiRequestOptions = RequestInit & {
  suppressUnauthorizedRedirect?: boolean;
};

export async function apiFetch(path: string, options: ApiRequestOptions = {}) {
  const { suppressUnauthorizedRedirect = false, ...requestOptions } = options;
  const token = localStorage.getItem('token');
  const headers: Record<string, string> = {
    ...(requestOptions.headers as Record<string, string> || {}),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  if (PREVIEW_MODE) {
    headers['X-Preview-Mode'] = 'true';
  }
  // Don't set Content-Type for FormData (file uploads)
  if (!(requestOptions.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(`${API_BASE}${path}`, { ...requestOptions, headers });

  // Keep login failures inline on the login page instead of forcing a hard redirect.
  if (res.status === 401 && !suppressUnauthorizedRedirect) {
    localStorage.removeItem('token');
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }

  return res;
}

export async function apiJson<T = unknown>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const res = await apiFetch(path, options);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Request failed: ${res.status}`);
  }
  return res.json();
}

export function trackEvent(eventType: string, eventData: Record<string, unknown> = {}) {
  apiFetch('/api/analytics/event', {
    method: 'POST',
    body: JSON.stringify({ event_type: eventType, event_data: eventData }),
  }).catch(() => {}); // fire-and-forget
}
