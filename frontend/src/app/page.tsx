'use client';

import { useState } from 'react';
import { ChatPane } from '@/components/ChatPane';
import { CitationViewer } from '@/components/CitationViewer';
import { streamAgent } from '@/lib/api';
import { AgentDone, ChatMessage, RetrievedChunk, TraceEvent } from '@/lib/types';

export default function Page() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [selectedChunk, setSelectedChunk] = useState<RetrievedChunk | null>(null);

  async function handleAsk(question: string) {
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: question,
    };
    const sysId = crypto.randomUUID();
    const sysMsg: ChatMessage = {
      id: sysId,
      role: 'system',
      content: '',
      loading: true,
      trace: [],
    };
    setMessages((prev) => [...prev, userMsg, sysMsg]);

    // Patch just this run's system message as events stream in.
    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((prev) => prev.map((m) => (m.id === sysId ? fn(m) : m)));

    try {
      await streamAgent(question, (event, data) => {
        switch (event) {
          case 'rewrite':
          case 'route':
          case 'retrieve':
          case 'tool_call':
          case 'fallback':
            // Trace frames already arrive shaped as TraceEvent ({node,type,data}).
            patch((m) => ({
              ...m,
              loading: false,
              trace: [...(m.trace ?? []), data as TraceEvent],
              // A tool call ends the model's pre-tool narration; clear it so the
              // real answer streams in clean (matches the authoritative `done`).
              ...(event === 'tool_call' ? { content: '' } : {}),
            }));
            break;
          case 'token':
            patch((m) => ({
              ...m,
              loading: false,
              streaming: true,
              content: m.content + ((data as { text?: string }).text ?? ''),
            }));
            break;
          case 'done': {
            const d = data as AgentDone;
            patch((m) => ({
              ...m,
              loading: false,
              streaming: false,
              content: d.answer || m.content,
              chunks: d.chunks,
              route: d.route,
              usage: d.usage,
            }));
            if (d.chunks.length > 0) setSelectedChunk(d.chunks[0]);
            break;
          }
          case 'error':
            patch((m) => ({
              ...m,
              loading: false,
              streaming: false,
              error: (data as { message?: string }).message ?? 'stream error',
            }));
            break;
        }
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      patch((m) => ({ ...m, loading: false, streaming: false, error: errorMsg }));
    }
  }

  return (
    <main className="h-screen flex flex-col bg-neutral-50 dark:bg-neutral-950">
      <header className="border-b border-neutral-200 dark:border-neutral-800 px-6 py-3 flex items-center justify-between bg-white dark:bg-neutral-950">
        <h1 className="text-base font-semibold tracking-tight">FinRAG</h1>
        <div className="text-xs text-neutral-500">
          SEC 10-K agent · plan → retrieve → tools → answer
        </div>
      </header>

      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 overflow-hidden">
        <ChatPane
          messages={messages}
          onAsk={handleAsk}
          selectedChunkId={selectedChunk?.chunk_id ?? null}
          onChunkSelect={setSelectedChunk}
        />
        <CitationViewer chunk={selectedChunk} />
      </div>
    </main>
  );
}
