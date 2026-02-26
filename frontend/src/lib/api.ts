// Cliente HTTP para llamadas REST al backend FastAPI

import { getToken } from './auth';
import { SessionContext, ImageSearchResult } from './types';

// Use relative URLs so requests go through Next.js rewrites (/api/* → backend).
// NEXT_PUBLIC_API_URL is only needed for SSR or direct backend access.
const BASE = typeof window !== 'undefined'
  ? ''   // browser: rutas relativas, Next.js proxy hace el rewrite
  : (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000');

// For endpoints that can take >30s (LLM calls), bypass the Next.js proxy
// and call the backend directly using the same hostname but port 8000.
const DIRECT_BASE = typeof window !== 'undefined'
  ? `${window.location.protocol}//${window.location.hostname}:${process.env.NEXT_PUBLIC_API_URL ? new URL(process.env.NEXT_PUBLIC_API_URL).port || '8000' : '8000'}`
  : (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000');

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string>),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? String(res.status));
  }
  return res.json() as T;
}

// ── Auth ───────────────────────────────────────────────────────────────────

export const apiLogin = (username: string, password: string) =>
  apiFetch<{ access_token: string; username: string }>('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });

export const apiMe = () =>
  apiFetch<{ id: string; username: string; role: string }>('/api/auth/me');

// ── Session ────────────────────────────────────────────────────────────────

export const apiGetContext = () =>
  apiFetch<SessionContext>('/api/session/context');

export const apiClearSession = () =>
  fetch(`${BASE}/api/session/clear`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  });

// ── Image ──────────────────────────────────────────────────────────────────

// ── Feedback ──────────────────────────────────────────────────────────────

export const apiFeedback = (payload: {
  session_id?: string;
  query: string;
  response: string;
  rating: 'up' | 'down';
  comment?: string;
}) =>
  apiFetch<{ ok: boolean }>('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

// ── Image ──────────────────────────────────────────────────────────────────

export const apiImageSearch = async (
  file: File | null,
  textQuery: string,
  maxResults: number,
): Promise<{ results: ImageSearchResult[]; description?: string; sql_query?: string }> => {
  const fd = new FormData();
  if (file) fd.append('file', file);
  if (textQuery) fd.append('text_query', textQuery);
  fd.append('max_results', String(maxResults));

  const token = getToken();
  const res = await fetch(`${DIRECT_BASE}/api/image/search`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
};

export const apiImageDescribe = async (file: File): Promise<string> => {
  const fd = new FormData();
  fd.append('file', file);
  const token = getToken();
  const res = await fetch(`${DIRECT_BASE}/api/image/describe`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  return data.description;
};

export const apiImageAnalyze = async (
  description: string,
  results: ImageSearchResult[],
  llm_backend: string,
  llm_model?: string,
): Promise<{ response: string; sources: unknown[]; error?: string }> => {
  // Use DIRECT_BASE to bypass Next.js proxy timeout (LLM calls can take >30s)
  const token = getToken();
  const res = await fetch(`${DIRECT_BASE}/api/image/analyze`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ description, results, session_id: '', llm_backend, llm_model }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? String(res.status));
  }
  return res.json();
};
