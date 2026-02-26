'use client';

import { useRef, useState, useEffect } from 'react';
import Image from 'next/image';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ImageSearchResult } from '@/lib/types';
import { apiImageSearch, apiImageAnalyze } from '@/lib/api';
import { ChatSettings } from '@/lib/types';
import ImageLightbox from '@/components/ImageLightbox';

const mdComponents = {
  a: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline" {...props}>{children}</a>
  ),
};

interface ImageSearchTabProps {
  settings: ChatSettings;
  onImageContext?: (description: string) => void;
  clearKey?: number;
}

export default function ImageSearchTab({ settings, onImageContext, clearKey }: ImageSearchTabProps) {
  const [textQuery, setTextQuery] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [maxResults, setMaxResults] = useState(60);
  const [results, setResults] = useState<ImageSearchResult[]>([]);
  const [description, setDescription] = useState<string | null>(null);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [sqlQuery, setSqlQuery] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysisResponse, setAnalysisResponse] = useState<string | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Limpiar todo cuando el padre incrementa clearKey
  useEffect(() => {
    if (!clearKey) return;
    setTextQuery('');
    setFile(null);
    setPreview(null);
    setResults([]);
    setDescription(null);
    setSqlQuery(null);
    setError(null);
    setAnalysisResponse(null);
    setLightboxSrc(null);
    if (inputRef.current) inputRef.current.value = '';
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearKey]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    if (f) {
      const url = URL.createObjectURL(f);
      setPreview(url);
    } else {
      setPreview(null);
    }
  };

  const handleSearch = async () => {
    if (!file && !textQuery) {
      setError('Sube una imagen o escribe una descripción');
      return;
    }
    setLoading(true);
    setError(null);
    setResults([]);
    setDescription(null);
    setAnalysisResponse(null);
    try {
      const data = await apiImageSearch(file, textQuery, maxResults);
      setResults(data.results ?? []);
      setDescription(data.description ?? null);
      setSqlQuery(data.sql_query ?? null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error en búsqueda');
    } finally {
      setLoading(false);
    }
  };

  const handleAnalyze = async () => {
    if (!description || !results.length) return;
    setAnalysisLoading(true);
    setAnalysisResponse(null);
    try {
      const data = await apiImageAnalyze(
        description,
        results,
        settings.llm_backend,
        settings.llm_model || undefined,
      );
      setAnalysisResponse(data.response);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error en análisis');
    } finally {
      setAnalysisLoading(false);
    }
  };

  const handleUseInChat = () => {
    if (description) onImageContext?.(description);
  };

  const handleClear = () => {
    setTextQuery('');
    setFile(null);
    setPreview(null);
    setResults([]);
    setDescription(null);
    setSqlQuery(null);
    setError(null);
    setAnalysisResponse(null);
    setLightboxSrc(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <>
      {lightboxSrc && <ImageLightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}
      <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-700">🔎 Búsqueda de Imágenes</h2>
        <button
          onClick={handleClear}
          className="text-xs text-gray-400 hover:text-red-500 transition"
        >
          🗑️ Limpiar
        </button>
      </div>

      {/* Controles */}
      <div className="grid grid-cols-3 gap-4">
        {/* Col 1: texto */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-600">Búsqueda por texto</label>
          <input
            type="text"
            value={textQuery}
            onChange={(e) => setTextQuery(e.target.value)}
            placeholder="Describe la imagen a buscar..."
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
          <label className="text-xs text-gray-500">
            Nº imágenes: <strong>{maxResults}</strong>
          </label>
          <input
            type="range" min={1} max={100} step={1}
            value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))}
            className="w-full accent-orange-500"
          />
          <button
            onClick={handleSearch}
            disabled={loading}
            className="w-full bg-gradient-to-r from-brand-orange to-brand-amber text-white py-2 rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {loading ? '⏳ Buscando...' : '🔍 Buscar'}
          </button>
        </div>

        {/* Col 2: upload */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-600">Búsqueda por imagen</label>
          <div
            className="border-2 border-dashed border-gray-300 rounded-lg p-4 text-center cursor-pointer hover:border-orange-400 transition"
            onClick={() => inputRef.current?.click()}
          >
            <p className="text-sm text-gray-400">
              {file ? file.name : 'Haz clic para subir PNG/JPG'}
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".png,.jpg,.jpeg"
            className="hidden"
            onChange={handleFileChange}
          />
        </div>

        {/* Col 3: preview */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-600">Vista previa</label>
          {preview ? (
            <div className="relative w-full h-32 rounded-lg overflow-hidden border border-gray-200">
              <Image src={preview} alt="preview" fill className="object-contain" />
            </div>
          ) : (
            <div className="w-full h-32 bg-gray-50 rounded-lg border border-gray-200 flex items-center justify-center text-gray-400 text-xs">
              Sin imagen
            </div>
          )}
          {description && (
            <div className="text-xs bg-amber-50 border border-amber-200 rounded-lg p-2 text-amber-700">
              📝 {description}
            </div>
          )}
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border-l-4 border-red-400 text-red-700 p-3 text-sm rounded">
          ❌ {error}
        </div>
      )}

      {/* Resultados */}
      {results.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-gray-600">
              Encontradas {results.length} imágenes
            </h3>
            <div className="flex gap-2">
              {description && (
                <button
                  onClick={handleUseInChat}
                  className="text-xs bg-blue-50 border border-blue-300 text-blue-700 px-3 py-1.5 rounded-lg hover:bg-blue-100 transition"
                >
                  💬 Usar en chat
                </button>
              )}
              <button
                onClick={handleAnalyze}
                disabled={analysisLoading}
                className="text-xs bg-gradient-to-r from-brand-orange to-brand-amber text-white px-3 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
              >
                {analysisLoading ? '⏳ Analizando...' : '🤖 Analizar con agente'}
              </button>
            </div>
          </div>

          {analysisResponse && (
            <div className="mb-4 bg-blue-50 border-l-4 border-brand-orange p-3 rounded-lg text-sm prose prose-sm max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                {analysisResponse}
              </ReactMarkdown>
            </div>
          )}

          {sqlQuery && (
            <details className="mb-3 text-xs">
              <summary className="cursor-pointer text-gray-400">Ver SQL ejecutado</summary>
              <pre className="bg-gray-100 p-2 rounded mt-1 overflow-x-auto">{sqlQuery}</pre>
            </details>
          )}

          {/* Grid de imágenes — 4 columnas */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {results.map((r, i) => {
              const imgUrl = r.png_url ?? r.src;
              const rel = r.relevance_score ?? r.similarity;
              return (
                <div key={r.id ?? i} className="border border-gray-200 rounded-xl overflow-hidden bg-white shadow-sm hover:shadow-md transition">
                  {imgUrl ? (
                    <button
                      onClick={() => setLightboxSrc(imgUrl)}
                      className="w-full text-left"
                      title="Ver imagen"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={imgUrl}
                        alt={r.description ?? 'imagen'}
                        className="w-full h-28 object-contain bg-gray-50"
                      />
                    </button>
                  ) : (
                    <div className="w-full h-28 bg-gray-100 flex items-center justify-center text-gray-400 text-xs">
                      Sin imagen
                    </div>
                  )}
                  <div className="p-2 text-xs space-y-0.5">
                    <p className="font-medium truncate">{r.magazine_id ?? 'N/A'}</p>
                    <p className="text-gray-400">Pág. {r.page_number ?? '-'}</p>
                    {rel != null && (
                      <p className="text-orange-500">{(rel * 100).toFixed(1)}%</p>
                    )}
                    {r.description && (
                      <p className="text-gray-500 line-clamp-2">{r.description}</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
    </>
  );
}
