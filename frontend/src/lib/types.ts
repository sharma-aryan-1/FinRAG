// Mirrors backend/src/finrag/retrieval/vector.py:RetrievedChunk
export interface RetrievedChunk {
  chunk_id: string;
  score: number;
  text: string;
  chunk_type: string;
  section_title: string | null;
  ticker: string;
  company_name: string;
  fiscal_year: number;
  period_of_report: string;
  accession_number: string;
  sec_url: string;
}

export interface QueryRequest {
  question: string;
  top_k?: number;
  ticker?: string | null;
  fiscal_year?: number | null;
  chunk_type?: string | null;
}

export interface QueryResponse {
  question: string;
  chunks: RetrievedChunk[];
}

// UI-only model for chat history. Day 3 will add streaming `answer` field
// when the agent produces synthesized text alongside the citations.
export interface ChatMessage {
  id: string;
  role: 'user' | 'system';
  content: string;
  chunks?: RetrievedChunk[];
  loading?: boolean;
  error?: string;
}
