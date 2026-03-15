import { useState, useEffect, type FormEvent, type ReactNode } from 'react';
import { apiFetch, apiJson } from '../api';
import { useAuth } from '../AuthContext';
import type { ReparseResult } from '../types';

type Provider = 'openai' | 'anthropic' | 'gemini' | 'ollama' | 'openai_compatible_local';
type SettingsTab = 'general' | 'data' | 'account' | 'agents';

const STORAGE_KEY = 'lessonlens-provider';
const MODEL_KEY_PREFIX = 'lessonlens-model-';
const REMOTE_URL_KEY = 'lessonlens-remote-url';
const REMOTE_EMAIL_KEY = 'lessonlens-remote-email';
const SETTINGS_TAB_KEY = 'lessonlens-settings-tab';

const CLOUD_MODEL_DEFAULTS: Record<string, string> = {
  openai: 'gpt-4o',
  anthropic: 'claude-sonnet-4-20250514',
  gemini: 'gemini-2.0-flash',
};

function getStoredModel(provider: Provider): string {
  return localStorage.getItem(`${MODEL_KEY_PREFIX}${provider}`) || '';
}

function saveStoredModel(provider: Provider, model: string) {
  if (model) {
    localStorage.setItem(`${MODEL_KEY_PREFIX}${provider}`, model);
  } else {
    localStorage.removeItem(`${MODEL_KEY_PREFIX}${provider}`);
  }
}

const ALL_PROVIDERS: Provider[] = ['openai', 'anthropic', 'gemini', 'ollama', 'openai_compatible_local'];

function getStoredProvider(): Provider {
  const value = localStorage.getItem(STORAGE_KEY);
  if (value && ALL_PROVIDERS.includes(value as Provider)) return value as Provider;
  return 'openai';
}

function getStoredTab(): SettingsTab {
  const value = localStorage.getItem(SETTINGS_TAB_KEY);
  if (value && ['general', 'data', 'account', 'agents'].includes(value)) return value as SettingsTab;
  return 'general';
}

interface LocalHealthResult {
  ok: boolean;
  base_url: string;
  models?: string[];
  error?: string;
}

const TABS: { key: SettingsTab; label: string }[] = [
  { key: 'general', label: 'General' },
  { key: 'data', label: 'Data' },
  { key: 'account', label: 'Account' },
  { key: 'agents', label: 'Agents' },
];

