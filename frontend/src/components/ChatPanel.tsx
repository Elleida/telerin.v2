'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import clsx from 'clsx';
import { apiFeedback } from '@/lib/api';
import { ChatFinalResult, ChatMessage, ChatSettings } from '@/lib/types';
import { useChatWs } from '@/hooks/useChatWs';

const GREETING = `🎉 ¡Hola! Soy **TELERÍN** 📻, tu asistente inteligente de búsqueda en el archivo histórico de **TeleRadio** (1958-1965).

¿Cómo puedo ayudarte? Puedo:
- 📺 Buscar programas de televisión o radio
- 📅 Encontrar contenido por fecha, canal o año
- 📝 Buscar artículos por tema
- 🖼️ Buscar imágenes similares
- 📊 Proporcionarte información sobre la historia de la TV española

Cuéntame, ¿qué te gustaría buscar?`;

interface ChatPanelProps {
  settings: ChatSettings;
  imageContext?: string;
  onClearImageContext?: () => void;
  onNewResult?: (result: ChatFinalResult) => void;
  onClear?: () => void;
  clearKey?: number;  // increment to trigger clear from parent
}

let _msgCounter = 0;
const nextId = () => `msg-${++_msgCounter}`;

// Hace que todos los enlaces del markdown se abran en pestaña nueva
const mdComponents = {
  a: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>
  ),
};

export default function ChatPanel({
  settings,
  imageContext,
  onClearImageContext,
  onNewResult,
  onClear,
  clearKey,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 'greeting', role: 'assistant', content: GREETING },
  ]);
  const [input, setInput] = useState('');
