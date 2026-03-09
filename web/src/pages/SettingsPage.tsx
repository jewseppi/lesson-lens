import { useState, type FormEvent } from 'react';
import { apiFetch, apiJson } from '../api';
import { useAuth } from '../AuthContext';

type Provider = 'openai' | 'anthropic' | 'gemini';

const STORAGE_KEY = 'lessonlens-provider';
const REMOTE_URL_KEY = 'lessonlens-remote-url';
const REMOTE_EMAIL_KEY = 'lessonlens-remote-email';

function getStoredProvider(): Provider {
  const value = localStorage.getItem(STORAGE_KEY);
  return value === 'anthropic' || value === 'gemini' ? value : 'openai';
}

export default function SettingsPage() {
  const { user } = useAuth();
  const [provider, setProvider] = useState<Provider>(getStoredProvider);
  const [sessionId, setSessionId] = useState('');
  const [providerStatus, setProviderStatus] = useState('');
  const [sessionImportFile, setSessionImportFile] = useState<File | null>(null);
  const [sessionImportStatus, setSessionImportStatus] = useState('');
  const [sessionImportError, setSessionImportError] = useState('');
  const [importingSummary, setImportingSummary] = useState(false);
  const [bulkGenerating, setBulkGenerating] = useState(false);
  const [bulkStatus, setBulkStatus] = useState('');
  const [bulkError, setBulkError] = useState('');
  const [exportingBackup, setExportingBackup] = useState(false);
  const [backupFile, setBackupFile] = useState<File | null>(null);
  const [importingBackup, setImportingBackup] = useState(false);
  const [previewingBackup, setPreviewingBackup] = useState(false);
  const [backupPreview, setBackupPreview] = useState<{
    incoming_session_count: number;
    incoming_summary_count: number;
    new_session_count: number;
    new_summary_count: number;
    skipped_session_count: number;
    skipped_summary_count: number;
    existing_session_count: number;
    existing_summary_count: number;
  } | null>(null);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [backupStatus, setBackupStatus] = useState('');
  const [backupError, setBackupError] = useState('');
  const [remoteBaseUrl, setRemoteBaseUrl] = useState(() => localStorage.getItem(REMOTE_URL_KEY) || '');
  const [remoteEmail, setRemoteEmail] = useState(() => localStorage.getItem(REMOTE_EMAIL_KEY) || (user?.email ?? ''));
  const [remotePassword, setRemotePassword] = useState('');
  const [syncingRemote, setSyncingRemote] = useState(false);
  const [remoteSyncReplace, setRemoteSyncReplace] = useState(false);
  const [confirmRemoteReplace, setConfirmRemoteReplace] = useState(false);
  const [remoteSyncStatus, setRemoteSyncStatus] = useState('');
  const [remoteSyncError, setRemoteSyncError] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordStatus, setPasswordStatus] = useState('');
  const [passwordError, setPasswordError] = useState('');

  const saveProvider = (next: Provider) => {
    setProvider(next);
    localStorage.setItem(STORAGE_KEY, next);
    setProviderStatus(`Default provider saved: ${next}`);
  };

  const handleImportSummary = async () => {
    if (!sessionImportFile || !sessionId.trim()) {
      setSessionImportError('Select a lesson-data.json file and enter a session ID first.');
      return;
    }

    setImportingSummary(true);
    setSessionImportError('');
    setSessionImportStatus('');
    try {
      const formData = new FormData();
      formData.append('file', sessionImportFile);
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
      setSessionImportStatus(`Imported summary package for ${sessionId.trim()}.`);
      setSessionImportFile(null);
    } catch (err) {
      setSessionImportError(err instanceof Error ? err.message : 'Import failed');
    } finally {
      setImportingSummary(false);
    }
  };

  const handleBackupExport = async () => {
    setExportingBackup(true);
    setBackupError('');
    setBackupStatus('');
    try {
      const res = await apiFetch('/api/backup/export');
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Backup export failed');
      }

      const blob = await res.blob();
      const disposition = res.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename="?([^\"]+)"?/);
      const filename = match?.[1] || 'lessonlens-backup.zip';
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      setBackupStatus('Backup exported successfully. Keep the .zip file somewhere safe.');
    } catch (err) {
      setBackupError(err instanceof Error ? err.message : 'Backup export failed');
    } finally {
      setExportingBackup(false);
    }
  };

  const handleBackupPreview = async () => {
    if (!backupFile) {
      setBackupError('Choose a backup .zip file first.');
      return;
    }

    setPreviewingBackup(true);
    setBackupError('');
    setBackupStatus('');
    setBackupPreview(null);
    try {
      const formData = new FormData();
      formData.append('file', backupFile);
      const res = await apiFetch('/api/backup/import/preview', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || 'Preview failed');
      }
      setBackupPreview(data);
    } catch (err) {
      setBackupError(err instanceof Error ? err.message : 'Preview failed');
    } finally {
      setPreviewingBackup(false);
    }
  };

  const handleBackupImport = async (replaceAll = false) => {
    if (!backupFile) {
      setBackupError('Choose a backup .zip file first.');
      return;
    }

    setImportingBackup(true);
    setBackupError('');
    setBackupStatus('');
    setConfirmReplace(false);
    try {
      const formData = new FormData();
      formData.append('file', backupFile);
      formData.append('replace_existing', replaceAll ? 'true' : 'false');
      const res = await apiFetch('/api/backup/import', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || 'Backup import failed');
      }

      const parts: string[] = [];
      if (data.session_count > 0) parts.push(`${data.session_count} new sessions`);
      if (data.summary_count > 0) parts.push(`${data.summary_count} new summaries`);
      if (data.skipped_session_count > 0) parts.push(`${data.skipped_session_count} sessions already existed`);
      if (data.skipped_summary_count > 0) parts.push(`${data.skipped_summary_count} summaries already existed`);
      setBackupStatus(parts.length > 0 ? `Import complete: ${parts.join(', ')}.` : data.message || 'Import complete.');
      setBackupFile(null);
      setBackupPreview(null);
    } catch (err) {
      setBackupError(err instanceof Error ? err.message : 'Backup import failed');
    } finally {
      setImportingBackup(false);
    }
  };

  const handleRemoteSync = async () => {
    if (!remoteBaseUrl.trim() || !remoteEmail.trim() || !remotePassword) {
      setRemoteSyncError('Enter the remote URL, remote email, and remote password first.');
      return;
    }

    setSyncingRemote(true);
    setRemoteSyncError('');
    setRemoteSyncStatus('');

    localStorage.setItem(REMOTE_URL_KEY, remoteBaseUrl.trim());
    localStorage.setItem(REMOTE_EMAIL_KEY, remoteEmail.trim());

    setConfirmRemoteReplace(false);
    try {
      const result = await apiJson<{
        message: string;
        session_count: number;
        summary_count: number;
        remote_base_url: string;
        skipped_session_count?: number;
        skipped_summary_count?: number;
      }>('/api/backup/sync-remote', {
        method: 'POST',
        body: JSON.stringify({
          remote_base_url: remoteBaseUrl.trim(),
          remote_email: remoteEmail.trim(),
          remote_password: remotePassword,
          replace_existing: remoteSyncReplace,
        }),
      });

      const parts: string[] = [];
      parts.push(`${result.session_count} new sessions`);
      parts.push(`${result.summary_count} new summaries`);
      if (result.skipped_session_count) parts.push(`${result.skipped_session_count} sessions already existed`);
      if (result.skipped_summary_count) parts.push(`${result.skipped_summary_count} summaries already existed`);
      setRemoteSyncStatus(`Synced to ${result.remote_base_url}: ${parts.join(', ')}.`);
      setRemotePassword('');
    } catch (err) {
      setRemoteSyncError(err instanceof Error ? err.message : 'Remote sync failed');
    } finally {
      setSyncingRemote(false);
    }
  };

  const handlePasswordChange = async (e: FormEvent) => {
    e.preventDefault();
    setPasswordError('');
    setPasswordStatus('');

    if (!currentPassword || !newPassword || !confirmPassword) {
      setPasswordError('Fill in all password fields.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError('New password confirmation does not match.');
      return;
    }
    if (newPassword === currentPassword) {
      setPasswordError('New password must be different from the current password.');
      return;
    }

    setChangingPassword(true);
    try {
      const result = await apiJson<{ message: string }>('/api/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
          confirm_password: confirmPassword,
        }),
      });
      setPasswordStatus(result.message);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : 'Password change failed');
    } finally {
      setChangingPassword(false);
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
        {providerStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{providerStatus}</div>}
      </section>

      <section className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-white">Account Security</h2>
          <p className="text-sm text-gray-400 mt-1">
            Change the password for <span className="text-gray-200">{user?.email}</span>. Best practice is a password manager-generated secret or a unique passphrase with at least 16 characters.
          </p>
        </div>

        <form className="space-y-4" onSubmit={handlePasswordChange}>
          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Current password</span>
            <input
              type="password"
              value={currentPassword}
              onChange={e => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-gray-300">New password</span>
            <input
              type="password"
              value={newPassword}
              onChange={e => setNewPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Confirm new password</span>
            <input
              type="password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500"
            />
          </label>

          <p className="text-xs text-gray-500">
            The app rejects short, common, repetitive, or personally-derived passwords and stores the new hash with a stronger password hashing method.
          </p>

          <button
            type="submit"
            disabled={changingPassword}
            className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            {changingPassword ? 'Updating password...' : 'Change Password'}
          </button>
        </form>

        {passwordStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{passwordStatus}</div>}
        {passwordError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{passwordError}</div>}
      </section>

      <section className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-white">Backup and Restore</h2>
          <p className="text-sm text-gray-400 mt-1">
            Export the current parsed dataset and imported/generated summaries from local, then restore that backup into the live app. Only new sessions and summaries are added — existing data is never overwritten.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={() => void handleBackupExport()}
            disabled={exportingBackup}
            className="w-full sm:w-auto bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            {exportingBackup ? 'Preparing backup...' : 'Download Full Backup'}
          </button>
        </div>

        <div className="border-t border-gray-800 pt-4 space-y-3">
          <label className="block">
            <span className="text-sm text-gray-300 block mb-2">Restore backup .zip</span>
            <input
              type="file"
              accept="application/zip,.zip"
              className="block w-full text-sm text-gray-400 file:mr-4 file:rounded-lg file:border-0 file:bg-gray-800 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-gray-700"
              onChange={e => {
                setBackupFile(e.target.files?.[0] ?? null);
                setBackupPreview(null);
                setBackupStatus('');
                setBackupError('');
              }}
              disabled={importingBackup || previewingBackup}
            />
          </label>

          {!backupPreview && (
            <button
              onClick={() => void handleBackupPreview()}
              disabled={previewingBackup || importingBackup || !backupFile}
              className="w-full sm:w-auto bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
            >
              {previewingBackup ? 'Analyzing backup...' : 'Preview Import'}
            </button>
          )}

          {backupPreview && (
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 space-y-3">
              <h4 className="text-sm font-semibold text-white">Import Preview</h4>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div className="text-gray-400">Sessions in backup:</div>
                <div className="text-white">{backupPreview.incoming_session_count}</div>
                <div className="text-gray-400">Summaries in backup:</div>
                <div className="text-white">{backupPreview.incoming_summary_count}</div>
                <div className="text-gray-400">Already on this account:</div>
                <div className="text-yellow-400">{backupPreview.skipped_session_count} sessions, {backupPreview.skipped_summary_count} summaries</div>
                <div className="text-gray-400">New (will be added):</div>
                <div className="text-green-400">{backupPreview.new_session_count} sessions, {backupPreview.new_summary_count} summaries</div>
              </div>
              {backupPreview.new_session_count === 0 && backupPreview.new_summary_count === 0 ? (
                <p className="text-sm text-yellow-400">Nothing new to import — everything in this backup already exists.</p>
              ) : (
                <button
                  onClick={() => void handleBackupImport(false)}
                  disabled={importingBackup}
                  className="w-full sm:w-auto bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
                >
                  {importingBackup ? 'Importing...' : `Import ${backupPreview.new_session_count} New Sessions & ${backupPreview.new_summary_count} Summaries`}
                </button>
              )}

              <div className="border-t border-gray-700 pt-3 mt-3">
                {!confirmReplace ? (
                  <button
                    onClick={() => setConfirmReplace(true)}
                    disabled={importingBackup}
                    className="text-sm text-red-400 hover:text-red-300 underline underline-offset-2 transition-colors"
                  >
                    Replace entire dataset instead...
                  </button>
                ) : (
                  <div className="bg-red-950/60 border border-red-800 rounded-lg p-3 space-y-2">
                    <p className="text-sm text-red-300 font-semibold">⚠️ This will delete ALL existing sessions and summaries on this account and replace them with the backup contents.</p>
                    <p className="text-xs text-red-400">This action cannot be undone. Export a backup first if you need to preserve your current data.</p>
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => void handleBackupImport(true)}
                        disabled={importingBackup}
                        className="bg-red-700 hover:bg-red-800 disabled:opacity-50 text-white text-sm px-4 py-2 rounded-lg font-medium transition-colors"
                      >
                        {importingBackup ? 'Replacing...' : 'Yes, Replace Everything'}
                      </button>
                      <button
                        onClick={() => setConfirmReplace(false)}
                        disabled={importingBackup}
                        className="bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm px-4 py-2 rounded-lg font-medium transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          <p className="text-xs text-gray-500">
            Default: only new sessions and summaries are added. Use "Replace entire dataset" to start fresh from the backup.
          </p>
        </div>

        {backupStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{backupStatus}</div>}
        {backupError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{backupError}</div>}

        <div className="border-t border-gray-800 pt-4 space-y-4">
          <div>
            <h3 className="text-base font-semibold text-white">Sync to Another LessonLens</h3>
            <p className="text-sm text-gray-400 mt-1">
              One-click alternative to download + restore. This sends your latest parsed data and summaries directly into another LessonLens instance.
            </p>
          </div>

          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Remote app URL</span>
            <input
              type="url"
              value={remoteBaseUrl}
              onChange={e => setRemoteBaseUrl(e.target.value)}
              placeholder="https://lens.jsilverman.ca"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Remote account email</span>
            <input
              type="email"
              value={remoteEmail}
              onChange={e => setRemoteEmail(e.target.value)}
              placeholder="admin@example.com"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Remote account password</span>
            <input
              type="password"
              value={remotePassword}
              onChange={e => setRemotePassword(e.target.value)}
              autoComplete="current-password"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500"
            />
          </label>

          <button
            onClick={() => {
              if (remoteSyncReplace && !confirmRemoteReplace) return;
              void handleRemoteSync();
            }}
            disabled={syncingRemote || (remoteSyncReplace && !confirmRemoteReplace)}
            className="w-full sm:w-auto bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            {syncingRemote ? 'Syncing to remote...' : 'Sync Local Data To Remote'}
          </button>

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={remoteSyncReplace}
                onChange={e => {
                  setRemoteSyncReplace(e.target.checked);
                  setConfirmRemoteReplace(false);
                }}
                className="rounded border-gray-600 bg-gray-800 text-red-600 focus:ring-red-500"
              />
              <span className="text-red-400">Replace remote data instead of merging</span>
            </label>

            {remoteSyncReplace && !confirmRemoteReplace && (
              <div className="bg-red-950/60 border border-red-800 rounded-lg p-3 space-y-2">
                <p className="text-sm text-red-300">⚠️ With this enabled, syncing will <strong>delete all existing sessions and summaries</strong> on the remote account and replace them with your local data.</p>
                <button
                  onClick={() => setConfirmRemoteReplace(true)}
                  className="bg-red-700 hover:bg-red-800 text-white text-sm px-4 py-2 rounded-lg font-medium transition-colors"
                >
                  I understand, allow replace
                </button>
              </div>
            )}
            {remoteSyncReplace && confirmRemoteReplace && (
              <p className="text-xs text-red-400">Replace mode active — remote data will be overwritten on sync.</p>
            )}
          </div>

          <p className="text-xs text-gray-500">
            Default: merges new sessions and summaries into the remote account. Existing data is preserved.
          </p>

          {remoteSyncStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{remoteSyncStatus}</div>}
          {remoteSyncError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{remoteSyncError}</div>}
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
            onChange={e => setSessionImportFile(e.target.files?.[0] ?? null)}
            disabled={importingSummary}
          />
        </label>

        <button
          onClick={() => void handleImportSummary()}
          disabled={importingSummary || !sessionImportFile || !sessionId.trim()}
          className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
        >
          {importingSummary ? 'Importing summary package...' : 'Import Summary Package'}
        </button>

        {sessionImportStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{sessionImportStatus}</div>}
        {sessionImportError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{sessionImportError}</div>}
      </section>
    </div>
  );
}