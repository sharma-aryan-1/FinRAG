'use client';

import { RetrievedChunk } from '@/lib/types';

interface Props {
  chunk: RetrievedChunk;
  index: number;
  selected: boolean;
  onSelect: (chunk: RetrievedChunk) => void;
}

// Truncate text for the card preview. The full body lives in CitationViewer.
const PREVIEW_CHARS = 200;

export function ChunkCard({ chunk, index, selected, onSelect }: Props) {
  const isTable = chunk.chunk_type === 'table';
  const preview = isTable
    ? '[Table: open citation to view]'
    : chunk.text.length > PREVIEW_CHARS
      ? chunk.text.slice(0, PREVIEW_CHARS) + '…'
      : chunk.text;

  return (
    <button
      onClick={() => onSelect(chunk)}
      className={`w-full text-left rounded-lg border p-3 transition
        ${selected
          ? 'border-lime-500 bg-lime-50 dark:bg-lime-950/40'
          : 'border-neutral-200 dark:border-neutral-800 hover:border-neutral-400 dark:hover:border-neutral-600 bg-white dark:bg-neutral-800'
        }`}
    >
      <div className="flex items-center justify-between text-xs mb-1.5 font-mono">
        <span className="font-medium uppercase tracking-wider">
          [{index + 1}] {chunk.ticker} · FY{chunk.fiscal_year}
        </span>
        <span className="text-neutral-500 tabular-nums">
          rel {chunk.score.toFixed(3)}
        </span>
      </div>
      {chunk.section_title && (
        <div className="text-xs text-neutral-500 mb-1 truncate">
          {chunk.section_title}
        </div>
      )}
      <p className="text-sm text-neutral-700 dark:text-neutral-300 leading-snug whitespace-pre-line line-clamp-3">
        {preview}
      </p>
    </button>
  );
}
