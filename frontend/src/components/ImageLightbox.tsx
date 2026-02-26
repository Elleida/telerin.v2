'use client';

import { useEffect } from 'react';

interface ImageLightboxProps {
  src: string;
  alt?: string;
  onClose: () => void;
}

export default function ImageLightbox({ src, alt = 'imagen', onClose }: ImageLightboxProps) {
  // Cerrar con Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* Panel */}
      <div
        className="relative max-w-5xl max-h-[90vh] w-full mx-4 flex flex-col items-center"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Botones cabecera */}
        <div className="w-full flex justify-end gap-2 mb-2">
          <a
            href={src}
            target="_blank"
            rel="noopener noreferrer"
            className="bg-white/20 hover:bg-white/30 text-white text-xs px-3 py-1 rounded-full transition"
            title="Abrir en pestaña nueva"
          >
            ↗ Abrir
          </a>
          <button
            onClick={onClose}
            className="bg-white/20 hover:bg-white/30 text-white text-xs px-3 py-1 rounded-full transition"
            title="Cerrar (Esc)"
          >
            ✕ Cerrar
          </button>
        </div>

        {/* Imagen */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={alt}
          className="max-h-[80vh] max-w-full object-contain rounded-lg shadow-2xl"
        />

        {/* Pie */}
        <p className="mt-2 text-white/60 text-xs truncate max-w-full">{src}</p>
      </div>
    </div>
  );
}
