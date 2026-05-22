import { QueryRequest, QueryResponse } from './types';

// Override at build time via NEXT_PUBLIC_API_BASE; defaults to local FastAPI.
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

export async function query(req: QueryRequest): Promise<QueryResponse> {
  const res = await fetch(`${API_BASE}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}
