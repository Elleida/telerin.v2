'use client';

import { useState } from 'react';
import { ChatFinalResult, SourceInfo, SqlQueryInfo } from '@/lib/types';
import ImageLightbox from '@/components/ImageLightbox';

interface DebugPanelProps {
  result: ChatFinalResult | null;
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3 text-center">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="font-semibold text-gray-800 mt-0.5">{value}</p>
    </div>
  );
}

export default function DebugPanel({ result }: DebugPanelProps) {
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);

  if (!result) {
    return (
      <div className="p-4 text-sm text-gray-400 text-center mt-8">
        Aquí aparecerán las fuentes, SQL y métricas tras la primera respuesta.
      </div>
    );
  }

  const sources: SourceInfo[] = result.sources ?? [];
  const sqlQueries: SqlQueryInfo[] = result.sql_queries ?? [];

  return (
    <>
    {lightboxSrc && <ImageLightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}
    <div className="space-y-4 text-sm">
      {/* Métricas */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <MetricCard label="Fuentes" value={String(sources.length)} />
        <MetricCard label="🔍 BD" value={`${(result.db_search_time ?? 0).toFixed(2)}s`} />
        <MetricCard label="🔄 Reranking" value={`${(result.reranking_time ?? 0).toFixed(2)}s`} />
        <MetricCard label="💬 Respuesta" value={`${(result.response_time ?? 0).toFixed(2)}s`} />
        <MetricCard
          label="⏱️ Total"
          value={`${((result.search_time ?? 0) + (result.response_time ?? 0)).toFixed(2)}s`}
        />
        <MetricCard
          label="Estado"
          value={result.success !== false ? '✅ OK' : '❌ Error'}
        />
      </div>

      {/* Tipo de query */}
      {result.query_type && (
        <div className="flex gap-2 flex-wrap">
          <span className="bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full text-xs">
            {result.query_type}
          </span>
          {result.search_classification && (
            <span className="bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full text-xs">
              {typeof result.search_classification === 'object'
                ? JSON.stringify(result.search_classification)
                : result.search_classification}
            </span>
          )}
          {result.is_contextual_follow_up && (
            <span className="bg-green-100 text-green-700 px-2 py-0.5 rounded-full text-xs">
              follow-up contextual
            </span>
          )}
        </div>
      )}

      {/* Fuentes */}
      {sources.length > 0 && (
        <details open>
          <summary className="cursor-pointer font-medium text-gray-600 mb-2">
            📚 Fuentes ({sources.length})
          </summary>
          <div className="space-y-2 max-h-64 overflow-y-auto thin-scrollbar pr-1">
            {sources.map((s, i) => {
              const rel = s.score ?? s.similarity ?? s.relevance;
              return (
                <div key={i} className="bg-gray-50 border border-gray-200 rounded-lg p-2 text-xs">
                  <p className="font-semibold truncate">{s.title ?? `Documento ${i + 1}`}</p>
                  <div className="text-gray-500 mt-0.5 space-x-2">
                    <span>📖 {s.magazine_id ?? 'N/A'}</span>
                    <span>Pág. {s.page_number ?? '-'}</span>
                    {s.date && <span>📅 {s.date}</span>}
                    {rel != null && <span>⭐ {Number(rel).toFixed(3)}</span>}
                  </div>
                  {s.png_url && (
                    <button
                      onClick={() => setLightboxSrc(s.png_url!)}
                      className="text-blue-500 hover:underline text-left"
                    >
                      🖼️ Ver página
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </details>
      )}

      {/* SQL Queries */}
      {sqlQueries.length > 0 && (
        <details>
          <summary className="cursor-pointer font-medium text-gray-600 mb-2">
            🗄️ SQL ejecutado ({sqlQueries.length})
          </summary>
          <div className="space-y-2">
            {sqlQueries.map((q, i) => (
              <div key={i}>
                <p className="text-xs text-gray-400 mb-0.5">
                  Query {i + 1} — tabla: <code>{q.table ?? 'N/A'}</code>
                </p>
                <pre className="bg-gray-100 rounded-lg p-2 text-xs overflow-x-auto whitespace-pre-wrap">
                  {q.sql}
                </pre>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Prompt */}
      {result.prompt_used && (
        <details>
          <summary className="cursor-pointer font-medium text-gray-600 mb-2">
            🔍 Prompt enviado al LLM ({result.prompt_used.length} chars)
          </summary>
          <pre className="bg-gray-100 rounded-lg p-2 text-xs overflow-x-auto max-h-48 overflow-y-auto thin-scrollbar whitespace-pre-wrap">
            {result.prompt_used}
          </pre>
        </details>
      )}

      {/* Error */}
      {result.error && (
        <div className="bg-red-50 border-l-4 border-red-400 text-red-700 p-3 rounded text-xs">
          ❌ {result.error}
        </div>
      )}
    </div>
    </>
  );
}
