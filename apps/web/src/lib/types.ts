export type User = {
  id: string;
  email: string;
  display_name: string;
};

export type RetrievalConfig = {
  top_k_vector: number;
  top_k_lexical: number;
  rrf_k: number;
  rerank_top_n: number;
  context_k: number;
  context_token_budget: number;
  hybrid_enabled: boolean;
  rerank_enabled: boolean;
  bm25_k1: number;
  bm25_b: number;
};

export type ChunkConfig = {
  strategy: string;
  target_tokens: number;
  overlap_tokens: number;
  split_oversized_blocks: boolean;
};

export type KnowledgeBase = {
  id: string;
  name: string;
  description: string;
  role: "owner" | "editor" | "viewer";
  document_count: number;
  ready_count: number;
  retrieval_config: RetrievalConfig;
  chunk_config: ChunkConfig;
  created_at: string;
  updated_at: string;
};

export type Providers = {
  embedding: { model: string; ready: boolean };
  reranker: { model: string; ready: boolean };
  language_model: { provider: string; model: string; ready: boolean };
};

export type Session = {
  accessToken: string;
  refreshToken: string;
};

export type Document = {
  id: string;
  knowledge_base_id: string;
  title: string;
  source_type: string;
  source_uri: string | null;
  status: string;
  error_code: string | null;
  error_detail: string | null;
  current_version_id: string | null;
  current_version_number: number | null;
  preview_version_id: string | null;
  pending_version_id: string | null;
  pending_run_id: string | null;
  pending_status: string | null;
  chunk_preview_run_id: string | null;
  byte_size: number | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
};

export type ProcessingRun = {
  id: string;
  document_version_id: string;
  status: string;
  is_active: boolean;
  chunk_count: number;
  error_code: string | null;
};

export type Chunk = {
  id: string;
  sequence: number;
  text: string;
  token_count: number;
  locator_start: Record<string, unknown>;
  locator_end: Record<string, unknown>;
  metadata: Record<string, unknown>;
};

export type ChunkPage = {
  items: Chunk[];
  next_cursor: string | null;
};
