import { useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { apiFetch } from '../api';
import type { ParseResult, AttachmentUploadResult } from '../types';

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<ParseResult | null>(null);
  const [error, setError] = useState('');
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [uploadingImages, setUploadingImages] = useState(false);
  const [imageResults, setImageResults] = useState<AttachmentUploadResult[]>([]);

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
    setImageFiles([]);
    setImageResults([]);
  };

  const handleImageUpload = async () => {
    if (imageFiles.length === 0) return;
    setUploadingImages(true);
    try {
      const formData = new FormData();
      imageFiles.forEach(f => formData.append('images', f));
      const res = await apiFetch('/api/attachments/upload', {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || 'Image upload failed');
      }
      const data = await res.json();
      setImageResults(data.attachments);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Image upload failed');
    } finally {
      setUploadingImages(false);
    }
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

      {/* Image upload section */}
      {result && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-3">
          <h2 className="text-lg font-semibold">📷 Attach Lesson Photos</h2>
          <p className="text-sm text-gray-400">Upload photos from your lessons. They'll be auto-matched to sessions by timestamp.</p>
          <label className="block">
            <input
              type="file"
              accept="image/*"
              multiple
              onChange={e => setImageFiles(Array.from(e.target.files || []))}
              className="block w-full text-sm text-gray-400 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-gray-800 file:text-white hover:file:bg-gray-700 file:cursor-pointer"
            />
          </label>
          {imageFiles.length > 0 && !imageResults.length && (
            <button
              onClick={handleImageUpload}
              disabled={uploadingImages}
              className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              {uploadingImages ? '⏳ Uploading...' : `📤 Upload ${imageFiles.length} image${imageFiles.length > 1 ? 's' : ''}`}
            </button>
          )}
          {imageResults.length > 0 && (
            <div className="space-y-1">
              {imageResults.map((r, i) => (
                <div key={i} className="text-sm flex items-center gap-2">
                  {r.error ? (
                    <span className="text-red-400">❌ {r.filename}: {r.error}</span>
                  ) : r.status === 'duplicate' ? (
                    <span className="text-yellow-400">⚠️ {r.filename}: already uploaded</span>
                  ) : (
                    <span className="text-green-400">
                      ✅ {r.filename}
                      {r.match?.confidence && r.match.confidence !== 'unmatched' && (
                        <span className="ml-1 text-gray-400">→ matched ({r.match.confidence})</span>
                      )}
                      {r.match?.confidence === 'unmatched' && (
                        <span className="ml-1 text-gray-500">— no session match</span>
                      )}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
