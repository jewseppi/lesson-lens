import { useEffect, useState } from 'react';
import { apiJson } from '../api';

interface EvalSummary {
  sessions_total?: number;
  sessions_successful?: number;
  sessions_failed?: number;
  avg_latency?: number;
  avg_schema_valid?: number;
  avg_content_coverage?: number;
  avg_pedagogical_structure?: number;
  avg_hallucination_proxy?: number;
}

interface EvalRun {
  id: number;
  provider: string;
  model: string;
  language: string;
  dataset_name: string;
  session_count: number;
  status: string;
  summary: EvalSummary;
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
}

interface ScorecardEntry {
  provider: string;
  model: string;
  language: string;
  metrics: Record<string, number>;
  session_count: number;
  run_count: number;
}

interface EvalRunDetail extends EvalRun {
  scores_by_session: Record<string, Record<string, { value: number; meta: Record<string, unknown> }>>;
}

interface Policy {
  id: number;
  language: string;
  provider: string;
  model_pattern: string;
  enabled: boolean;
  min_score: number;
  warning_threshold: number;
  block_threshold: number;
  fallback_provider: string | null;
  fallback_model: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

const METRIC_LABELS: Record<string, string> = {
  schema_valid: 'Schema',
  content_coverage: 'Coverage',
  pedagogical_structure: 'Exercises',
  hallucination_proxy: 'Grounding',
  latency: 'Latency (s)',
};

const QUALITY_METRICS = ['schema_valid', 'content_coverage', 'pedagogical_structure', 'hallucination_proxy'];

type Tab = 'scorecard' | 'runs' | 'policies';

function ScoreBar({ value }: { value: number }) {
  const pct = Math.min(100, value * 100);
  const color = pct >= 80 ? 'bg-green-500' : pct >= 60 ? 'bg-yellow-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="flex-1 bg-gray-800 rounded-full h-2 min-w-[60px]">
        <div className={`h-2 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-400 w-12 text-right">{pct.toFixed(0)}%</span>
    </div>
  );
}

function HeatCell({ value }: { value: number | undefined }) {
  if (value === undefined) return <td className="p-2 text-center text-gray-700">—</td>;
  const pct = value * 100;
  const bg = pct >= 80 ? 'bg-green-900/60 text-green-300'
    : pct >= 60 ? 'bg-yellow-900/60 text-yellow-300'
    : 'bg-red-900/60 text-red-300';
  return <td className={`p-2 text-center text-xs font-mono rounded ${bg}`}>{pct.toFixed(0)}%</td>;
}

export default function EvalPage() {
  const [tab, setTab] = useState<Tab>('scorecard');
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [scorecard, setScorecard] = useState<ScorecardEntry[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [selectedRun, setSelectedRun] = useState<EvalRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const reload = () => {
    Promise.all([
      apiJson<EvalRun[]>('/api/eval/runs').catch(() => []),
      apiJson<ScorecardEntry[]>('/api/eval/scorecard').catch(() => []),
      apiJson<Policy[]>('/api/policies').catch(() => []),
    ]).then(([r, s, p]) => {
      setRuns(r);
      setScorecard(s);
      setPolicies(p);
    }).catch(() => setError('Failed to load evaluation data'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { reload(); }, []);

  const loadRunDetail = async (id: number) => {
    try {
      const detail = await apiJson<EvalRunDetail>(`/api/eval/runs/${id}`);
      setSelectedRun(detail);
    } catch {
      setError('Failed to load run details');
    }
  };

  if (loading) return <div className="text-gray-400">Loading evaluations...</div>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Model Evaluation</h1>
        <p className="text-sm text-gray-500 mt-1">Compare summary quality and manage generation policies.</p>
      </div>

      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">
          {error}
          <button onClick={() => setError('')} className="ml-2 text-red-400 hover:text-red-300">✕</button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-800 pb-px">
        {([['scorecard', 'Scorecard'], ['runs', 'Runs'], ['policies', 'Policies']] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              tab === key ? 'bg-gray-800 text-white border-b-2 border-indigo-500' : 'text-gray-400 hover:text-white'
            }`}
          >
            {label}
            {key === 'policies' && policies.length > 0 && (
              <span className="ml-1.5 text-xs bg-gray-700 px-1.5 py-0.5 rounded">{policies.length}</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'scorecard' && <ScorecardTab scorecard={scorecard} runs={runs} />}
      {tab === 'runs' && <RunsTab runs={runs} selectedRun={selectedRun} onSelect={loadRunDetail} />}
      {tab === 'policies' && <PoliciesTab policies={policies} onReload={reload} setError={setError} />}
    </div>
  );
}

function ScorecardTab({ scorecard, runs }: { scorecard: ScorecardEntry[]; runs: EvalRun[] }) {
  if (scorecard.length === 0 && runs.length === 0) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-3">
        <h2 className="font-semibold text-white">Run your first evaluation</h2>
        <p className="text-sm text-gray-400">
          Use the CLI to evaluate a model against your parsed sessions:
        </p>
        <pre className="bg-gray-950 border border-gray-800 rounded-lg p-3 text-sm text-green-300 overflow-x-auto">
          python scripts/eval_runner.py --provider ollama --model qwen2.5:7b --sessions 3
        </pre>
      </div>
    );
  }

  // Build heatmap data: rows = models, cols = metrics
  const models = scorecard.map(e => ({
    label: `${e.provider}/${e.model}`,
    language: e.language,
    metrics: e.metrics,
    sessions: e.session_count,
    runs: e.run_count,
  }));

  // Trend data: completed runs sorted by date
  const completedRuns = runs
    .filter(r => r.status === 'completed' && r.summary)
    .sort((a, b) => (a.started_at || '').localeCompare(b.started_at || ''));

  return (
    <div className="space-y-8">
      {/* Heatmap */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Quality Heatmap</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-400 text-left">
                <th className="pb-2 pr-4">Model</th>
                <th className="pb-2 pr-4">Lang</th>
                {QUALITY_METRICS.map(m => <th key={m} className="pb-2 px-2 text-center">{METRIC_LABELS[m]}</th>)}
                <th className="pb-2 px-2 text-center">Avg</th>
                <th className="pb-2 pr-4 text-right">Sessions</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m, i) => {
                const avg = QUALITY_METRICS.reduce((sum, k) => sum + (m.metrics[k] || 0), 0) / QUALITY_METRICS.length;
                return (
                  <tr key={i} className="border-b border-gray-900">
                    <td className="py-2 pr-4 font-medium text-white">{m.label}</td>
                    <td className="py-2 pr-4 text-gray-500">{m.language}</td>
                    {QUALITY_METRICS.map(k => <HeatCell key={k} value={m.metrics[k]} />)}
                    <HeatCell value={avg} />
                    <td className="py-2 pr-4 text-right text-gray-500">{m.sessions}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Trend */}
      {completedRuns.length > 1 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Run Trend</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-400 text-left">
                  <th className="pb-2 pr-4">Date</th>
                  <th className="pb-2 pr-4">Model</th>
                  {QUALITY_METRICS.map(m => <th key={m} className="pb-2 px-2 text-center">{METRIC_LABELS[m]}</th>)}
                  <th className="pb-2 px-2 text-center">Latency</th>
                  <th className="pb-2 text-right">✓/Total</th>
                </tr>
              </thead>
              <tbody>
                {completedRuns.map(run => (
                  <tr key={run.id} className="border-b border-gray-900">
                    <td className="py-2 pr-4 text-gray-400 text-xs">{(run.started_at || '').split(' ')[0]}</td>
                    <td className="py-2 pr-4 text-white">{run.provider}/{run.model}</td>
                    {QUALITY_METRICS.map(m => {
                      const key = `avg_${m}` as keyof EvalSummary;
                      return <HeatCell key={m} value={run.summary[key] as number | undefined} />;
                    })}
                    <td className="py-2 px-2 text-center text-gray-400 text-xs">
                      {run.summary.avg_latency?.toFixed(1)}s
                    </td>
                    <td className="py-2 text-right text-gray-500 text-xs">
                      {run.summary.sessions_successful}/{run.summary.sessions_total}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Failure breakdown */}
      {completedRuns.some(r => (r.summary.sessions_failed ?? 0) > 0) && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Failure Breakdown</h2>
          <div className="space-y-2">
            {completedRuns.filter(r => (r.summary.sessions_failed ?? 0) > 0).map(run => (
              <div key={run.id} className="bg-red-950/20 border border-red-900/40 rounded-lg p-3">
                <div className="flex justify-between items-center">
                  <span className="text-sm text-white">{run.provider}/{run.model}</span>
                  <span className="text-xs text-red-400">
                    {run.summary.sessions_failed} of {run.summary.sessions_total} failed
                  </span>
                </div>
                {run.error_message && <div className="text-xs text-red-400/80 mt-1">{run.error_message}</div>}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function RunsTab({ runs, selectedRun, onSelect }: {
  runs: EvalRun[];
  selectedRun: EvalRunDetail | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="space-y-6">
      {runs.length === 0 && <p className="text-gray-500 text-sm">No evaluation runs yet.</p>}
      {runs.length > 0 && (
        <div className="space-y-2">
          {runs.map(run => (
            <div
              key={run.id}
              onClick={() => onSelect(run.id)}
              className={`bg-gray-900 border rounded-lg p-4 cursor-pointer transition-colors ${
                selectedRun?.id === run.id ? 'border-indigo-600' : 'border-gray-800 hover:border-gray-700'
              }`}
            >
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <span className="text-indigo-400">{run.provider}</span>
                  <span className="text-gray-500">/</span>
                  <span className="font-medium">{run.model}</span>
                  <span className="text-gray-500 text-sm ml-2">({run.language})</span>
                </div>
                <div className="flex items-center gap-3 text-sm">
                  <StatusBadge status={run.status} />
                  <span className="text-gray-500">{run.session_count} sessions</span>
                  <span className="text-gray-600 text-xs">{(run.started_at || '').split(' ')[0]}</span>
                </div>
              </div>
              {run.status === 'completed' && run.summary && (
                <div className="mt-2 flex gap-4 text-xs text-gray-400 flex-wrap">
                  {QUALITY_METRICS.map(m => {
                    const key = `avg_${m}` as keyof EvalSummary;
                    const val = run.summary[key] as number | undefined;
                    return <span key={m}>{METRIC_LABELS[m]}: {val !== undefined ? `${(val * 100).toFixed(0)}%` : '—'}</span>;
                  })}
                  <span>Latency: {run.summary.avg_latency?.toFixed(1)}s</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {selectedRun && (
        <section>
          <h2 className="text-lg font-semibold mb-3">
            Run #{selectedRun.id}: {selectedRun.provider}/{selectedRun.model}
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-400 text-left">
                  <th className="pb-2 pr-4">Session</th>
                  {Object.entries(METRIC_LABELS).map(([k, label]) => (
                    <th key={k} className="pb-2 pr-4">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(selectedRun.scores_by_session).map(([sid, metrics]) => (
                  <tr key={sid} className="border-b border-gray-900">
                    <td className="py-2 pr-4 text-gray-300 font-mono text-xs">{sid}</td>
                    {QUALITY_METRICS.map(metric => (
                      <td key={metric} className="py-2 pr-4 min-w-[120px]">
                        {metrics[metric] ? <ScoreBar value={metrics[metric].value} /> : <span className="text-gray-600">—</span>}
                      </td>
                    ))}
                    <td className="py-2 pr-4 text-gray-400">
                      {metrics.latency ? `${metrics.latency.value.toFixed(1)}s` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function PoliciesTab({ policies, onReload, setError }: {
  policies: Policy[];
  onReload: () => void;
  setError: (msg: string) => void;
}) {
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({
    language: 'zh', provider: 'ollama', model_pattern: '*',
    warning_threshold: '0.6', block_threshold: '0.3',
    fallback_provider: '', fallback_model: '', notes: '',
  });
  const [saving, setSaving] = useState(false);

  const handleCreate = async () => {
    setSaving(true);
    try {
      await apiJson('/api/policies', {
        method: 'POST',
        body: JSON.stringify({
          language: form.language,
          provider: form.provider,
          model_pattern: form.model_pattern,
          warning_threshold: parseFloat(form.warning_threshold),
          block_threshold: parseFloat(form.block_threshold),
          fallback_provider: form.fallback_provider || null,
          fallback_model: form.fallback_model || null,
          notes: form.notes || null,
        }),
      });
      setShowAdd(false);
      onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create policy');
    } finally {
      setSaving(false);
    }
  };

  const togglePolicy = async (id: number, enabled: boolean) => {
    try {
      await apiJson(`/api/policies/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled }),
      });
      onReload();
    } catch {
      setError('Failed to update policy');
    }
  };

  const deletePolicy = async (id: number) => {
    try {
      await apiJson(`/api/policies/${id}`, { method: 'DELETE' });
      onReload();
    } catch {
      setError('Failed to delete policy');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <p className="text-sm text-gray-400">
          Policies control which model/language combinations are allowed, warned, or blocked during generation.
        </p>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium flex-shrink-0"
        >
          {showAdd ? 'Cancel' : '+ Add Policy'}
        </button>
      </div>

      {showAdd && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-gray-400 block mb-1">Language</label>
              <input value={form.language} onChange={e => setForm({ ...form, language: e.target.value })}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white" />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Provider</label>
              <select value={form.provider} onChange={e => setForm({ ...form, provider: e.target.value })}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white">
                <option value="ollama">ollama</option>
                <option value="openai_compatible_local">openai_compatible_local</option>
                <option value="openai">openai</option>
                <option value="anthropic">anthropic</option>
                <option value="gemini">gemini</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Model Pattern</label>
              <input value={form.model_pattern} onChange={e => setForm({ ...form, model_pattern: e.target.value })}
                placeholder="* or qwen*:7b*"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white" />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Warn below</label>
              <input type="number" step="0.1" min="0" max="1" value={form.warning_threshold}
                onChange={e => setForm({ ...form, warning_threshold: e.target.value })}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white" />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Block below</label>
              <input type="number" step="0.1" min="0" max="1" value={form.block_threshold}
                onChange={e => setForm({ ...form, block_threshold: e.target.value })}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white" />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Notes</label>
              <input value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-gray-400 block mb-1">Fallback Provider</label>
              <input value={form.fallback_provider} onChange={e => setForm({ ...form, fallback_provider: e.target.value })}
                placeholder="e.g. openai"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white" />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Fallback Model</label>
              <input value={form.fallback_model} onChange={e => setForm({ ...form, fallback_model: e.target.value })}
                placeholder="e.g. gpt-4o-mini"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white" />
            </div>
          </div>
          <button onClick={handleCreate} disabled={saving}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium">
            {saving ? 'Creating...' : 'Create Policy'}
          </button>
        </div>
      )}

      {policies.length === 0 && !showAdd && (
        <p className="text-gray-600 text-sm py-4">No policies configured. All model/language combinations are allowed.</p>
      )}

      {policies.length > 0 && (
        <div className="space-y-2">
          {policies.map(p => (
            <div key={p.id} className={`bg-gray-900 border rounded-lg p-4 ${p.enabled ? 'border-gray-800' : 'border-gray-800 opacity-50'}`}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <span className="font-medium text-white">{p.provider}</span>
                  <span className="text-gray-500">/</span>
                  <span className="text-indigo-400">{p.model_pattern}</span>
                  <span className="text-gray-500 text-sm ml-2">({p.language})</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-yellow-400">warn &lt;{(p.warning_threshold * 100).toFixed(0)}%</span>
                  <span className="text-xs text-red-400">block &lt;{(p.block_threshold * 100).toFixed(0)}%</span>
                  {p.fallback_provider && (
                    <span className="text-xs text-gray-500">→ {p.fallback_provider}/{p.fallback_model}</span>
                  )}
                  <button onClick={() => togglePolicy(p.id, !p.enabled)}
                    className={`text-xs px-2 py-1 rounded ${p.enabled ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-400'}`}>
                    {p.enabled ? 'Enabled' : 'Disabled'}
                  </button>
                  <button onClick={() => deletePolicy(p.id)}
                    className="text-xs text-red-500 hover:text-red-400 px-1">✕</button>
                </div>
              </div>
              {p.notes && <div className="text-xs text-gray-500 mt-1">{p.notes}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: 'bg-green-900 text-green-300',
    running: 'bg-blue-900 text-blue-300',
    pending: 'bg-yellow-900 text-yellow-300',
    failed: 'bg-red-900 text-red-300',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${colors[status] || 'bg-gray-700 text-gray-400'}`}>
      {status}
    </span>
  );
}
