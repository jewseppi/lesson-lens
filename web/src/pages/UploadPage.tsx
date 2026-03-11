import { useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { apiFetch } from '../api';
import type { ParseResult, AttachmentUploadResult } from '../types';

type SyncResult = {
  filename: string;
  ok: boolean;
  data?: ParseResult;
  error?: string;
};

export default function UploadPage() {
  const [textFiles, setTextFiles] = useState<File[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResults, setSyncResults] = useState<SyncResult[]>([]);
  const [error, setError] = useState('');
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [uploadingImages, setUploadingImages] = useState(false);
  const [imageResults, setImageResults] = useState<AttachmentUploadResult[]>([]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const dropped = Array.from(e.dataTransfer.files || []).filter(f => f.name.toLowerCase().endsWith('.txt'));
    if (dropped.length > 0) {
      setTextFiles(prev => [...prev, ...dropped]);
      setError('');
    }
  }, []);

  const handleSync = async () => {
    if (textFiles.length === 0) return;
    setError('');
    setSyncing(true);
    setSyncResults([]);

    try {
      const results: SyncResult[] = [];
      for (const file of textFiles) {
        const formData = new FormData();
        formData.append('file', file);
        const res = await apiFetch('/api/sync', {
          method: 'POST',
          body: formData,
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          results.push({ filename: file.name, ok: false, error: data.error || 'Sync failed' });
          continue;
        }
        const data = await res.json();
        results.push({ filename: file.name, ok: true, data });
      }
      setSyncResults(results);
      setTextFiles([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setSyncing(false);
    }
  };

  const handleReset = () => {
    setTextFiles([]);
    setSyncResults([]);
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
        Add one or more LINE chat export files (.txt), sync them, then attach lesson images.
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
          {textFiles.length > 0 ? `${textFiles.length} text file${textFiles.length > 1 ? 's' : ''} selected` : 'Drag & drop one or more chat exports here'}
        </p>
        <p className="text-sm text-gray-500 mb-3">or</p>
        <label className="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded-lg cursor-pointer transition-colors">
          Add Text Files
          <input
            type="file"
            accept=".txt,text/plain"
            multiple
            className="hidden"
            onChange={e => {
              const selected = Array.from(e.target.files || []);
              if (selected.length > 0) {
                setTextFiles(prev => [...prev, ...selected]);
                setError('');
              }
            }}
          />
        </label>
        {textFiles.length > 0 && (
          <div className="mt-4 text-left max-h-40 overflow-auto rounded-lg border border-gray-800 p-3 bg-gray-950/50">
            {textFiles.map((f, i) => (
              <div key={`${f.name}-${i}`} className="text-sm text-gray-300 flex items-center justify-between gap-2 py-0.5">
                <span className="truncate">{f.name}</span>
                <button
                  type="button"
                  className="text-xs text-gray-500 hover:text-red-400"
                  onClick={() => setTextFiles(prev => prev.filter((_, idx) => idx !== i))}
                >
                  remove
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">
          {error}
        </div>
      )}

      {textFiles.length > 0 && (
        <button
          onClick={handleSync}
          disabled={syncing}
          className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-lg py-3 font-medium transition-colors"
        >
          {syncing ? '🔄 Syncing...' : `🚀 Sync ${textFiles.length} file${textFiles.length > 1 ? 's' : ''}`}
        </button>
      )}

      {/* Results */}
      {syncResults.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-3">
          <h2 className="text-lg font-semibold text-green-400">✅ Sync Results</h2>
          <div className="space-y-2 text-sm">
            {syncResults.map((r, idx) => (
              <div key={`${r.filename}-${idx}`} className="rounded-lg border border-gray-800 p-3 bg-gray-950/40">
                <div className="font-medium text-gray-200 truncate">{r.filename}</div>
                {r.ok && r.data ? (
                  <div className="mt-1 text-gray-400">
                    {r.data.duplicate ? (
                      <span>{r.data.session_count} sessions (already synced, no changes)</span>
                    ) : r.data.new_session_count != null && r.data.new_session_count < r.data.session_count ? (
                      <span>{r.data.new_session_count} new sessions added ({r.data.session_count} total), {r.data.message_count} messages, {r.data.lesson_content_count} lesson content, {r.data.warnings} warnings</span>
                    ) : (
                      <span>{r.data.session_count} sessions, {r.data.message_count} messages, {r.data.lesson_content_count} lesson content, {r.data.warnings} warnings</span>
                    )}
                  </div>
                ) : (
                  <div className="mt-1 text-red-400">{r.error || 'Sync failed'}</div>
                )}
              </div>
            ))}
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
              disabled={syncing || textFiles.length === 0}
              className="text-gray-400 hover:text-white text-sm"
            >
              {syncing ? 'Syncing...' : '🔄 Sync selected files'}
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
      {syncResults.some(r => r.ok) && (
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
