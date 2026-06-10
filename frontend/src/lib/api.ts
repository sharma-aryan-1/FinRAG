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

// Receives one SSE frame: (eventName, parsedJsonData).
export type SSEHandler = (event: string, data: unknown) => void;

/**
 * Stream the agent run from POST /agent/stream, parsing Server-Sent Events off
 * the response body and invoking `onEvent` per frame (rewrite | route |
 * retrieve | tool_call | token | done | error). We parse SSE by hand rather
 * than using EventSource because EventSource is GET-only — we need a POST body.
 */
export async function streamAgent(
  question: string,
  onEvent: SSEHandler,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/agent/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${body}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line. Process every complete frame in
    // the buffer; leave any partial tail for the next read.
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      let event = 'message';
      const dataLines: string[] = [];
      for (const raw of frame.split('\n')) {
        const line = raw.replace(/\r$/, '');
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
      }
      if (dataLines.length === 0) continue;
      try {
        onEvent(event, JSON.parse(dataLines.join('\n')));
      } catch {
        // Skip a malformed frame rather than aborting the whole stream.
      }
    }
  }
}
