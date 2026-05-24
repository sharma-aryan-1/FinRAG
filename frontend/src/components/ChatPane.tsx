'use client';

import { useEffect, useRef, useState } from 'react';
import { ChatMessage, RetrievedChunk } from '@/lib/types';
import { ChunkCard } from './ChunkCard';

interface Props {
  messages: ChatMessage[];
  onAsk: (question: string) => void;
  selectedChunkId: string | null;
  onChunkSelect: (chunk: RetrievedChunk) => void;
}

export function ChatPane({ messages, onAsk, selectedChunkId, onChunkSelect }: Props) {
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Pin to bottom whenever messages change. ScrollIntoView with smooth
    // looks nicer than a hard jump.
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    });
  }, [messages]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    onAsk(trimmed);
    setInput('');
  }

  return (
    <div className="flex flex-col h-full border-r border-neutral-200 dark:border-neutral-800">
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-6">
        {messages.length === 0 ? (
          <div className="text-center text-neutral-500 mt-12">
            <p className="text-sm">Ask a question about Apple, Tesla, or JPMorgan 10-K filings</p>
            <p className="text-xs mt-2 text-neutral-400">
              FY2022 – FY2024
            </p>
          </div>
        ) : (
          messages.map((m) => (
            <div key={m.id}>
              {m.role === 'user' ? (
                <div className="font-medium text-neutral-900 dark:text-neutral-100">
                  <span className="text-neutral-400 mr-2">›</span>
                  {m.content}
                </div>
              ) : (
                <div className="space-y-2 pl-4 border-l-2 border-neutral-200 dark:border-neutral-800">
                  {m.loading && (
                    <div className="text-sm text-neutral-500 italic animate-pulse">
                      retrieving…
                    </div>
                  )}
                  {m.error && (
                    <div className="text-sm text-red-600">
                      Error: {m.error}
                    </div>
                  )}
                  {m.chunks?.map((c, i) => (
                    <ChunkCard
                      key={c.chunk_id}
                      chunk={c}
                      index={i}
                      selected={c.chunk_id === selectedChunkId}
                      onSelect={onChunkSelect}
                    />
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      <form
        onSubmit={handleSubmit}
        className="border-t border-neutral-200 dark:border-neutral-800 p-3 bg-white dark:bg-neutral-950"
      >
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a question..."
            className="flex-1 px-3 py-2 rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={!input.trim()}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-neutral-300 disabled:cursor-not-allowed text-white text-sm font-medium transition"
          >
            Ask
          </button>
        </div>
      </form>
    </div>
  );
}
