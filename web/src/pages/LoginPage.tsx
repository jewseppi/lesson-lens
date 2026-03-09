import { useState, type FormEvent } from 'react';
import { useAuth } from '../AuthContext';

const isDev = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState(isDev ? 'admin@lessonlens.local' : '');
  const [password, setPassword] = useState(isDev ? 'adminpassword1' : '');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-2 flex items-center justify-center gap-3 text-indigo-400">
          <img src="/lessonlens-favicon.svg" alt="" aria-hidden="true" className="h-10 w-10 rounded-lg" />
          <h1 className="text-3xl font-bold">LessonLens</h1>
        </div>
        <p className="text-center text-gray-400 mb-8">Language lesson summarizer</p>

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
            <label className="block text-sm text-gray-400 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
              required
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-lg py-2 font-medium transition-colors"
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}
