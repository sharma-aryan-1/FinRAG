'use client';

import { RetrievedChunk } from '@/lib/types';

interface Props {
  chunk: RetrievedChunk | null;
}

export function CitationViewer({ chunk }: Props) {
  if (!chunk) {
    return (
      <div className="h-full flex items-center justify-center text-neutral-400 text-sm p-8 text-center">
        Click a citation in the chat to see the full source passage here.
      </div>
    );
  }

  const isTable = chunk.chunk_type === 'table';

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="space-y-1 mb-4">
        <h2 className="text-lg font-semibold">
          {chunk.company_name}
        </h2>
        <div className="text-xs text-neutral-500 space-x-2">
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
          className="text-xs text-blue-600 hover:underline"
        >
          View on SEC.gov ↗
        </a>
        <div className="text-xs text-neutral-400 mt-1">
          Accession: <span className="font-mono">{chunk.accession_number}</span>
        </div>
      </div>
    </div>
  );
}
