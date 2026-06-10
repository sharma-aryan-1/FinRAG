'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Renders the agent's markdown answer (headers, GFM tables, lists, [N] cites).
// We style via Tailwind arbitrary-variant selectors instead of the typography
// plugin (not installed) — same approach CitationViewer uses for its tables.
const MD_STYLES = [
  'text-sm leading-relaxed text-neutral-800 dark:text-neutral-200',
  '[&_h1]:text-base [&_h1]:font-semibold [&_h1]:mt-3 [&_h1]:mb-1',
  '[&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1',
  '[&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1',
  '[&_p]:my-1.5',
  '[&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:my-1.5',
  '[&_strong]:font-semibold',
  '[&_code]:font-mono [&_code]:text-xs [&_code]:bg-neutral-100 dark:[&_code]:bg-neutral-800 [&_code]:px-1 [&_code]:rounded',
  '[&_table]:text-xs [&_table]:border-collapse [&_table]:my-2 [&_table]:block [&_table]:overflow-x-auto',
  '[&_td]:border [&_td]:border-neutral-300 dark:[&_td]:border-neutral-700 [&_td]:px-2 [&_td]:py-1',
  '[&_th]:border [&_th]:border-neutral-300 dark:[&_th]:border-neutral-700 [&_th]:px-2 [&_th]:py-1 [&_th]:bg-neutral-100 dark:[&_th]:bg-neutral-800',
  '[&_blockquote]:border-l-2 [&_blockquote]:border-neutral-300 [&_blockquote]:pl-3 [&_blockquote]:text-neutral-600 dark:[&_blockquote]:text-neutral-400',
].join(' ');

export function AgentAnswer({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className={MD_STYLES}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      {streaming && (
        <span className="inline-block w-1.5 h-4 bg-neutral-400 align-text-bottom ml-0.5 animate-pulse" />
      )}
    </div>
  );
}
