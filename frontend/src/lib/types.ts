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

// ── Agent streaming (Decision 17/18) ──────────────────────────────────────
// One step in the agent's visible reasoning. Mirrors the SSE frames emitted by
// the backend /agent/stream: each trace frame arrives already shaped as
// {node, type, data}, so these objects drop straight into ChatMessage.trace.
export type TraceEvent =
  | { node: string; type: 'rewrite'; data: { original: string; rewritten: string } }
  | { node: string; type: 'route'; data: { route: string } }
  // The model's own narration between tool calls ("let me check X to find Y").
  // Synthesized client-side: the tokens streamed before a tool_call are its
  // reasoning, so we snapshot them into the trace instead of discarding them.
  | { node: string; type: 'thought'; data: { text: string } }
  | {
      node: string;
      type: 'retrieve';
      data: { n_chunks: number; top: { chunk_id: string; ticker: string; fy: number }[] };
    }
  | {
      node: string;
      type: 'tool_call';
      data: { tool: string; args: Record<string, unknown>; result: unknown };
    }
  | { node: string; type: 'fallback'; data: { reason: string } };

// Payload of the terminal `done` SSE frame.
export interface AgentDone {
  question: string;
  answer: string;
  route: string;
  chunks: RetrievedChunk[];
  usage: Record<string, number>;
  trace: TraceEvent[];
}

// UI model for chat history. The agent path fills `trace` live, streams tokens
// into `content`, and attaches `chunks`/`route`/`usage` on the final frame.
export interface ChatMessage {
  id: string;
  role: 'user' | 'system';
  content: string;
  chunks?: RetrievedChunk[];
  trace?: TraceEvent[];
  route?: string;
  usage?: Record<string, number>;
  streaming?: boolean; // tokens still arriving
  loading?: boolean; // dispatched, awaiting first event
  error?: string;
}