const [feedbackGiven, setFeedbackGiven] = useState<Record<string, 'up' | 'down'>>({});
  const [pendingCommentId, setPendingCommentId] = useState<string | null>(null);
  const [commentText, setCommentText]         = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  const handleFinal = useCallback((result: ChatFinalResult) => {
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: 'assistant', content: result.response, result },
    ]);
    onNewResult?.(result);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { status, statusLabel, streamingText, sendChat, clearConv, isStreaming } =
    useChatWs(handleFinal);

  // Trigger clear when parent increments clearKey
  useEffect(() => {
    if (clearKey === undefined || clearKey === 0) return;
    setMessages([{ id: 'greeting', role: 'assistant', content: GREETING }]);
    clearConv();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearKey]);

  // Scroll to bottom on new message or streaming update
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingText, statusLabel]);

  const handleSend = () => {
    const q = input.trim();
    if (!q || isStreaming) return;

    const userMsg: ChatMessage = { id: nextId(), role: 'user', content: q };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');

    sendChat(q, settings, imageContext);
    onClearImageContext?.();
  };

  const handleClear = () => {
    clearConv();
    setMessages([{ id: 'greeting', role: 'assistant', content: GREETING }]);
    setFeedbackGiven({});
    setPendingCommentId(null);
    setCommentText('');
    onClear?.();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const sendFeedback = async (msg: ChatMessage, allMsgs: ChatMessage[], rating: 'up' | 'down', comment = '') => {
    const idx = allMsgs.findIndex((m) => m.id === msg.id);
    const query = idx > 0 ? allMsgs[idx - 1].content : '';
    try {
      await apiFeedback({
        query,
        response: msg.content,
        rating,
        comment,
        db_search_time: msg.result?.db_search_time ?? 0,
        reranking_time: msg.result?.reranking_time ?? 0,
        response_time: msg.result?.response_time ?? 0,
        num_sources: msg.result?.sources?.length ?? 0,
        llm_model: `${settings.llm_backend}/${settings.llm_model}`,
        prompt_tokens: msg.result?.prompt_tokens ?? 0,
        response_tokens: msg.result?.response_tokens ?? 0,
      });
    } catch {
      // silencioso — el log de backend ya captura errores
    }
  };

  const handleFeedback = async (msg: ChatMessage, allMsgs: ChatMessage[], rating: 'up' | 'down') => {
    if (feedbackGiven[msg.id]) return; // ya valorado
    if (rating === 'down') {
      // Mostrar cajetín de comentario antes de enviar
      setPendingCommentId(msg.id);
      setCommentText('');
    } else {
      setFeedbackGiven((prev) => ({ ...prev, [msg.id]: rating }));
      await sendFeedback(msg, allMsgs, rating);
    }
  };

  const handleCommentSubmit = async (msg: ChatMessage, allMsgs: ChatMessage[], skip = false) => {
    const comment = skip ? '' : commentText.trim();
    setFeedbackGiven((prev) => ({ ...prev, [msg.id]: 'down' }));
    setPendingCommentId(null);
    setCommentText('');
    await sendFeedback(msg, allMsgs, 'down', comment);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b bg-white">
        <h2 className="font-semibold text-gray-700">💬 Conversación</h2>
        <div className="flex items-center gap-3">
          {/* Indicador WS */}
          <span
            className={clsx(
              'inline-block w-2 h-2 rounded-full',
              status === 'authenticated' ? 'bg-green-400' : 'bg-gray-300',
            )}
            title={`WebSocket: ${status}`}
          />
          <button
            onClick={handleClear}
            className="text-xs text-gray-400 hover:text-red-500 transition"
          >
            🗑️ Limpiar
          </button>
        </div>
      </div>

      {/* Mensajes */}
      <div className="flex-1 overflow-y-auto thin-scrollbar px-4 py-4 space-y-4">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={clsx('flex', msg.role === 'user' ? 'justify-end' : 'justify-start')}
          >
            <div
              className={clsx(
                'max-w-[80%] rounded-2xl px-4 py-3 text-sm shadow-sm',
                msg.role === 'user'
                  ? 'bg-gradient-to-r from-brand-orange to-brand-amber text-white rounded-br-none'
                  : 'bg-white border border-gray-200 rounded-bl-none',
              )}
            >
              {msg.role === 'assistant' ? (
                <>
                  <div className="prose prose-sm max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                  {/* Tiempos si existen en result */}
                  {msg.result && (
                    <p className="text-xs text-gray-400 mt-2">
                      ⏱️ BD: {(msg.result.db_search_time ?? 0).toFixed(2)}s
                      {(msg.result.reranking_time ?? 0) > 0 &&
                        ` + Reranking: ${msg.result.reranking_time!.toFixed(2)}s`}{' '}
                      | Respuesta: {(msg.result.response_time ?? 0).toFixed(2)}s
                    </p>
                  )}
                  {/* Enhanced query hint */}
                  {msg.result?.enhanced_query && msg.result.enhanced_query !== msg.content && (
                    <p className="text-xs italic text-gray-400 mt-1">
                      🔍 Interpretado como: {msg.result.enhanced_query}
                    </p>
                  )}
                  {/* Feedback buttons — solo en mensajes reales (no el saludo) */}
                  {msg.result && (
                    <div className="mt-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-400">¿Fue útil?</span>
                        <button
                          onClick={() => handleFeedback(msg, messages, 'up')}
                          disabled={!!feedbackGiven[msg.id] || pendingCommentId === msg.id}
                          title="Respuesta correcta"
                          className={clsx(
                            'text-base transition',
                            feedbackGiven[msg.id] === 'up'
                              ? 'opacity-100 scale-110'
                              : feedbackGiven[msg.id] === 'down'
                              ? 'opacity-25 cursor-not-allowed'
                              : 'opacity-50 hover:opacity-100 hover:scale-110',
                          )}
                        >
                          👍
                        </button>
                        <button
                          onClick={() => handleFeedback(msg, messages, 'down')}
                          disabled={!!feedbackGiven[msg.id] || pendingCommentId === msg.id}
                          title="Respuesta incorrecta"
                          className={clsx(
                            'text-base transition',
                            feedbackGiven[msg.id] === 'down'
                              ? 'opacity-100 scale-110'
                              : feedbackGiven[msg.id] === 'up' || pendingCommentId === msg.id
                              ? 'opacity-25 cursor-not-allowed'
                              : 'opacity-50 hover:opacity-100 hover:scale-110',
                          )}
                        >
                          👎
                        </button>
                      </div>

                      {/* Cajetín de comentario — aparece tras pulsar 👎 */}
                      {pendingCommentId === msg.id && (
                        <div className="mt-2 border border-red-200 rounded-xl bg-red-50 p-3 space-y-2">
                          <p className="text-xs text-red-600 font-medium">¿Qué ha fallado? (opcional)</p>
                          <textarea
                            rows={3}
                            autoFocus
                            value={commentText}
                            onChange={(e) => setCommentText(e.target.value)}
                            placeholder="Describe brevemente el problema..."
                            className="w-full resize-none border border-red-300 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-red-300"
                          />
                          <div className="flex gap-2 justify-end">
                            <button
                              onClick={() => handleCommentSubmit(msg, messages, true)}
                              className="text-xs text-gray-400 hover:text-gray-600 transition px-2 py-1 rounded"
                            >
                              Saltar
                            </button>
                            <button
                              onClick={() => handleCommentSubmit(msg, messages, false)}
                              className="text-xs bg-red-500 hover:bg-red-600 text-white px-3 py-1 rounded-lg transition"
                            >
                              Enviar
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <p className="whitespace-pre-wrap">{msg.content}</p>
              )}
            </div>
          </div>
        ))}

        {/* Texto de streaming en tiempo real */}
        {streamingText && (
          <div className="flex justify-start">
            <div className="max-w-[80%] rounded-2xl px-4 py-3 text-sm shadow-sm bg-white border border-gray-200 rounded-bl-none">
              <div className="prose prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{streamingText}</ReactMarkdown>
              </div>
            </div>
          </div>
        )}

        {/* Label de estado */}
        {!streamingText && statusLabel && (
          <div className="flex justify-start">
            <div className="bg-gray-100 text-gray-500 text-xs px-3 py-2 rounded-xl animate-pulse">
              {statusLabel}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Imagen context badge */}
      {imageContext && (
        <div className="mx-4 mb-1 flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1 text-xs text-amber-700">
          🖼️ Contexto de imagen activo: <em className="truncate max-w-xs">{imageContext}</em>
          <button onClick={onClearImageContext} className="ml-auto text-amber-400 hover:text-red-500">✕</button>
        </div>
      )}

      {/* Input */}
      <div className="px-4 pb-4 pt-2 border-t bg-white">
        <div className="flex gap-2 items-end">
          <textarea
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming || status !== 'authenticated'}
            placeholder="P.ej: ¿quién presentaba 'Caras Nuevas'?"
            className="flex-1 resize-none border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={isStreaming || !input.trim() || status !== 'authenticated'}
            className="bg-gradient-to-r from-brand-orange to-brand-amber text-white rounded-xl px-4 py-2 font-medium text-sm hover:opacity-90 disabled:opacity-40 transition"
          >
            {isStreaming ? '⏳' : '➤'}
          </button>
        </div>
        {status !== 'authenticated' && (
          <p className="text-xs text-red-400 mt-1">
            {status === 'connecting' ? 'Conectando...' : 'Sin conexión al servidor'}
          </p>
        )}
      </div>
    </div>
  );
}
