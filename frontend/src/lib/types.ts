// Tipos compartidos entre componentes

export interface SourceInfo {
  document?: string;
  magazine_id?: string;
  page_number?: string | number;
  title?: string;
  date?: string;
  score?: number;
  similarity?: number;
  relevance?: number;
  png_url?: string;
}

export interface SqlQueryInfo {
  table?: string;
  sql?: string;
}

export interface ChatFinalResult {
  response: string;
  sources: SourceInfo[];
  sql_queries: SqlQueryInfo[];
  prompt_used?: string;
  query_type?: string;
  search_classification?: string;
  enhanced_query?: string;
  is_contextual_follow_up?: boolean;
  elapsed_time?: number;
  search_time?: number;
  db_search_time?: number;
  reranking_time?: number;
  response_time?: number;
  prompt_tokens?: number;
  response_tokens?: number;
  error?: string;
  success?: boolean;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  result?: ChatFinalResult;
}

export interface ImageSearchResult {
  id?: string;
  magazine_id?: string;
  page_number?: string | number;
  src?: string;
  png_url?: string;
  description?: string;
  caption_literal?: string;
  similarity?: number;
  relevance_score?: number;
}

export interface SessionContext {
  num_turns: number;
  context_summary: Record<string, unknown>;
  global_entities: Record<string, unknown>;
  recent_searches: Array<{ query: string }>;
  last_turn?: {
    user_query?: string;
    enhanced_query?: string;
    query_type?: string;
    response?: string;
  };
}

export type LlmBackend = 'ollama' | 'gemini';

export interface ChatSettings {
  llm_backend: LlmBackend;
  llm_model: string;
  sql_limit: number;
  llm_score_threshold: number;
}
