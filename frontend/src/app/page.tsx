'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { isAuthenticated, getUsername, clearToken } from '@/lib/auth';
import { apiClearSession } from '@/lib/api';
import { ChatFinalResult, ChatSettings } from '@/lib/types';

import Sidebar from '@/components/Sidebar';
import ChatPanel from '@/components/ChatPanel';
import DebugPanel from '@/components/DebugPanel';
import ContextPanel from '@/components/ContextPanel';
import ImageSearchTab from '@/components/ImageSearchTab';

const DEFAULT_SETTINGS: ChatSettings = {
  llm_backend: 'ollama',
  llm_model: process.env.NEXT_PUBLIC_DEFAULT_LLM_MODEL ?? '',
  sql_limit: 60,
  llm_score_threshold: 0.5,
};

type Tab = 'chat' | 'images';
type RightPanel = 'debug' | 'context';

export default function HomePage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [settings, setSettings] = useState<ChatSettings>(DEFAULT_SETTINGS);
  const [activeTab, setActiveTab] = useState<Tab>('chat');
  const [rightPanel, setRightPanel] = useState<RightPanel>('debug');
  const [lastResult, setLastResult] = useState<ChatFinalResult | null>(null);
  const [contextRefresh, setContextRefresh] = useState(0);
  const [imageContext, setImageContext] = useState<string | undefined>(undefined);
  const [username, setUsername] = useState<string | null>(null);
  const [clearKey, setClearKey] = useState(0);

  // Guard de autenticación (client-side)
  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace('/login');
    } else {
      setUsername(getUsername());
      setReady(true);
    }
  }, [router]);

  const handleLogout = () => {
    clearToken();
    router.replace('/login');
  };

  const handleNewResult = (result: ChatFinalResult) => {
    setLastResult(result);
    setContextRefresh((n) => n + 1);
  };

  const handleClearConv = async () => {
    try {
      await apiClearSession();
    } catch {
      // ignorar
    }
    setLastResult(null);
    setContextRefresh((n) => n + 1);
    setClearKey((k) => k + 1);  // signals ChatPanel to call clearConv()
  };

  if (!ready) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <span className="text-gray-400 animate-pulse">Cargando...</span>
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* ── Sidebar ────────────────────────────────────────────────────── */}
      <Sidebar
        settings={settings}
        onChange={setSettings}
        username={username ?? undefined}
        onLogout={handleLogout}
        onClearConv={handleClearConv}
      />

      {/* ── Contenido principal ─────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {/* Header */}
        <header className="bg-white border-b px-6 py-3 flex items-center gap-4 shrink-0">
          <h1 className="text-2xl font-bold brand-gradient">📺 TELERÍN</h1>
          <p className="text-sm text-gray-400 hidden sm:block">
            Memoria de RTVE “Reviviendo el papel de 1958 a 1965”
          </p>

          {/* Tabs principales */}
          <div className="ml-auto flex gap-1 bg-gray-100 p-1 rounded-lg">
            {(['chat', 'images'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setActiveTab(t)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition ${
                  activeTab === t
                    ? 'bg-white shadow text-gray-800'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {t === 'chat' ? '💬 Conversación' : '🖼️ Imágenes'}
              </button>
            ))}
          </div>
        </header>

        {/* Body: panel central + panel derecho */}
        <div className="flex flex-1 min-h-0">
          {/* Centro */}
          <main className="flex-1 min-w-0 overflow-hidden flex flex-col">
            <div className={activeTab === 'chat' ? 'flex flex-col flex-1 min-h-0' : 'hidden'}>
              <ChatPanel
                settings={settings}
                imageContext={imageContext}
                onClearImageContext={() => setImageContext(undefined)}
                onNewResult={handleNewResult}
                onClear={handleClearConv}
                clearKey={clearKey}
              />
            </div>
            <div className={activeTab === 'images' ? 'flex-1 overflow-y-auto thin-scrollbar p-4' : 'hidden'}>
              <ImageSearchTab
                settings={settings}
                clearKey={clearKey}
                onImageContext={(desc) => {
                  setImageContext(desc);
                  setActiveTab('chat');
                }}
              />
            </div>
          </main>

          {/* Panel derecho */}
          <aside className="w-80 shrink-0 border-l bg-white flex flex-col overflow-hidden">
            {/* Tabs del panel derecho */}
            <div className="flex border-b shrink-0">
              {(['debug', 'context'] as RightPanel[]).map((p) => (
                <button
                  key={p}
                  onClick={() => setRightPanel(p)}
                  className={`flex-1 py-2 text-xs font-medium transition ${
                    rightPanel === p
                      ? 'border-b-2 border-orange-500 text-orange-600'
                      : 'text-gray-400 hover:text-gray-600'
                  }`}
                >
                  {p === 'debug' ? '🔍 Debug' : '🧠 Contexto'}
                </button>
              ))}
            </div>

            <div className="flex-1 overflow-y-auto thin-scrollbar p-3">
              {rightPanel === 'debug' ? (
                <DebugPanel result={lastResult} />
              ) : (
                <ContextPanel refreshTrigger={contextRefresh} />
              )}
            </div>
          </aside>
        </div>

        {/* Footer */}
        <footer className="bg-white border-t px-6 py-2 text-xs text-gray-400 text-center shrink-0">
          💾 CrateDB · 🧭 LangGraph · 🧠 {settings.llm_backend} ({settings.llm_model || 'modelo .env'}) · 🎯 FastAPI + Next.js
        </footer>
      </div>
    </div>
  );
}
