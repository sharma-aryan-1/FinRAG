'use client';

import { useEffect, useRef, useState } from 'react';
import { ChatMessage, RetrievedChunk } from '@/lib/types';
import { ChunkCard } from './ChunkCard';
import { AgentTrace } from './AgentTrace';
import { AgentAnswer } from './AgentAnswer';

interface Props {
  messages: ChatMessage[];
  onAsk: (question: string) => void;
  selectedChunkId: string | null;
  onChunkSelect: (chunk: RetrievedChunk) => void;
}

// Clickable starters on the empty state — one per route flavour, so a first-time
// visitor can see the agent plan/route/tool without typing (ChatGPT-style prompts).
const SUGGESTIONS = [
  'Compare net income for Apple, Tesla, and JPMorgan in fiscal 2023',
  'How do Apple and Tesla describe supply-chain risk?',
  'Which of the three grew net income fastest year-over-year into 2023?',
];

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
    <div className="flex flex-col h-full min-h-0 border-r border-neutral-200 dark:border-neutral-800">
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-4 py-6 space-y-7">
        {messages.length === 0 ? (
          <div className="mx-auto max-w-lg mt-20 px-2">
            <h2 className="font-serif text-4xl sm:text-5xl font-light tracking-tight text-neutral-900 dark:text-neutral-50">
              Ask the filings<span className="text-lime-500 dark:text-lime-400">.</span>
            </h2>
            <p className="mt-4 font-mono text-[11px] uppercase tracking-[0.18em] text-neutral-400">
              AAPL · TSLA · JPM · FY2022–2024
            </p>
            <div className="mt-8 flex flex-col gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => onAsk(s)}
                  className="group flex items-center gap-2 rounded-xl border border-neutral-200 dark:border-neutral-800 px-3.5 py-2.5 text-left text-sm text-neutral-600 dark:text-neutral-300 hover:border-lime-400 hover:bg-lime-50 dark:hover:border-lime-700/70 dark:hover:bg-lime-950/30 transition"
                >
                  <span className="font-mono text-lime-500 dark:text-lime-400">↳</span>
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-neutral-100 dark:bg-neutral-800 px-3.5 py-2 text-sm text-neutral-900 dark:text-neutral-100">
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={m.id} className="space-y-3">
                {m.loading && !m.trace?.length && (
                  <div className="flex items-center gap-2 text-xs italic text-lime-600 dark:text-lime-400">
                    <span className="animate-pulse">✦</span> Thinking…
                  </div>
                )}
                {m.trace && m.trace.length > 0 && (
                  <AgentTrace trace={m.trace} running={m.loading || m.streaming} />
                )}
                {m.error && (
                  <div className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30 px-3 py-2 text-sm text-red-600 dark:text-red-400">
                    {m.error}
                  </div>
                )}
                {m.content && <AgentAnswer text={m.content} streaming={m.streaming} />}
                {m.chunks && m.chunks.length > 0 && (
                  <div className="space-y-2 pt-1">
                    <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-neutral-400">
                      Sources
                    </div>
                    {m.chunks.map((c, i) => (
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
            ),
          )
        )}
      </div>

      <form
        onSubmit={handleSubmit}
        className="border-t border-neutral-200 dark:border-neutral-800 p-3 bg-white dark:bg-neutral-900"
      >
        <div className="flex items-center gap-2 rounded-2xl border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-800 pl-4 pr-2 py-1.5 focus-within:border-lime-400 focus-within:ring-2 focus-within:ring-lime-400/40 transition">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a question…"
            className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-neutral-400"
          />
          <button
            type="submit"
            disabled={!input.trim()}
            aria-label="Send"
            className="grid h-8 w-8 place-items-center rounded-full bg-accent hover:bg-lime-400 disabled:bg-neutral-200 dark:disabled:bg-neutral-700 disabled:text-neutral-400 text-neutral-900 transition"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path
                d="M8 13V3M8 3L4 7M8 3l4 4"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      </form>
    </div>
  );
}
