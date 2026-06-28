'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChatMessage } from './types';

// A conversation is one named scrollback. NOTE: turns within it are still
// independent Q&As — the backend agent is stateless, so this is presentation
// only (Claude-web-style history), not cross-turn memory. That upgrade lives in
// the agent (history-aware query rewrite) and is intentionally out of scope here.
export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

const STORAGE_KEY = 'finrag.conversations.v1';
const ACTIVE_KEY = 'finrag.activeConversation.v1';
const TITLE_MAX = 42;

function deriveTitle(text: string): string {
  const clean = text.trim().replace(/\s+/g, ' ');
  return clean.length > TITLE_MAX ? `${clean.slice(0, TITLE_MAX).trimEnd()}…` : clean;
}

function loadConversations(): Conversation[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Conversation[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/**
 * Client-side conversation store backed by localStorage (per-browser; no server,
 * so no cross-device sync — the right tradeoff for a public demo). Returns the
 * active conversation's messages plus the operations the UI needs.
 */
export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  // Gate persistence until after the first client read, so we never clobber
  // stored data with the empty SSR/initial state.
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const loaded = loadConversations();
    setConversations(loaded);
    const storedActive = window.localStorage.getItem(ACTIVE_KEY);
    setActiveId(
      storedActive && loaded.some((c) => c.id === storedActive)
        ? storedActive
        : loaded[0]?.id ?? null,
    );
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
  }, [conversations, hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    if (activeId) window.localStorage.setItem(ACTIVE_KEY, activeId);
    else window.localStorage.removeItem(ACTIVE_KEY);
  }, [activeId, hydrated]);

  const newConversation = useCallback((): string => {
    // Reuse an existing blank conversation rather than stacking empties (matches
    // Claude/ChatGPT: hitting "New chat" twice doesn't create two blanks).
    const blank = conversations.find((c) => c.messages.length === 0);
    if (blank) {
      setActiveId(blank.id);
      return blank.id;
    }
    const id = crypto.randomUUID();
    const conv: Conversation = {
      id,
      title: '',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    setConversations((prev) => [conv, ...prev]);
    setActiveId(id);
    return id;
  }, [conversations]);

  // Returns the active conversation id, lazily creating one if none exists.
  const ensureActive = useCallback((): string => {
    if (activeId) return activeId;
    return newConversation();
  }, [activeId, newConversation]);

  const selectConversation = useCallback((id: string) => setActiveId(id), []);

  const deleteConversation = useCallback((id: string) => {
    setConversations((prev) => {
      const remaining = prev.filter((c) => c.id !== id);
      setActiveId((cur) => (cur === id ? remaining[0]?.id ?? null : cur));
      return remaining;
    });
  }, []);

  // Patch one conversation's messages. Also backfills the title from the first
  // user turn so the sidebar has a label without a separate naming step.
  const updateMessages = useCallback(
    (id: string, fn: (prev: ChatMessage[]) => ChatMessage[]) => {
      setConversations((prev) =>
        prev.map((c) => {
          if (c.id !== id) return c;
          const messages = fn(c.messages);
          const firstUser = messages.find((m) => m.role === 'user');
          const title = c.title || (firstUser ? deriveTitle(firstUser.content) : '');
          return { ...c, messages, title, updatedAt: Date.now() };
        }),
      );
    },
    [],
  );

  const activeMessages = useMemo(
    () => conversations.find((c) => c.id === activeId)?.messages ?? [],
    [conversations, activeId],
  );

  return {
    conversations,
    activeId,
    activeMessages,
    hydrated,
    newConversation,
    ensureActive,
    selectConversation,
    deleteConversation,
    updateMessages,
  };
}
