// Cliente HTTP para llamadas REST al backend FastAPI

import { getToken } from './auth';
import { SessionContext, ImageSearchResult } from './types';

// Use relative URLs so requests go through Next.js rewrites (/api/* → backend).
// In the browser we prefix with basePath ('/teleradio') so the fetch path
// '/teleradio/api/...' matches the Next.js rewrite rule (which auto-prepends
// basePath to the source '/api/:path*'). Works both direct and via nginx proxy.
// NEXT_PUBLIC_API_URL is only needed for SSR or direct backend access.
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
const BASE = typeof window !== 'undefined'
  ? BASE_PATH  // browser: prefijo /teleradio + Next.js proxy hace el rewrite
  : (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000');

// DIRECT_BASE removed: all calls (including long LLM ones) go through the
// Next.js rewrite proxy which has proxyTimeout:120_000 in next.config.js.

/**
 * Convert any absolute image URL (e.g. http://signal4:8000/images/...)
 * to a relative path (/teleradio/images/...) so images are always fetched
 * through the Next.js /images rewrite — works direct and via nginx, no
 * mixed-content issues.
 */
export function normalizePngUrl(url: string | undefined): string | undefined {
  if (!url) return url;
  // Already relative — nothing to do
  if (!url.startsWith('http://') && !url.startsWith('https://')) return url;
  const m = url.match(/(\/images\/.+)/);
  return m ? (BASE_PATH + m[1]) : url;
}

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

// ── Admin / Users ───────────────────────────────────────────────────────────

export interface UserPublic {
  id: string;
  username: string;
  email?: string;
  role: string;
  first_name?: string;
  last_name?: string;
  created_at?: string;
}

export const apiGetUsers = () =>
  apiFetch<UserPublic[]>('/api/auth/users');

export const apiCreateUser = (data: { username: string; password: string; email?: string; role: string; first_name?: string; last_name?: string }) =>
  apiFetch<UserPublic>('/api/auth/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const apiUpdateUser = (id: string, data: { username?: string; email?: string; role?: string; password?: string; first_name?: string; last_name?: string }) =>
  apiFetch<UserPublic>(`/api/auth/users/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const apiDeleteUser = (id: string) =>
  fetch(`${BASE}/api/auth/users/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  });

// ── Stats ─────────────────────────────────────────────────────────────────

export interface DayStat   { date: string; count: number; up: number; down: number; }
export interface UserStat  { username: string; count: number; up: number; down: number; avg_response_s: number; }
export interface RecentEntry { ts: string; user: string; rating: string; query: string; num_sources: number; total_s: number; llm_model: string; comment?: string; }
export interface StatsData {
  total: number; up: number; down: number;
  avg_db_search_s: number; avg_reranking_s: number; avg_response_s: number; avg_total_s: number;
  avg_num_sources: number;
  avg_prompt_tokens: number;
  avg_response_tokens: number;
  by_day: DayStat[]; by_user: UserStat[]; recent: RecentEntry[];
}

export const apiGetStats = () => apiFetch<StatsData>('/api/stats');

// ── Models ──────────────────────────────────────────────────────────────────────────────
export interface ModelInfo { name: string; size: number; }
export const apiGetModels = () => apiFetch<{ ollama: ModelInfo[] }>('/api/models');

// ── Feedback ──────────────────────────────────────────────────────────────

export const apiFeedback = (payload: {
  session_id?: string;
  query: string;
  response: string;
  rating: 'up' | 'down';
  comment?: string;
  db_search_time?: number;
  reranking_time?: number;
  response_time?: number;
  num_sources?: number;
  llm_model?: string;
  prompt_tokens?: number;
  response_tokens?: number;
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
  const res = await fetch(`${BASE}/api/image/search`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json() as { results: ImageSearchResult[]; description?: string; sql_query?: string };
  // Normalize png_url to relative paths so images work behind nginx
  data.results = (data.results ?? []).map(r => ({ ...r, png_url: normalizePngUrl(r.png_url) }));
  return data;
};

export const apiImageDescribe = async (file: File): Promise<string> => {
  const fd = new FormData();
  fd.append('file', file);
  const token = getToken();
  const res = await fetch(`${BASE}/api/image/describe`, {
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
  // Routed through Next.js proxy (proxyTimeout:120_000 in next.config.js)
  const token = getToken();
  const res = await fetch(`${BASE}/api/image/analyze`, {
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
