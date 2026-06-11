'use client';

import { RetrievedChunk } from '@/lib/types';

interface Props {
  chunk: RetrievedChunk | null;
  onClose?: () => void;
}

export function CitationViewer({ chunk, onClose }: Props) {
  if (!chunk) return null;

  const isTable = chunk.chunk_type === 'table';

  return (
    <div className="h-full overflow-y-auto">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-neutral-200 dark:border-neutral-800 bg-white/90 dark:bg-neutral-900/90 backdrop-blur px-4 py-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-neutral-400">
          Source
        </span>
        {onClose && (
          <button
            onClick={onClose}
            aria-label="Close source panel"
            className="grid h-6 w-6 place-items-center rounded-md text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800 hover:text-neutral-700 dark:hover:text-neutral-200 transition"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>
      <div className="p-6">
      <div className="space-y-1 mb-4">
        <h2 className="font-serif text-2xl font-normal tracking-tight">
          {chunk.company_name}
        </h2>
        <div className="font-mono text-[11px] text-neutral-500 space-x-2">
          <span className="font-medium">{chunk.ticker}</span>
          <span>·</span>
          <span>FY{chunk.fiscal_year}</span>
          <span>·</span>
          <span>{chunk.period_of_report}</span>
          <span>·</span>
          <span className="uppercase tracking-wider">{chunk.chunk_type}</span>
        </div>
        {chunk.section_title && (
          <div className="text-sm text-neutral-600 dark:text-neutral-400 pt-1">
            {chunk.section_title}
          </div>
        )}
      </div>

      <div className="text-xs text-neutral-500 mb-4 flex items-center gap-4">
        <span>relevance: <span className="tabular-nums">{chunk.score.toFixed(3)}</span></span>
        <span className="font-mono">chunk: {chunk.chunk_id}</span>
      </div>

      <div className="border-t border-neutral-200 dark:border-neutral-800 pt-4">
        {isTable ? (
          // Tables come through as HTML — render with `dangerouslySetInnerHTML`.
          // Safe here because the source is our own ingestion pipeline (SEC
          // filings); never do this for user-supplied content.
          <div
            className="prose prose-sm dark:prose-invert max-w-none [&_table]:text-xs [&_table]:border [&_td]:border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:px-2 [&_th]:py-1 [&_th]:bg-neutral-100 dark:[&_th]:bg-neutral-800"
            dangerouslySetInnerHTML={{ __html: chunk.text }}
          />
        ) : (
          <p className="text-sm leading-relaxed whitespace-pre-line text-neutral-800 dark:text-neutral-200">
            {chunk.text}
          </p>
        )}
      </div>

      <div className="mt-6 pt-4 border-t border-neutral-200 dark:border-neutral-800">
        <a
          href={chunk.sec_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-lime-600 dark:text-lime-400 hover:underline"
        >
          View on SEC.gov ↗
        </a>
        <div className="text-xs text-neutral-400 mt-1">
          Accession: <span className="font-mono">{chunk.accession_number}</span>
        </div>
      </div>
      </div>
    </div>
  );
}
