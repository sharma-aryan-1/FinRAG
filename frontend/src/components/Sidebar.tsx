'use client';

import { Conversation } from '@/lib/conversations';

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

// Left rail: New chat + the conversation list (newest first). Purely client-side
// history (localStorage) — see useConversations. Hidden on narrow screens to
// keep the chat full-width on mobile.
export function Sidebar({ conversations, activeId, onNew, onSelect, onDelete }: Props) {
  return (
    <aside className="hidden md:flex w-60 shrink-0 flex-col border-r border-neutral-200 dark:border-neutral-800 bg-white/60 dark:bg-neutral-900/60">
      <div className="p-3">
        <button
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-xl border border-neutral-300 dark:border-neutral-700 px-3 py-2 text-left text-sm text-neutral-700 dark:text-neutral-200 hover:border-lime-400 hover:bg-lime-50 dark:hover:border-lime-700/70 dark:hover:bg-lime-950/30 transition"
        >
          <span className="text-lime-500 dark:text-lime-400 text-base leading-none">＋</span>
          New chat
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-2 pb-3 space-y-0.5">
        {conversations.length === 0 ? (
          <p className="px-2 py-3 font-mono text-[10px] uppercase tracking-[0.18em] text-neutral-400">
            No conversations yet
          </p>
        ) : (
          conversations.map((c) => {
            const active = c.id === activeId;
            return (
              <div
                key={c.id}
                onClick={() => onSelect(c.id)}
                className={`group flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-sm cursor-pointer transition ${
                  active
                    ? 'bg-lime-50 dark:bg-lime-950/40 text-neutral-900 dark:text-neutral-50'
                    : 'text-neutral-600 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800/60'
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    active ? 'bg-lime-400' : 'bg-transparent'
                  }`}
                />
                <span className="flex-1 truncate">{c.title || 'New chat'}</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(c.id);
                  }}
                  aria-label="Delete conversation"
                  className="opacity-0 group-hover:opacity-100 text-neutral-400 hover:text-red-500 transition"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path
                      d="M3 4h10M6.5 4V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1m-5 0 .5 9a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1L12 4"
                      stroke="currentColor"
                      strokeWidth="1.3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
