'use client';

import { useEffect, useState } from 'react';
import { SessionContext } from '@/lib/types';
import { apiGetContext } from '@/lib/api';

interface ContextPanelProps {
  refreshTrigger?: number; // se incrementa externamente para forzar refresh
}

export default function ContextPanel({ refreshTrigger = 0 }: ContextPanelProps) {
  const [ctx, setCtx] = useState<SessionContext | null>(null);

  useEffect(() => {
    apiGetContext()
      .then(setCtx)
      .catch(() => setCtx(null));
  }, [refreshTrigger]);

  if (!ctx || ctx.num_turns === 0) {
    return (
      <div className="p-4 text-sm text-gray-400 text-center">
        💭 No hay conversación activa aún
      </div>
    );
  }

  const summary = ctx.context_summary as Record<string, unknown>;
  const entities = ctx.global_entities as Record<string, unknown>;

  return (
    <div className="space-y-3 text-sm p-2">
      {/* Métricas rápidas */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-orange-50 rounded-lg p-2 text-center">
          <p className="text-xs text-gray-500">💬 Turnos</p>
          <p className="font-bold text-gray-800">{ctx.num_turns}</p>
        </div>
        <div className="bg-amber-50 rounded-lg p-2 text-center">
          <p className="text-xs text-gray-500">📅 Periodo</p>
          <p className="font-semibold text-gray-700 text-xs truncate">
            {(summary.time_period as string) ?? 'N/A'}
          </p>
        </div>
      </div>

      {/* Foco */}
      {summary.conversation_focus && (
        <div className="bg-gray-50 rounded-lg p-2">
          <p className="text-xs text-gray-400 mb-0.5">🎯 Foco</p>
          <p className="text-xs text-gray-700">{String(summary.conversation_focus)}</p>
        </div>
      )}

      {/* Temas */}
      {Array.isArray(summary.main_topics) && (summary.main_topics as string[]).length > 0 && (
        <div>
          <p className="text-xs text-gray-400 mb-1">🏷️ Temas</p>
          <div className="flex flex-wrap gap-1">
            {(summary.main_topics as string[]).slice(0, 4).map((t) => (
              <span key={t} className="bg-blue-100 text-blue-700 text-xs px-2 py-0.5 rounded-full">
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Útimo turno */}
      {ctx.last_turn && (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-gray-500 mb-1">
            Último turno
          </summary>
          <div className="text-xs bg-gray-50 rounded-lg p-2 space-y-1">
            <p><span className="text-gray-400">Usuario:</span> {ctx.last_turn.user_query}</p>
            {ctx.last_turn.enhanced_query && (
              <p><span className="text-gray-400">Mejorado:</span> {ctx.last_turn.enhanced_query}</p>
            )}
            <p><span className="text-gray-400">Tipo:</span> {ctx.last_turn.query_type}</p>
          </div>
        </details>
      )}

      {/* Entidades */}
      {Object.keys(entities).length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-gray-500 mb-1">
            Entidades globales
          </summary>
          <pre className="text-xs bg-gray-100 rounded-lg p-2 overflow-x-auto">
            {JSON.stringify(entities, null, 2)}
          </pre>
        </details>
      )}

      {/* Búsquedas recientes */}
      {ctx.recent_searches.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-gray-500 mb-1">
            Búsquedas recientes
          </summary>
          <ul className="text-xs space-y-0.5 list-disc list-inside text-gray-600">
            {ctx.recent_searches.slice(0, 5).map((s, i) => (
              <li key={i} className="truncate">{s.query}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
