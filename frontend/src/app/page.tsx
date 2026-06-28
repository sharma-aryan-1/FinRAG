'use client';

import { useState } from 'react';
import { ChatPane } from '@/components/ChatPane';
import { CitationViewer } from '@/components/CitationViewer';
import { Sidebar } from '@/components/Sidebar';
import { useConversations } from '@/lib/conversations';
import { streamAgent } from '@/lib/api';
import { AgentDone, ChatMessage, RetrievedChunk, TraceEvent } from '@/lib/types';

export default function Page() {
  const {
    conversations,
    activeId,
    activeMessages,
    newConversation,
    ensureActive,
    selectConversation,
    deleteConversation,
    updateMessages,
  } = useConversations();
  const [selectedChunk, setSelectedChunk] = useState<RetrievedChunk | null>(null);
  // The citation pane is closed by default; opening is an explicit act (clicking
  // a source), so the chat gets the full width until the user wants provenance.
  const [panelOpen, setPanelOpen] = useState(false);

  function openCitation(chunk: RetrievedChunk) {
    setSelectedChunk(chunk);
    setPanelOpen(true);
  }

  // Starting a fresh chat also closes any open citation pane.
  function startNewChat() {
    newConversation();
    setPanelOpen(false);
    setSelectedChunk(null);
  }

  async function handleAsk(question: string) {
    // Pin this run to the conversation active at ask-time, so streaming patches
    // land on the right chat even if the user switches conversations mid-answer.
    const convId = ensureActive();
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
    updateMessages(convId, (prev) => [...prev, userMsg, sysMsg]);

    // Patch just this run's system message as events stream in.
    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      updateMessages(convId, (prev) => prev.map((m) => (m.id === sysId ? fn(m) : m)));

    try {
      await streamAgent(question, (event, data) => {
        switch (event) {
          case 'rewrite':
          case 'route':
          case 'retrieve':
          case 'fallback':
            // Trace frames already arrive shaped as TraceEvent ({node,type,data}).
            patch((m) => ({
              ...m,
              loading: false,
              trace: [...(m.trace ?? []), data as TraceEvent],
            }));
            break;
          case 'tool_call':
            // A tool call ends the model's pre-tool narration. Snapshot whatever
            // it streamed ("let me check X…") into the trace as a `thought` so it
            // persists, then clear content so the real answer streams in clean.
            patch((m) => {
              const narration = m.content.trim();
              const thought: TraceEvent[] = narration
                ? [{ node: 'agent', type: 'thought', data: { text: narration } }]
                : [];
              return {
                ...m,
                loading: false,
                content: '',
                trace: [...(m.trace ?? []), ...thought, data as TraceEvent],
              };
            });
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
            // Note: we do NOT auto-open the citation pane — it opens only when the
            // user clicks a source, keeping the answer view uncluttered.
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
    <main className="h-screen flex flex-col bg-neutral-50 dark:bg-neutral-900">
      <header className="border-b border-neutral-200 dark:border-neutral-800 px-6 py-3 flex items-center justify-between bg-white/80 dark:bg-neutral-900/80 backdrop-blur">
        <button
          onClick={startNewChat}
          aria-label="New chat"
          title="New chat"
          className="flex items-center gap-2 font-mono rounded-md -mx-1 px-1 py-0.5 hover:opacity-80 transition cursor-pointer"
        >
          <span className="h-2 w-2 rounded-full bg-accent shadow-[0_0_8px] shadow-lime-400/60" />
          <span className="text-sm font-medium tracking-tight text-neutral-900 dark:text-neutral-50">
            finrag<span className="text-lime-500 dark:text-lime-400">.ai</span>
          </span>
        </button>
        <div className="flex items-center gap-4">
          <span className="hidden sm:block font-mono text-[10px] uppercase tracking-[0.18em] text-neutral-400">
            SEC 10-K AGENT · PLAN → RETRIEVE → TOOLS → ANSWER
          </span>
          <a
            href="https://github.com/sharma-aryan-1/FinRAG"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Source on GitHub"
            title="View source — built by Aryan Sharma"
            className="text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100 transition"
          >
            <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </a>
        </div>
      </header>

      <div className="flex-1 min-h-0 flex overflow-hidden">
        <Sidebar
          conversations={conversations}
          activeId={activeId}
          onNew={startNewChat}
          onSelect={selectConversation}
          onDelete={deleteConversation}
        />
        <div className="flex-1 min-w-0">
          <ChatPane
            messages={activeMessages}
            onAsk={handleAsk}
            selectedChunkId={panelOpen ? selectedChunk?.chunk_id ?? null : null}
            onChunkSelect={openCitation}
          />
        </div>
        {panelOpen && selectedChunk && (
          <aside className="w-full max-w-md lg:max-w-lg border-l border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900">
            <CitationViewer chunk={selectedChunk} onClose={() => setPanelOpen(false)} />
          </aside>
        )}
      </div>
    </main>
  );
}
