import { useState, type FormEvent } from 'react';
import { apiFetch } from '../api';

export default function RequestAccessPage() {
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await apiFetch('/api/signup-requests', {
        method: 'POST',
        body: JSON.stringify({ email, display_name: displayName, reason }),
        suppressUnauthorizedRedirect: true,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Request failed');
        return;
      }
      setSuccess(true);
    } catch {
      setError('Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
        <div className="w-full max-w-sm text-center">
          <div className="mb-2 flex items-center justify-center gap-3 text-indigo-400">
            <img src="/lessonlens-favicon.svg" alt="" aria-hidden="true" className="h-10 w-10 rounded-lg" />
            <h1 className="text-3xl font-bold">LessonLens</h1>
          </div>
          <div className="bg-gray-900 rounded-xl p-6 border border-gray-800 mt-8">
            <div className="text-green-400 text-lg font-medium mb-2">Request Submitted</div>
            <p className="text-gray-400 text-sm">
              Your access request has been submitted. You'll receive an invitation when it's reviewed.
            </p>
            <a href="/login" className="inline-block mt-4 text-indigo-400 hover:text-indigo-300 text-sm">
              ← Back to login
            </a>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-2 flex items-center justify-center gap-3 text-indigo-400">
          <img src="/lessonlens-favicon.svg" alt="" aria-hidden="true" className="h-10 w-10 rounded-lg" />
          <h1 className="text-3xl font-bold">LessonLens</h1>
        </div>
        <p className="text-center text-gray-400 mb-8">Request access</p>

        <form onSubmit={handleSubmit} className="bg-gray-900 rounded-xl p-6 border border-gray-800 space-y-4">
          {error && (
            <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm text-gray-400 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
              required
            />
          </div>

          <div>
            <label className="block text-sm text-gray-400 mb-1">Display Name</label>
            <input
              type="text"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div>
            <label className="block text-sm text-gray-400 mb-1">Why do you want access?</label>
            <textarea
              value={reason}
              onChange={e => setReason(e.target.value)}
              rows={3}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500 resize-none"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-lg py-2 font-medium transition-colors"
          >
            {loading ? 'Submitting...' : 'Request Access'}
          </button>

          <div className="text-center">
            <a href="/login" className="text-indigo-400 hover:text-indigo-300 text-sm">
              ← Back to login
            </a>
          </div>
        </form>
      </div>
    </div>
  );
}
