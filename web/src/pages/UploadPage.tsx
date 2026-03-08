import { useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { apiFetch } from '../api';
import type { ParseResult } from '../types';

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<ParseResult | null>(null);
  const [error, setError] = useState('');

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer.files[0];
    if (f) { setFile(f); setResult(null); setError(''); }
  }, []);

  const handleSync = async () => {
    if (!file) return;
    setError('');
    setSyncing(true);
    setResult(null);

    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await apiFetch('/api/sync', {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || 'Sync failed');
      }
      setResult(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setSyncing(false);
    }
  };

  const handleReset = () => {
    setFile(null);
    setResult(null);
    setError('');
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Sync Chat Export</h1>
      <p className="text-gray-400">
        Drop your LINE chat export (.txt file) and hit Sync. The system will upload,
        parse, and index all your lesson sessions in one step.
      </p>

      {/* Drop zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragActive(true); }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-xl p-12 text-center transition-colors ${
          dragActive
            ? 'border-indigo-500 bg-indigo-950/30'
            : 'border-gray-700 hover:border-gray-600'
        }`}
      >
        <div className="text-4xl mb-3">📁</div>
        <p className="text-gray-300 mb-2">
          {file ? file.name : 'Drag & drop your chat export here'}
        </p>
        {file ? (
          <p className="text-sm text-gray-500">{(file.size / 1024).toFixed(1)} KB</p>
        ) : (
          <>
            <p className="text-sm text-gray-500 mb-3">or</p>
            <label className="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded-lg cursor-pointer transition-colors">
              Browse Files
              <input
                type="file"
                accept=".txt,text/plain"
                className="hidden"
                onChange={e => { if (e.target.files?.[0]) { setFile(e.target.files[0]); setResult(null); setError(''); } }}
              />
            </label>
          </>
        )}
      </div>

      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">
          {error}
        </div>
      )}

      {file && !result && (
        <button
          onClick={handleSync}
          disabled={syncing}
          className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-lg py-3 font-medium transition-colors"
        >
          {syncing ? '🔄 Syncing...' : '🚀 Sync File'}
        </button>
      )}

      {/* Results */}
      {result && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-3">
          <h2 className="text-lg font-semibold text-green-400">✅ Parse Complete</h2>
          {result.duplicate && (
            <p className="text-yellow-400 text-sm">This file was already parsed.</p>
          )}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-gray-400">Sessions:</span>{' '}
              <span className="font-medium">{result.session_count}</span>
            </div>
            <div>
              <span className="text-gray-400">Messages:</span>{' '}
              <span className="font-medium">{result.message_count}</span>
            </div>
            <div>
              <span className="text-gray-400">Lesson content:</span>{' '}
              <span className="font-medium">{result.lesson_content_count}</span>
            </div>
            <div>
              <span className="text-gray-400">Warnings:</span>{' '}
              <span className="font-medium">{result.warnings}</span>
            </div>
          </div>
          <div className="flex gap-3 mt-3">
            <Link
              to="/sessions"
              className="text-indigo-400 hover:text-indigo-300"
            >
              Browse sessions →
            </Link>
            <button
              onClick={handleSync}
              disabled={syncing}
              className="text-gray-400 hover:text-white text-sm"
            >
              {syncing ? 'Syncing...' : '🔄 Re-sync'}
            </button>
            <button
              onClick={handleReset}
              className="text-gray-400 hover:text-white text-sm"
            >
              Upload different file
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