function Section({ children }: { children: ReactNode }) {
  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
      {children}
    </section>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<SettingsTab>(getStoredTab);

  const switchTab = (next: SettingsTab) => {
    setTab(next);
    localStorage.setItem(SETTINGS_TAB_KEY, next);
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-gray-400 mt-1">Configure providers, manage data, and set up agent integrations.</p>
      </div>

      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => switchTab(t.key)}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === t.key
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'general' && <GeneralTab />}
      {tab === 'data' && <DataTab user={user} />}
      {tab === 'account' && <AccountTab user={user} />}
      {tab === 'agents' && <AgentsTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// General Tab
// ---------------------------------------------------------------------------
function GeneralTab() {
  const [provider, setProvider] = useState<Provider>(getStoredProvider);
  const [selectedModel, setSelectedModel] = useState(() => getStoredModel(getStoredProvider()));
  const [providerStatus, setProviderStatus] = useState('');
  const [localHealth, setLocalHealth] = useState<{ ollama: LocalHealthResult; openai_compatible_local: LocalHealthResult } | null>(null);
  const [healthChecking, setHealthChecking] = useState(false);
  const [reparsing, setReparsing] = useState(false);
  const [reparseResult, setReparseResult] = useState<ReparseResult | null>(null);
  const [reparseError, setReparseError] = useState('');

  const saveProvider = (next: Provider) => {
    setProvider(next);
    setSelectedModel(getStoredModel(next));
    localStorage.setItem(STORAGE_KEY, next);
    setProviderStatus(`Default provider saved: ${next}`);
  };

  const handleModelChange = (model: string) => {
    setSelectedModel(model);
    saveStoredModel(provider, model);
  };

  const checkLocalHealth = async () => {
    setHealthChecking(true);
    try {
      const data = await apiJson<{ ollama: LocalHealthResult; openai_compatible_local: LocalHealthResult }>('/api/models/local/health');
      setLocalHealth(data);
    } catch {
      setLocalHealth(null);
    } finally {
      setHealthChecking(false);
    }
  };

  // Auto-check health when a local provider is selected
  useEffect(() => {
    if (provider === 'ollama' || provider === 'openai_compatible_local') {
      void checkLocalHealth();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const handleReparse = async () => {
    setReparsing(true);
    setReparseResult(null);
    setReparseError('');
    try {
      const result = await apiJson<ReparseResult>('/api/reparse', { method: 'POST' });
      setReparseResult(result);
    } catch (err) {
      setReparseError(err instanceof Error ? err.message : 'Re-parse failed');
    } finally {
      setReparsing(false);
    }
  };

  return (
    <div className="space-y-8">
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Default AI Provider</h2>
          <p className="text-sm text-gray-400 mt-1">
            This provider will be used by default when generating a summary from the session page.
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-500 mb-2">Cloud</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {([['openai', 'OpenAI', 'green'], ['anthropic', 'Claude', 'orange'], ['gemini', 'Gemini', 'blue']] as const).map(([key, label, color]) => (
              <button
                key={key}
                onClick={() => saveProvider(key)}
                className={`py-2 rounded-lg text-sm font-medium transition-colors border ${
                  provider === key
                    ? `bg-${color}-900/50 border-${color}-600 text-${color}-300`
                    : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div>
          <p className="text-xs text-gray-500 mb-2">Local</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <button
              onClick={() => saveProvider('ollama')}
              className={`py-2 rounded-lg text-sm font-medium transition-colors border ${
                provider === 'ollama'
                  ? 'bg-purple-900/50 border-purple-600 text-purple-300'
                  : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
              }`}
            >
              Ollama
            </button>
            <button
              onClick={() => saveProvider('openai_compatible_local')}
              className={`py-2 rounded-lg text-sm font-medium transition-colors border ${
                provider === 'openai_compatible_local'
                  ? 'bg-teal-900/50 border-teal-600 text-teal-300'
                  : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
              }`}
            >
              Local OpenAI-Compatible
            </button>
          </div>
        </div>

        {/* Model selector for cloud providers */}
        {(provider === 'openai' || provider === 'anthropic' || provider === 'gemini') && (
          <div className="border-t border-gray-800 pt-4 space-y-2">
            <label className="text-xs text-gray-500">Model override (leave blank for default: {CLOUD_MODEL_DEFAULTS[provider]})</label>
            <input
              type="text"
              value={selectedModel}
              onChange={e => handleModelChange(e.target.value)}
              placeholder={CLOUD_MODEL_DEFAULTS[provider]}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500"
            />
          </div>
        )}

        {/* Local provider health + model selector */}
        {(provider === 'ollama' || provider === 'openai_compatible_local') && (
          <div className="border-t border-gray-800 pt-4 space-y-3">
            <button
              onClick={() => void checkLocalHealth()}
              disabled={healthChecking}
              className="w-full sm:w-auto bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              {healthChecking ? 'Checking...' : 'Refresh Models'}
            </button>

            {localHealth && (
              <div className="space-y-2">
                {(['ollama', 'openai_compatible_local'] as const).map(key => {
                  const h = localHealth[key];
                  return (
                    <div key={key} className={`text-sm rounded-lg p-3 border ${h.ok ? 'bg-green-900/40 border-green-700 text-green-300' : 'bg-red-900/50 border-red-700 text-red-300'}`}>
                      <span className="font-semibold">{key === 'ollama' ? 'Ollama' : 'OpenAI-Compatible'}</span>
                      {' — '}
                      {h.ok ? (
                        <>Online at {h.base_url}{h.models && h.models.length > 0 && ` (${h.models.length} model${h.models.length === 1 ? '' : 's'})`}</>
                      ) : (
                        <>Unreachable at {h.base_url}. {key === 'ollama' ? 'Is Ollama running? (ollama serve)' : 'Is your local server running?'}</>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Model dropdown when models are available */}
            {localHealth && localHealth[provider as 'ollama' | 'openai_compatible_local']?.ok && (
              <div className="space-y-1">
                <label className="text-xs text-gray-500">Select model</label>
                <select
                  value={selectedModel}
                  onChange={e => handleModelChange(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500"
                >
                  <option value="">Use server default</option>
                  {(localHealth[provider as 'ollama' | 'openai_compatible_local']?.models || []).map(m => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
            )}

            <p className="text-xs text-gray-500">
              {provider === 'ollama'
                ? 'Ollama must be running locally. Default model: qwen2.5:7b-instruct.'
                : 'Point LOCAL_OAI_BASE_URL to your LM Studio / vLLM / llama.cpp server. Default: http://localhost:1234/v1.'}
            </p>
          </div>
        )}

        {providerStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{providerStatus}</div>}
      </Section>

      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Re-parse Sessions</h2>
          <p className="text-sm text-gray-400 mt-1">
            Re-run the parser on your uploaded chat export using the latest parser code.
            Picks up new format support, improved classification, and pinyin detection
            without losing existing summaries or annotations.
          </p>
        </div>

        <button
          onClick={() => void handleReparse()}
          disabled={reparsing}
          className="w-full sm:w-auto bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
        >
          {reparsing ? 'Re-parsing sessions...' : 'Re-parse All Sessions'}
        </button>

        <p className="text-xs text-gray-500">
          Archives the previous sessions.json for recovery. Summaries and annotations are preserved.
        </p>

        {reparseResult && (
          <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3 space-y-1">
            <div className="font-semibold">Re-parse complete</div>
            <div className="text-xs space-y-0.5">
              <div>Total sessions: {reparseResult.total_sessions} ({reparseResult.updated_sessions} updated, {reparseResult.new_sessions} new)</div>
              <div>Total messages: {reparseResult.total_messages}</div>
              <div>Lesson content: {reparseResult.lesson_content_count}</div>
              {(reparseResult.auto_archived ?? 0) > 0 && (
                <div className="text-yellow-300">Auto-archived {reparseResult.auto_archived} sessions with no teacher messages</div>
              )}
              {(reparseResult.auto_unarchived ?? 0) > 0 && (
                <div className="text-green-300">Restored {reparseResult.auto_unarchived} sessions (teacher messages detected)</div>
              )}
              {(reparseResult.user_overrides_applied ?? 0) > 0 && (
                <div className="text-purple-300">Applied {reparseResult.user_overrides_applied} user corrections from feedback</div>
              )}
            </div>
          </div>
        )}
        {reparseError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{reparseError}</div>}
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Data Tab
// ---------------------------------------------------------------------------
function DataTab({ user }: { user: ReturnType<typeof useAuth>['user'] }) {
  const provider = getStoredProvider();
  const [sessionId, setSessionId] = useState('');
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
  const [runnerDispatching, setRunnerDispatching] = useState(false);
  const [runnerStatus, setRunnerStatus] = useState<{
    job_id?: number;
    status: string;
    dispatched_at?: string;
    completed_at?: string;
    result?: { generated: number; failed: number; imported: number; import_failed: number; total_missing: number } | null;
  } | null>(null);
  const [runnerError, setRunnerError] = useState('');

  const dispatchRunnerGeneration = async () => {
    setRunnerDispatching(true);
    setRunnerError('');
    try {
      await apiJson<{ job_id: number; message: string }>('/api/generation/dispatch', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      void pollRunnerStatus();
    } catch (err) {
      setRunnerError(err instanceof Error ? err.message : 'Failed to dispatch runner');
    } finally {
      setRunnerDispatching(false);
    }
  };

  const pollRunnerStatus = async () => {
    try {
      const data = await apiJson<typeof runnerStatus>('/api/generation/status');
      setRunnerStatus(data);
      if (data && (data.status === 'dispatched' || data.status === 'running')) {
        setTimeout(() => void pollRunnerStatus(), 30_000);
      }
    } catch {
      // Silently ignore poll failures
    }
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
      const match = disposition.match(/filename="?([^"]+)"?/);
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
    if (!backupFile) { setBackupError('Choose a backup .zip file first.'); return; }
    setPreviewingBackup(true);
    setBackupError('');
    setBackupStatus('');
    setBackupPreview(null);
    try {
      const formData = new FormData();
      formData.append('file', backupFile);
      const res = await apiFetch('/api/backup/import/preview', { method: 'POST', body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Preview failed');
      setBackupPreview(data);
    } catch (err) {
      setBackupError(err instanceof Error ? err.message : 'Preview failed');
    } finally {
      setPreviewingBackup(false);
    }
  };

  const handleBackupImport = async (replaceAll = false) => {
    if (!backupFile) { setBackupError('Choose a backup .zip file first.'); return; }
    setImportingBackup(true);
    setBackupError('');
    setBackupStatus('');
    setConfirmReplace(false);
    try {
      const formData = new FormData();
      formData.append('file', backupFile);
      formData.append('replace_existing', replaceAll ? 'true' : 'false');
      const res = await apiFetch('/api/backup/import', { method: 'POST', body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Backup import failed');
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
        body: JSON.stringify({ provider, min_lesson_content_count: 3 }),
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
    <div className="space-y-8">
      {/* Backup and Restore */}
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Backup and Restore</h2>
          <p className="text-sm text-gray-400 mt-1">
            Export the current parsed dataset and summaries, then restore that backup into the live app.
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
                    <p className="text-sm text-red-300 font-semibold">This will delete ALL existing sessions and summaries on this account and replace them with the backup contents.</p>
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

        {/* Remote Sync */}
        <div className="border-t border-gray-800 pt-4 space-y-4">
          <div>
            <h3 className="text-base font-semibold text-white">Sync to Another LessonLens</h3>
            <p className="text-sm text-gray-400 mt-1">
              One-click alternative to download + restore. Sends your latest data directly into another LessonLens instance.
            </p>
          </div>

          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Remote app URL</span>
            <input type="url" value={remoteBaseUrl} onChange={e => setRemoteBaseUrl(e.target.value)}
              placeholder="https://lens.jsilverman.ca"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500" />
          </label>
          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Remote account email</span>
            <input type="email" value={remoteEmail} onChange={e => setRemoteEmail(e.target.value)}
              placeholder="admin@example.com"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500" />
          </label>
          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Remote account password</span>
            <input type="password" value={remotePassword} onChange={e => setRemotePassword(e.target.value)}
              autoComplete="current-password"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500" />
          </label>

          <button
            onClick={() => { if (remoteSyncReplace && !confirmRemoteReplace) return; void handleRemoteSync(); }}
            disabled={syncingRemote || (remoteSyncReplace && !confirmRemoteReplace)}
            className="w-full sm:w-auto bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            {syncingRemote ? 'Syncing to remote...' : 'Sync Local Data To Remote'}
          </button>

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={remoteSyncReplace}
                onChange={e => { setRemoteSyncReplace(e.target.checked); setConfirmRemoteReplace(false); }}
                className="rounded border-gray-600 bg-gray-800 text-red-600 focus:ring-red-500" />
              <span className="text-red-400">Replace remote data instead of merging</span>
            </label>
            {remoteSyncReplace && !confirmRemoteReplace && (
              <div className="bg-red-950/60 border border-red-800 rounded-lg p-3 space-y-2">
                <p className="text-sm text-red-300">With this enabled, syncing will <strong>delete all existing sessions and summaries</strong> on the remote account and replace them with your local data.</p>
                <button onClick={() => setConfirmRemoteReplace(true)}
                  className="bg-red-700 hover:bg-red-800 text-white text-sm px-4 py-2 rounded-lg font-medium transition-colors">
                  I understand, allow replace
                </button>
              </div>
            )}
            {remoteSyncReplace && confirmRemoteReplace && (
              <p className="text-xs text-red-400">Replace mode active — remote data will be overwritten on sync.</p>
            )}
          </div>

          <p className="text-xs text-gray-500">Default: merges new sessions and summaries into the remote account.</p>

          {remoteSyncStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{remoteSyncStatus}</div>}
          {remoteSyncError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{remoteSyncError}</div>}
        </div>
      </Section>

      {/* Generate Missing Summaries */}
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Generate Missing Summaries</h2>
          <p className="text-sm text-gray-400 mt-1">
            Generate summaries for every parsed session with at least 3 lesson-content messages that does not already have one.
          </p>
        </div>

        <button onClick={handleGenerateMissing} disabled={bulkGenerating}
          className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors">
          {bulkGenerating ? 'Generating missing summaries...' : 'Generate All Missing Summaries'}
        </button>

        <p className="text-xs text-gray-500">Uses the current default provider: <span className="text-gray-300">{provider}</span>.</p>

        {bulkStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{bulkStatus}</div>}
        {bulkError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{bulkError}</div>}
      </Section>

      {/* Ollama Runner */}
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Generate Missing (Ollama Runner)</h2>
          <p className="text-sm text-gray-400 mt-1">
            Generate summaries using a free GitHub Actions runner with Ollama. Takes 5-10 minutes per session. No API keys needed.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={() => void dispatchRunnerGeneration()}
            disabled={runnerDispatching || (runnerStatus?.status === 'dispatched') || (runnerStatus?.status === 'running')}
            className="w-full sm:w-auto bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            {runnerDispatching ? 'Dispatching...' : (runnerStatus?.status === 'dispatched' || runnerStatus?.status === 'running') ? 'Runner in progress...' : 'Generate Missing (Ollama Runner)'}
          </button>
          <button onClick={() => void pollRunnerStatus()}
            className="w-full sm:w-auto bg-gray-700 hover:bg-gray-600 text-white px-4 py-3 rounded-lg text-sm font-medium transition-colors">
            Check Status
          </button>
        </div>

        {runnerStatus && runnerStatus.status !== 'none' && (
          <div className={`text-sm rounded-lg p-3 border ${
            runnerStatus.status === 'completed' ? 'bg-green-900/40 border-green-700 text-green-300' :
            runnerStatus.status === 'completed_with_errors' ? 'bg-yellow-900/40 border-yellow-700 text-yellow-300' :
            runnerStatus.status === 'failed' ? 'bg-red-900/50 border-red-700 text-red-300' :
            'bg-blue-900/40 border-blue-700 text-blue-300'
          }`}>
            <span className="font-semibold">
              {runnerStatus.status === 'dispatched' && 'Runner dispatched — waiting for GitHub Actions to start...'}
              {runnerStatus.status === 'running' && 'Runner is generating summaries...'}
              {runnerStatus.status === 'completed' && 'Generation complete'}
              {runnerStatus.status === 'completed_with_errors' && 'Generation complete (with some errors)'}
              {runnerStatus.status === 'failed' && 'Generation failed'}
            </span>
            {runnerStatus.dispatched_at && (
              <span className="text-xs ml-2 opacity-75">
                Dispatched {new Date(runnerStatus.dispatched_at + 'Z').toLocaleString()}
              </span>
            )}
            {runnerStatus.result && (
              <div className="mt-2 text-xs space-y-0.5">
                <div>Generated: {runnerStatus.result.generated} / {runnerStatus.result.total_missing} missing</div>
                <div>Imported: {runnerStatus.result.imported}</div>
                {(runnerStatus.result.failed > 0 || runnerStatus.result.import_failed > 0) && (
                  <div className="text-red-400">Failed: {runnerStatus.result.failed} generation, {runnerStatus.result.import_failed} import</div>
                )}
              </div>
            )}
          </div>
        )}

        <p className="text-xs text-gray-500">
          Uses a GitHub Actions runner with Ollama (qwen2.5:7b-instruct).
          {(runnerStatus?.status === 'dispatched' || runnerStatus?.status === 'running') && ' Status auto-refreshes every 30 seconds.'}
        </p>
        {runnerError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{runnerError}</div>}
      </Section>

      {/* Import Summary Package */}
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Import Summary Package</h2>
          <p className="text-sm text-gray-400 mt-1">
            Upload a completed <span className="font-mono">lesson-data.json</span> created on another machine or by another agent.
          </p>
        </div>

        <label className="block space-y-2">
          <span className="text-sm text-gray-300">Session ID</span>
          <input type="text" value={sessionId} onChange={e => setSessionId(e.target.value)}
            placeholder="2026-03-05"
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500" />
        </label>
        <label className="block">
          <span className="sr-only">Choose lesson-data.json</span>
          <input type="file" accept="application/json,.json"
            className="block w-full text-sm text-gray-400 file:mr-4 file:rounded-lg file:border-0 file:bg-gray-800 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-gray-700"
            onChange={e => setSessionImportFile(e.target.files?.[0] ?? null)}
            disabled={importingSummary} />
        </label>
        <button onClick={() => void handleImportSummary()}
          disabled={importingSummary || !sessionImportFile || !sessionId.trim()}
          className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors">
          {importingSummary ? 'Importing summary package...' : 'Import Summary Package'}
        </button>
        {sessionImportStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{sessionImportStatus}</div>}
        {sessionImportError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{sessionImportError}</div>}
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Account Tab
// ---------------------------------------------------------------------------
function AccountTab({ user }: { user: ReturnType<typeof useAuth>['user'] }) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordStatus, setPasswordStatus] = useState('');
  const [passwordError, setPasswordError] = useState('');

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

  return (
    <div className="space-y-8">
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Change Password</h2>
          <p className="text-sm text-gray-400 mt-1">
            Change the password for <span className="text-gray-200">{user?.email}</span>.
            Best practice is a password manager-generated secret or a unique passphrase with at least 16 characters.
          </p>
        </div>

        <form className="space-y-4" onSubmit={handlePasswordChange}>
          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Current password</span>
            <input type="password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500" />
          </label>
          <label className="block space-y-2">
            <span className="text-sm text-gray-300">New password</span>
            <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500" />
          </label>
          <label className="block space-y-2">
            <span className="text-sm text-gray-300">Confirm new password</span>
            <input type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500" />
          </label>
          <p className="text-xs text-gray-500">
            The app rejects short, common, repetitive, or personally-derived passwords.
          </p>
          <button type="submit" disabled={changingPassword}
            className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-5 py-3 rounded-lg font-medium transition-colors">
            {changingPassword ? 'Updating password...' : 'Change Password'}
          </button>
        </form>

        {passwordStatus && <div className="bg-green-900/40 border border-green-700 text-green-300 text-sm rounded-lg p-3">{passwordStatus}</div>}
        {passwordError && <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">{passwordError}</div>}
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agents Tab
// ---------------------------------------------------------------------------
const CONTEXT_DOC = `# LessonLens — Agent Context

## What This App Does
LessonLens parses WeChat Mandarin lesson transcripts, classifies messages (lesson-content,
logistics, media, etc.), generates AI-powered lesson summaries with vocabulary, key sentences,
and corrections, and provides study tools. It runs as a Flask API + React SPA.

## Project Structure
\`\`\`
api/app.py              — Flask API (all endpoints, DB schema, auth)
scripts/
  parse_line_export.py  — Message parser (regex + context-aware classification)
  generate_outputs.py   — LLM summary generator (OpenAI, Anthropic, Gemini, Ollama)
  pinyin_dict.py        — Pinyin syllable dictionary for informal romanization detection
  extract_transcript.py — Chat export file reader
  ai_review.py          — AI review scripts (Phase 2)
prompts/
  system-prompt.md      — Main LLM prompt for summary generation
  parse-reviewer.md     — Parse review prompt (Phase 2)
web/src/
  pages/                — React pages (Dashboard, Sessions, Summary, Study, Settings, Admin, Upload)
  components/           — Shared components
  types.ts              — TypeScript interfaces
  api.ts                — API client with JWT auth
config.json             — Parser config (speaker names, patterns, timezone)
\`\`\`

## Key API Endpoints (all require JWT unless noted)

### Auth
- POST /api/login — { email, password } -> { access_token }
- POST /api/register — { email, password, display_name, token }
- POST /api/change-password — { current_password, new_password, confirm_password }

### Sessions & Summaries
- GET  /api/sessions — List sessions (with has_summary, needs_summary flags)
- GET  /api/sessions/<id> — Session detail with messages
- GET  /api/sessions/<id>/summary — Get generated summary (lesson-data.json)
- POST /api/sessions/<id>/generate — Generate summary { provider, model? }
- POST /api/sessions/<id>/summary/import — Upload lesson-data.json (multipart)
- POST /api/summaries/generate — Bulk generate missing summaries { provider }

### Annotations
- GET    /api/sessions/<id>/annotations — List annotations for session
- POST   /api/sessions/<id>/annotations — Create { target_type, target_id, annotation_type, content }
- PUT    /api/sessions/<id>/annotations/<aid> — Update annotation
- DELETE /api/sessions/<id>/annotations/<aid> — Soft-delete (status='dismissed')

### Data Management
- POST /api/upload — Upload .txt chat export (multipart)
- POST /api/parse/<upload_id> — Parse uploaded file
- POST /api/reparse — Re-parse all sessions with latest parser code
- GET  /api/backup/export — Download full backup .zip
- POST /api/backup/import — Restore backup .zip (multipart, replace_existing flag)
- POST /api/backup/sync-remote — Sync to another LessonLens instance

### Attachments
- POST /api/attachments/upload — Upload images (multipart)
- GET  /api/attachments — List all attachments
- GET  /api/sessions/<id>/attachments — Session attachments
- POST /api/sessions/<id>/attachments/assign — Assign attachment to session

### Admin
- GET  /api/admin/users — List users
- POST /api/admin/users/<id>/suspend — Suspend user
- POST /api/admin/users/<id>/reactivate — Reactivate user
- POST /api/admin/users/<id>/role — Set role { role: 'student' | 'teacher' }
- POST /api/admin/invitations — Create invitation token

### Generation Runner
- POST /api/generation/dispatch — Trigger GitHub Actions Ollama runner
- GET  /api/generation/status — Check runner status
- POST /api/generation/webhook — Webhook callback (no JWT, requires X-Webhook-Token)

## Database Tables
users, uploads, parse_runs, sessions, lesson_summaries, annotations,
analytics_events, invitation_tokens, signup_requests, security_events,
attachments, session_attachments, generation_jobs

## Common Agent Tasks

### Generate a summary for a specific session
\`\`\`bash
curl -X POST $BASE_URL/api/sessions/2026-03-05/generate \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"provider": "openai"}'
\`\`\`

### Re-parse all sessions after parser improvements
\`\`\`bash
curl -X POST $BASE_URL/api/reparse \\
  -H "Authorization: Bearer $TOKEN"
\`\`\`

### Add an annotation (correction)
\`\`\`bash
curl -X POST $BASE_URL/api/sessions/2026-03-05/annotations \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"target_type":"summary_item","target_id":"vocab-0","target_section":"vocabulary","annotation_type":"correction","content":{"field":"en","original":"hit fold","corrected":"discount"}}'
\`\`\`

### Bulk generate missing summaries
\`\`\`bash
curl -X POST $BASE_URL/api/summaries/generate \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"provider": "anthropic", "min_lesson_content_count": 3}'
\`\`\`
`;

const CURSOR_RULES = `# LessonLens Project Rules

## Architecture
- Flask API at api/app.py (monolith, SQLite, JWT auth)
- React SPA at web/src/ (Vite, Tailwind, TypeScript)
- Python scripts in scripts/ for parsing and generation
- All API calls go through web/src/api.ts (apiFetch / apiJson helpers)

## Conventions
- Dark theme UI (gray-900 backgrounds, gray-800 borders)
- Mobile-first responsive design
- API endpoints use /api/ prefix, JWT required
- Parser config in config.json (speaker mappings, timezone)
- Summaries stored as lesson-data.json per session

## Key Patterns
- State management: React useState + useEffect (no Redux)
- Auth: JWT tokens via AuthContext
- Forms: controlled inputs with inline validation
- Error display: red/green alert boxes with border styling
- File uploads: FormData via apiFetch (not apiJson)

## Testing
- pytest for API: api/tests/ (97% coverage)
- Run: .venv/bin/pytest api/tests/ -x -q
- Frontend build check: cd web && npm run build

## Do NOT
- Modify the database schema without updating init_db()
- Skip JWT auth on new endpoints (except webhooks)
- Use localStorage for sensitive data
- Import React components with default exports from wrong paths
`;

const TASK_TEMPLATES: { label: string; prompt: string }[] = [
  {
    label: 'Generate missing summaries',
    prompt: `Run the bulk summary generation for all sessions that don't have summaries yet.
Use the API endpoint POST /api/summaries/generate with provider "openai" and min_lesson_content_count 3.
First login to get a JWT token, then call the endpoint.`,
  },
  {
    label: 'Re-parse with latest parser',
    prompt: `The parser code has been updated. Re-parse all sessions to pick up the improvements.
Call POST /api/reparse with a valid JWT token. This preserves existing summaries and annotations.
After re-parsing, verify the session counts look correct.`,
  },
  {
    label: 'Add parser classification rule',
    prompt: `I need to add a new message classification rule to the parser.
The parser is in scripts/parse_line_export.py, function classify_message().
Messages are classified into types: lesson-content, logistics, media, sticker, system, unknown.
Add the new rule and run tests: .venv/bin/pytest api/tests/ -x -q
Then re-parse via the API to apply changes to existing sessions.`,
  },
  {
    label: 'Import summary from file',
    prompt: `Import a pre-generated lesson-data.json summary for a specific session.
Use POST /api/sessions/<session_id>/summary/import with multipart form data.
The file should be a valid lesson-data.json with key_sentences, vocabulary, and corrections.`,
  },
  {
    label: 'Review and correct translations',
    prompt: `Review the summary for session <SESSION_ID> by calling GET /api/sessions/<SESSION_ID>/summary.
Check each vocabulary item and key sentence for translation accuracy.
For any errors, create annotations via POST /api/sessions/<SESSION_ID>/annotations with:
  target_type: "summary_item"
  annotation_type: "correction"
  content: { field: "en"|"pinyin", original: "...", corrected: "..." }`,
  },
];

function AgentsTab() {
  const [copied, setCopied] = useState<string | null>(null);

  const copyToClipboard = async (text: string, label: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(label);
    setTimeout(() => setCopied(null), 2000);
  };

  const downloadFile = (content: string, filename: string) => {
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-8">
      {/* Context Document */}
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Agent Context Document</h2>
          <p className="text-sm text-gray-400 mt-1">
            A generated markdown file with project structure, all API endpoints, database schema,
            and common task examples. Drop this into your IDE so AI agents understand the project.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={() => void copyToClipboard(CONTEXT_DOC, 'context')}
            className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            {copied === 'context' ? 'Copied!' : 'Copy to Clipboard'}
          </button>
          <button
            onClick={() => downloadFile(CONTEXT_DOC, 'LESSONLENS_CONTEXT.md')}
            className="w-full sm:w-auto bg-gray-700 hover:bg-gray-600 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            Download .md
          </button>
        </div>

        <div className="border-t border-gray-800 pt-4 space-y-3">
          <h3 className="text-sm font-semibold text-white">Where to place it</h3>
          <div className="grid gap-2 text-sm">
            <div className="flex items-start gap-3 bg-gray-800 rounded-lg p-3">
              <span className="text-indigo-400 font-mono text-xs mt-0.5 shrink-0">Claude Code</span>
              <span className="text-gray-300">Paste into <code className="text-gray-200 bg-gray-700 px-1 rounded">CLAUDE.md</code> at the project root</span>
            </div>
            <div className="flex items-start gap-3 bg-gray-800 rounded-lg p-3">
              <span className="text-green-400 font-mono text-xs mt-0.5 shrink-0">Cursor</span>
              <span className="text-gray-300">Save as <code className="text-gray-200 bg-gray-700 px-1 rounded">.cursorrules</code> at the project root</span>
            </div>
            <div className="flex items-start gap-3 bg-gray-800 rounded-lg p-3">
              <span className="text-blue-400 font-mono text-xs mt-0.5 shrink-0">VS Code</span>
              <span className="text-gray-300">Save as <code className="text-gray-200 bg-gray-700 px-1 rounded">.github/copilot-instructions.md</code></span>
            </div>
          </div>
        </div>
      </Section>

      {/* Cursor Rules */}
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">IDE Rules File</h2>
          <p className="text-sm text-gray-400 mt-1">
            Compact project conventions and patterns for Cursor / Copilot. Covers architecture,
            coding patterns, testing, and things to avoid.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={() => void copyToClipboard(CURSOR_RULES, 'rules')}
            className="w-full sm:w-auto bg-indigo-600 hover:bg-indigo-700 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            {copied === 'rules' ? 'Copied!' : 'Copy to Clipboard'}
          </button>
          <button
            onClick={() => downloadFile(CURSOR_RULES, '.cursorrules')}
            className="w-full sm:w-auto bg-gray-700 hover:bg-gray-600 text-white px-5 py-3 rounded-lg font-medium transition-colors"
          >
            Download .cursorrules
          </button>
        </div>
      </Section>

      {/* Task Templates */}
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">Task Templates</h2>
          <p className="text-sm text-gray-400 mt-1">
            Pre-written prompts for common operations. Copy and paste into your agent's chat to run these tasks.
          </p>
        </div>

        <div className="space-y-3">
          {TASK_TEMPLATES.map(t => (
            <div key={t.label} className="bg-gray-800 border border-gray-700 rounded-lg p-4">
              <div className="flex items-center justify-between gap-3 mb-2">
                <h4 className="text-sm font-medium text-white">{t.label}</h4>
                <button
                  onClick={() => void copyToClipboard(t.prompt, t.label)}
                  className="shrink-0 text-xs px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded transition-colors"
                >
                  {copied === t.label ? 'Copied!' : 'Copy'}
                </button>
              </div>
              <pre className="text-xs text-gray-400 whitespace-pre-wrap font-mono leading-relaxed">{t.prompt}</pre>
            </div>
          ))}
        </div>
      </Section>

      {/* MCP Integration (future) */}
      <Section>
        <div>
          <h2 className="text-lg font-semibold text-white">MCP Server (Coming Soon)</h2>
          <p className="text-sm text-gray-400 mt-1">
            Connect Claude Code and Cursor directly to the LessonLens API via MCP (Model Context Protocol).
            Agents will get native tools like <code className="text-gray-300 bg-gray-800 px-1 rounded">get_session</code>,{' '}
            <code className="text-gray-300 bg-gray-800 px-1 rounded">add_annotation</code>,{' '}
            <code className="text-gray-300 bg-gray-800 px-1 rounded">generate_summary</code> without needing
            to know the REST API.
          </p>
        </div>

        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <p className="text-sm text-gray-500">
            MCP integration will be added in a future update. For now, use the context document
            and task templates above to configure your agents.
          </p>
        </div>
      </Section>
    </div>
  );
}
