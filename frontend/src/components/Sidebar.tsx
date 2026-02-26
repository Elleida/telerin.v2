'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ChatSettings, LlmBackend } from '@/lib/types';
import { getUserRole } from '@/lib/auth';
import { apiGetModels, ModelInfo } from '@/lib/api';

interface SidebarProps {
  settings: ChatSettings;
  onChange: (s: ChatSettings) => void;
  username?: string;
  onLogout: () => void;
  onClearConv: () => void;
}

export default function Sidebar({ settings, onChange, username, onLogout, onClearConv }: SidebarProps) {
  const update = <K extends keyof ChatSettings>(key: K, value: ChatSettings[K]) =>
    onChange({ ...settings, [key]: value });
  const router = useRouter();
  const [isAdmin, setIsAdmin] = useState(false);
  const [ollamaModels, setOllamaModels] = useState<ModelInfo[]>([]);

  useEffect(() => { setIsAdmin(getUserRole() === 'admin'); }, []);

  // Cargar modelos Ollama disponibles al montar / cuando cambia a ollama
  useEffect(() => {
    if (settings.llm_backend !== 'ollama') return;
    apiGetModels()
      .then((data) => {
        const ALLOWED = /^(qwen|glm|gemma3)/i;
        const filtered = (data.ollama ?? []).filter((m) => ALLOWED.test(m.name));
        setOllamaModels(filtered);
        // Si el modelo actual no está en la lista, seleccionar el primero
        if (filtered.length && !filtered.find((m) => m.name === settings.llm_model)) {
          onChange({ ...settings, llm_model: filtered[0].name });
        }
      })
      .catch(() => setOllamaModels([]));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.llm_backend]);

  return (
    <aside className="w-72 shrink-0 bg-white border-r flex flex-col h-full overflow-y-auto thin-scrollbar">
      {/* Cabecera */}
      <div className="p-4 border-b">
        <h2 className="font-bold text-gray-700">⚙️ Sistema Multi-Agente</h2>
        {username && (
          <p className="text-xs text-gray-400 mt-1">
            👤 {username}{' '}
            <button
              onClick={onLogout}
              className="text-red-400 hover:underline ml-1"
            >
              (salir)
            </button>
          </p>
        )}
        {isAdmin && (
          <button
            onClick={() => router.push('/admin')}
            className="mt-2 w-full text-left text-xs text-purple-600 hover:text-purple-800 hover:underline"
          >
            🛠️ Panel de administración
          </button>
        )}
      </div>

      <div className="p-4 space-y-6 flex-1">

        {/* Limpiar conversación */}
        <section>
          <h3 className="text-sm font-semibold text-gray-600 mb-2">🗑️ Conversación</h3>
          <button
            onClick={onClearConv}
            className="w-full border border-gray-300 text-gray-600 text-sm py-1.5 rounded-lg hover:bg-red-50 hover:border-red-300 hover:text-red-600 transition"
          >
            🔄 Borrar Todo
          </button>
        </section>

        {/* Ajustes de búsqueda */}
        <section>
          <h3 className="text-sm font-semibold text-gray-600 mb-3">🔧 Búsqueda</h3>

          <label className="block text-xs text-gray-500 mb-1">
            Límite SQL por consulta: <strong>{settings.sql_limit}</strong>
          </label>
          <input
            type="range" min={5} max={200} step={5}
            value={settings.sql_limit}
            onChange={(e) => update('sql_limit', Number(e.target.value))}
            className="w-full accent-orange-500"
          />

          <label className="block text-xs text-gray-500 mt-3 mb-1">
            Umbral score LLM: <strong>{settings.llm_score_threshold.toFixed(2)}</strong>
          </label>
          <input
            type="range" min={0} max={1} step={0.01}
            value={settings.llm_score_threshold}
            onChange={(e) => update('llm_score_threshold', Number(e.target.value))}
            className="w-full accent-orange-500"
          />
        </section>

        {/* LLM Backend */}
        <section>
          <h3 className="text-sm font-semibold text-gray-600 mb-3">🧠 LLM Backend</h3>

          <div className="flex gap-2 mb-3">
            {(['ollama', 'gemini'] as LlmBackend[]).map((b) => (
              <button
                key={b}
                onClick={() => {
                  const defaultModel = b === 'gemini'
                    ? (process.env.NEXT_PUBLIC_DEFAULT_GEMINI_MODEL ?? 'gemini-3-flash-preview')
                    : (ollamaModels[0]?.name ?? process.env.NEXT_PUBLIC_DEFAULT_LLM_MODEL ?? '');
                  onChange({ ...settings, llm_backend: b, llm_model: defaultModel });
                }}
                className={`flex-1 py-1.5 text-sm rounded-lg border transition ${
                  settings.llm_backend === b
                    ? 'bg-orange-500 text-white border-orange-500'
                    : 'border-gray-300 text-gray-600 hover:border-orange-300'
                }`}
              >
                {b === 'ollama' ? '🦙 Ollama' : '✨ Gemini'}
              </button>
            ))}
          </div>

          {settings.llm_backend === 'ollama' && ollamaModels.length > 0 ? (
            <>
              <label className="block text-xs text-gray-500 mb-1">Modelo</label>
              <select
                value={settings.llm_model}
                onChange={(e) => update('llm_model', e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 bg-white"
              >
                {ollamaModels.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name}{m.size ? ` (${(m.size / 1e9).toFixed(1)} GB)` : ''}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <>
              <label className="block text-xs text-gray-500 mb-1">Modelo{settings.llm_backend === 'ollama' ? ' (cargando...)' : ' (opcional)'}</label>
              <input
                type="text"
                value={settings.llm_model}
                onChange={(e) => update('llm_model', e.target.value)}
                placeholder={settings.llm_backend === 'gemini' ? 'gemini-...' : (process.env.NEXT_PUBLIC_DEFAULT_LLM_MODEL ?? 'llama3.2')}
                className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
              />
            </>
          )}
        </section>
      </div>

      {/* Footer info */}
      <div className="p-3 border-t text-xs text-gray-400 space-y-0.5">
        <p>📦 LangGraph · CrateDB · FastAPI</p>
        <p>🌐 Backend: {process.env.NEXT_PUBLIC_API_URL ?? 'localhost:8000'}</p>
      </div>
    </aside>
  );
}
