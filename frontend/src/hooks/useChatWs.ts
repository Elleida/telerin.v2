// Hook para gestionar la conexión WebSocket de chat

import { useCallback, useEffect, useRef, useState } from 'react';
import { getToken } from '@/lib/auth';
import { ChatFinalResult, ChatSettings } from '@/lib/types';
import { normalizePngUrl } from '@/lib/api';

// WebSocket is proxied through the Next.js custom server (server.js), so we
// connect to the SAME origin and port as the page — works both direct (:8502)
// and via any nginx reverse proxy (no need for port 8000 to be reachable).
// server.js strips the basePath prefix and forwards to ws://localhost:8000/api/ws/*.
const _wsProto  = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const _wsOrigin = typeof window !== 'undefined'
  ? `${_wsProto}//${window.location.host}`   // same host + port as the page
  : (process.env.NEXT_PUBLIC_WS_URL ?? 'ws://localhost:8502');
const _basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
const WS_BASE   = _wsOrigin + _basePath; // e.g. ws://dihana.unizar.es/teleradio

type WsStatus = 'disconnected' | 'connecting' | 'authenticated' | 'error';
type StatusLabel = string;

interface UseChatWsReturn {
  status: WsStatus;
  statusLabel: StatusLabel;
  streamingText: string;
  sendChat: (query: string, settings: ChatSettings, imageContext?: string) => void;
  clearConv: () => void;
  disconnect: () => void;
  lastResult: ChatFinalResult | null;
  isStreaming: boolean;
}

export function useChatWs(onFinal: (result: ChatFinalResult) => void): UseChatWsReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<WsStatus>('disconnected');
  const [statusLabel, setStatusLabel] = useState<StatusLabel>('');
  const [streamingText, setStreamingText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [lastResult, setLastResult] = useState<ChatFinalResult | null>(null);
  const streamBufferRef = useRef<string[]>([]);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelayRef = useRef<number>(1000);
  const intentionalCloseRef = useRef<boolean>(false);

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState < 2) return; // already open/connecting

    setStatus('connecting');
    const ws = new WebSocket(`${WS_BASE}/ws/chat`);
    wsRef.current = ws;

    ws.onopen = () => {
      // Autenticar con JWT
      const token = getToken();
      ws.send(JSON.stringify({ type: 'auth', token }));
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        switch (msg.type) {
          case 'auth_ok':
            setStatus('authenticated');
            setStatusLabel('');
            reconnectDelayRef.current = 1000; // reset backoff en conexión exitosa
            break;
          case 'status':
            setStatusLabel(msg.label ?? '');
            break;
          case 'chunk':
            streamBufferRef.current.push(msg.text ?? '');
            setStreamingText(streamBufferRef.current.join(''));
            setIsStreaming(true);
            break;
          case 'cleared':
            setStreamingText('');
            streamBufferRef.current = [];
            setLastResult(null);
            break;
          case 'final': {
            const result: ChatFinalResult = {
              response:                msg.response ?? streamBufferRef.current.join(''),
              sources:                 (msg.sources ?? []).map((s: {png_url?: string; [k: string]: unknown}) => ({ ...s, png_url: normalizePngUrl(s.png_url) })),
              sql_queries:             msg.sql_queries ?? [],
              prompt_used:             msg.prompt_used,
              query_type:              msg.query_type,
              search_classification:   msg.search_classification,
              enhanced_query:          msg.enhanced_query,
              is_contextual_follow_up: msg.is_contextual_follow_up,
              elapsed_time:            msg.elapsed_time,
              search_time:             msg.search_time,
              db_search_time:          msg.db_search_time,
              reranking_time:          msg.reranking_time,
              response_time:           msg.response_time,
              prompt_tokens:           msg.prompt_tokens,
              response_tokens:         msg.response_tokens,
              error:                   msg.error,
              success:                 msg.success,
            };
            streamBufferRef.current = [];
            setStreamingText('');
            setIsStreaming(false);
            setStatusLabel('✅ Respuesta lista');
            setLastResult(result);
            onFinal(result);
            break;
          }
          case 'error':
            setStatusLabel(`❌ ${msg.message}`);
            setIsStreaming(false);
            break;
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onerror = () => {
      setStatus('error');
      setStatusLabel('Error de conexión WebSocket');
    };

    ws.onclose = () => {
      setStatus('disconnected');
      setIsStreaming(false);
      if (intentionalCloseRef.current) return;
      // Reconexión automática con backoff exponencial (máx 30s)
      const delay = reconnectDelayRef.current;
      reconnectDelayRef.current = Math.min(delay * 2, 30000);
      reconnectTimerRef.current = setTimeout(() => {
        if (!intentionalCloseRef.current) connect();
      }, delay);
    };
  }, [onFinal]);

  // Auto-connect on mount
  useEffect(() => {
    intentionalCloseRef.current = false;
    const token = getToken();
    if (token) connect();
    return () => {
      intentionalCloseRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const sendChat = useCallback(
    (query: string, settings: ChatSettings, imageContext?: string) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        connect();
        return;
      }
      reconnectDelayRef.current = 1000; // reset backoff al enviar con éxito
      streamBufferRef.current = [];
      setStreamingText('');
      setIsStreaming(true);
      setStatusLabel('🔎 Buscando información...');
      wsRef.current.send(
        JSON.stringify({
          type:                'chat',
          query,
          llm_backend:         settings.llm_backend,
          llm_model:           settings.llm_model || null,
          sql_limit:           settings.sql_limit,
          llm_score_threshold: settings.llm_score_threshold,
          image_context:       imageContext ?? null,
        }),
      );
    },
    [connect],
  );

  const clearConv = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'clear' }));
    }
    streamBufferRef.current = [];
    setStreamingText('');
    setLastResult(null);
    setStatusLabel('');
  }, []);

  const disconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    wsRef.current?.close();
  }, []);

  return { status, statusLabel, streamingText, sendChat, clearConv, disconnect, lastResult, isStreaming };
}
