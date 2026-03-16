import { useState } from 'react';
import { Link } from 'react-router-dom';

function CopyBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <div className="space-y-1">
      <div className="text-sm text-gray-400">{label}</div>
      <div className="relative group">
        <pre className="bg-gray-950 border border-gray-800 rounded-lg p-3 pr-16 text-sm text-green-300 overflow-x-auto whitespace-pre-wrap break-all">
          {text}
        </pre>
        <button
          onClick={copy}
          className="absolute top-2 right-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs px-2 py-1 rounded transition-colors"
        >
          {copied ? '✓ Copied' : 'Copy'}
        </button>
      </div>
    </div>
  );
}

export default function SetupPage() {
  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Getting Started</h1>
        <p className="text-gray-400 mt-1">Set up LessonLens on your local machine.</p>
      </div>

      {/* Step 1 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <span className="bg-indigo-600 text-white w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold">1</span>
          Clone the repository
        </h2>
        <CopyBlock
          label="Clone and enter the project directory"
          text="git clone https://github.com/sharonhsu/language.git && cd language"
        />
      </section>

      {/* Step 2 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <span className="bg-indigo-600 text-white w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold">2</span>
          Install dependencies
        </h2>
        <CopyBlock
          label="Python backend"
          text={"python3 -m venv .venv && source .venv/bin/activate\npip install -r requirements.txt"}
        />
        <CopyBlock
          label="Node frontend"
          text="cd web && npm install && cd .."
        />
      </section>

      {/* Step 3 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <span className="bg-indigo-600 text-white w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold">3</span>
          Start the app
        </h2>
        <CopyBlock
          label="Run the Flask API server"
          text="source .venv/bin/activate && python api/app.py"
        />
        <CopyBlock
          label="In a separate terminal, start the frontend dev server"
          text="cd web && npm run dev"
        />
        <p className="text-sm text-gray-500">
          The app will be available at <code className="text-gray-300">http://localhost:5173</code> (frontend) and
          the API at <code className="text-gray-300">http://localhost:5001</code>.
        </p>
      </section>

      {/* Step 4 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <span className="bg-indigo-600 text-white w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold">4</span>
          Optional: Local LLM with Ollama
        </h2>
        <p className="text-sm text-gray-400">
          For local AI summarization without cloud API keys, install Ollama and pull a model:
        </p>
        <CopyBlock
          label="Install and run Ollama"
          text={"# Install from https://ollama.com\nollama pull qwen2.5:7b\nollama serve"}
        />
        <p className="text-sm text-gray-500">
          Then select <strong className="text-gray-300">Ollama</strong> as your provider in{' '}
          <Link to="/settings" className="text-indigo-400 hover:text-indigo-300">Settings</Link>.
          7B+ models are recommended for quality summaries.
        </p>
      </section>

      {/* Step 5 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <span className="bg-indigo-600 text-white w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold">5</span>
          Upload your first chat export
        </h2>
        <p className="text-sm text-gray-400">
          Export your lesson chat from LINE, WhatsApp, or any messaging app as a text file,
          then upload it to start parsing.
        </p>
        <Link
          to="/upload"
          className="inline-block bg-indigo-600 hover:bg-indigo-700 text-white px-5 py-2.5 rounded-lg font-medium transition-colors"
        >
          Go to Upload
        </Link>
      </section>
    </div>
  );
}
