'use client';

import { useState } from 'react';
import { ChatPane } from '@/components/ChatPane';
import { CitationViewer } from '@/components/CitationViewer';
import { query } from '@/lib/api';
import { ChatMessage, RetrievedChunk } from '@/lib/types';

export default function Page() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [selectedChunk, setSelectedChunk] = useState<RetrievedChunk | null>(null);

  async function handleAsk(question: string) {
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: question,
    };
    const sysMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'system',
      content: '',
      loading: true,
    };
    setMessages((prev) => [...prev, userMsg, sysMsg]);

    try {
      const res = await query({ question, top_k: 5 });
      setMessages((prev) =>
        prev.map((m) =>
          m.id === sysMsg.id ? { ...m, loading: false, chunks: res.chunks } : m,
        ),
      );
      // Auto-select the top chunk so the viewer always shows something
      // useful right after a query.
      if (res.chunks.length > 0) {
        setSelectedChunk(res.chunks[0]);
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === sysMsg.id ? { ...m, loading: false, error: errorMsg } : m,
        ),
      );
    }
  }

  return (
    <main className="h-screen flex flex-col bg-neutral-50 dark:bg-neutral-950">
      <header className="border-b border-neutral-200 dark:border-neutral-800 px-6 py-3 flex items-center justify-between bg-white dark:bg-neutral-950">
        <h1 className="text-base font-semibold tracking-tight">FinRAG</h1>
        <div className="text-xs text-neutral-500">
          SEC 10-K retrieval · hybrid + rerank
        </div>
      </header>

      <div className="flex-1 grid grid-cols-1 md:grid-cols-2 overflow-hidden">
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
