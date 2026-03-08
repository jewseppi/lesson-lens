import { useState } from 'react';
import { apiFetch, apiJson } from '../api';

type Provider = 'openai' | 'anthropic' | 'gemini';

const STORAGE_KEY = 'lessonlens-provider';

function getStoredProvider(): Provider {
  const value = localStorage.getItem(STORAGE_KEY);
  return value === 'anthropic' || value === 'gemini' ? value : 'openai';
}

export default function SettingsPage() {
  const [provider, setProvider] = useState<Provider>(getStoredProvider);
  const [sessionId, setSessionId] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [importing, setImporting] = useState(false);
  const [bulkGenerating, setBulkGenerating] = useState(false);
  const [bulkStatus, setBulkStatus] = useState('');
  const [bulkError, setBulkError] = useState('');

  const saveProvider = (next: Provider) => {
    setProvider(next);
    localStorage.setItem(STORAGE_KEY, next);
    setStatus(`Default provider saved: ${next}`);
    setError('');
  };

  const handleImport = async (file: File | null) => {
    if (!file || !sessionId.trim()) {
      setError('Select a lesson-data.json file and enter a session ID first.');
      return;
    }

    setImporting(true);
    setError('');
    setStatus('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('provider', 'uploaded-summary');
      formData.append('model', 'external-agent');
      const res = await apiFetch(`/api/sessions/${sessionId.trim()}/summary/import`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Import failed');
      }
      setStatus(`Imported summary package for ${sessionId.trim()}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  };

  const handleGenerateMissing = async () => {
    setBulkGenerating(true);
    setBulkStatus('');
    setBulkError('');
    try {
      const result = await apiJson<{
        generated_count: number;
        failed_count: number;
        skipped_existing_count: number;
      }>('/api/summaries/generate', {
        method: 'POST',
        body: JSON.stringify({
          provider,
          min_lesson_content_count: 3,
        }),
      });

      setBulkStatus(
        `Generated ${result.generated_count} summaries, ${result.failed_count} failed, ${result.skipped_existing_count} already existed.`
      );
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : 'Bulk generation failed');
    } finally {
      setBulkGenerating(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-gray-400 mt-1">Provider preference and advanced summary import tools.</p>
      </div>

      <section className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-white">Default AI Provider</h2>
          <p className="text-sm text-gray-400 mt-1">
            This provider will be used by default when generating a summary from the session page.
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <button
            onClick={() => saveProvider('openai')}
            className={`py-2 rounded-lg text-sm font-medium transition-colors border ${
              provider === 'openai'
                ? 'bg-green-900/50 border-green-600 text-green-300'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
            }`}
          >
            OpenAI
          </button>
          <button
            onClick={() => saveProvider('anthropic')}
            className={`py-2 rounded-lg text-sm font-medium transition-colors border ${
              provider === 'anthropic'
                ? 'bg-orange-900/50 border-orange-600 text-orange-300'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
            }`}
          >
            Claude
          </button>
          <button
            onClick={() => saveProvider('gemini')}
            className={`py-2 rounded-lg text-sm font-medium transition-colors border ${
              provider === 'gemini'
                ? 'bg-blue-900/50 border-blue-600 text-blue-300'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
            }`}
          >
            Gemini
          </button>
        </div>
      </section>

      <section className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-white">Generate Missing Summaries</h2>
          <p className="text-sm text-gray-400 mt-1">
            Generate summaries for every parsed session with at least 3 lesson-content messages that does not already have one.
          </p>
        </div>

        <button
          onClick={handleGenerateMissing}
          disabled={bulkGenerating}
          className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
        >
          {bulkGenerating ? 'Generating missing summaries...' : 'Generate All Missing Summaries'}
        </button>

        <p className="text-xs text-gray-500">
          Uses the current default provider: <span className="text-gray-300">{provider}</span>.
        </p>

        {bulkStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{bulkStatus}</div>}
        {bulkError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{bulkError}</div>}
      </section>

      <section className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-white">Import Summary Package</h2>
          <p className="text-sm text-gray-400 mt-1">
            Advanced flow: upload a completed <span className="font-mono">lesson-data.json</span> created on another machine or by another agent.
          </p>
        </div>

        <label className="block space-y-2">
          <span className="text-sm text-gray-300">Session ID</span>
          <input
            type="text"
            value={sessionId}
            onChange={e => setSessionId(e.target.value)}
            placeholder="2026-03-05"
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500"
          />
        </label>

        <label className="block">
          <span className="sr-only">Choose lesson-data.json</span>
          <input
            type="file"
            accept="application/json,.json"
            className="block w-full text-sm text-gray-400 file:mr-4 file:rounded-lg file:border-0 file:bg-gray-800 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-gray-700"
            onChange={e => void handleImport(e.target.files?.[0] ?? null)}
            disabled={importing}
          />
        </label>

        {status && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{status}</div>}
        {error && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{error}</div>}
        {importing && <p className="text-xs text-gray-500">Importing summary package...</p>}
      </section>
    </div>
  );
}