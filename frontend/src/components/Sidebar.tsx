'use client';

import { Conversation } from '@/lib/conversations';

const GITHUB_URL = 'https://github.com/sharma-aryan-1/FinRAG';
const LINKEDIN_URL = 'https://www.linkedin.com/in/sharmaaryan25/';

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

      {/* Attribution — whose project this is. Recruiter-facing: name + source. */}
      <div className="border-t border-neutral-200 dark:border-neutral-800 px-3 py-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-neutral-400">
          Built by Aryan Sharma
        </p>
        <div className="mt-1.5 flex items-center gap-3 text-neutral-500 dark:text-neutral-400">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Source on GitHub"
            className="flex items-center gap-1 text-xs hover:text-neutral-900 dark:hover:text-neutral-100 transition"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
            Source
          </a>
          <a
            href={LINKEDIN_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="LinkedIn"
            className="flex items-center gap-1 text-xs hover:text-neutral-900 dark:hover:text-neutral-100 transition"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M13.6 0H2.4A2.4 2.4 0 0 0 0 2.4v11.2A2.4 2.4 0 0 0 2.4 16h11.2a2.4 2.4 0 0 0 2.4-2.4V2.4A2.4 2.4 0 0 0 13.6 0zM4.8 13.6H2.4V6.4h2.4v7.2zM3.6 5.3a1.4 1.4 0 1 1 0-2.8 1.4 1.4 0 0 1 0 2.8zm10 8.3h-2.4V9.9c0-.9-.02-2-1.23-2-1.23 0-1.42.96-1.42 1.95v3.75H6.15V6.4h2.3v.98h.03c.32-.6 1.1-1.23 2.27-1.23 2.43 0 2.88 1.6 2.88 3.68v3.77z" />
            </svg>
            LinkedIn
          </a>
        </div>
      </div>
    </aside>
  );
}
