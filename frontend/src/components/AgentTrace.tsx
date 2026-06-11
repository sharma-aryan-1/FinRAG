'use client';

import { useState } from 'react';
import { TraceEvent } from '@/lib/types';

// Route → badge colour. Mirrors the agent's three routes (vector | sql | both).
const ROUTE_STYLES: Record<string, string> = {
  vector: 'bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-300',
  sql: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300',
  both: 'bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300',
};

// The whole panel reads as the agent's "thinking" — distinct from the answer:
// lime-tinted, italic, persistent. It stays after the run completes (collapsed
// to a one-line summary on demand) rather than vanishing, the way Claude/ChatGPT
// keep a foldable thought trace.
function Row({
  icon,
  label,
  children,
}: {
  icon: string;
  label?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex gap-2 text-xs italic">
      <span className="select-none text-lime-400/70 mt-px not-italic">{icon}</span>
      <div className="min-w-0 flex-1">
        {label && <div className="text-lime-700/90 dark:text-lime-300/90">{label}</div>}
        {children}
      </div>
    </div>
  );
}

function ToolCallStep({ data }: { data: Extract<TraceEvent, { type: 'tool_call' }>['data'] }) {
  const [open, setOpen] = useState(false);
  // sql_query surfaces the generated SQL on result.sql — the headline payload.
  const sql = (data.result as { sql?: string } | null)?.sql;

  return (
    <Row
      icon="⚙"
      label={
        <button
          onClick={() => setOpen((o) => !o)}
          className="text-left hover:text-lime-900 dark:hover:text-lime-100"
        >
          Called <span className="font-mono font-medium not-italic">{data.tool}</span>
          <span className="ml-1 text-lime-400">{open ? '▾' : '▸'}</span>
        </button>
      }
    >
      {open && (
        <div className="mt-1 space-y-1 not-italic">
          <pre className="text-[11px] bg-neutral-100 dark:bg-neutral-800 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
            {sql ?? JSON.stringify(data.args, null, 2)}
          </pre>
          <pre className="text-[11px] bg-neutral-100 dark:bg-neutral-800 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono text-neutral-500">
            {JSON.stringify(data.result, null, 2)}
          </pre>
        </div>
      )}
    </Row>
  );
}

function Step({ ev }: { ev: TraceEvent }) {
  switch (ev.type) {
    case 'rewrite':
      return (
        <Row icon="✎" label="Rewrote query">
          <div className="text-lime-500/80 dark:text-lime-400/80 truncate">{ev.data.rewritten}</div>
        </Row>
      );
    case 'route':
      return (
        <Row
          icon="⇄"
          label={
            <span>
              Routed to{' '}
              <span
                className={`px-1.5 py-0.5 rounded font-mono text-[10px] uppercase tracking-wider not-italic ${ROUTE_STYLES[ev.data.route] ?? ''}`}
              >
                {ev.data.route}
              </span>
            </span>
          }
        />
      );
    case 'retrieve':
      return <Row icon="▤" label={`Retrieved ${ev.data.n_chunks} chunks`} />;
    case 'thought':
      // The model's own words — render verbatim, italic, as a quoted aside.
      return (
        <Row icon="💭">
          <div className="whitespace-pre-wrap text-lime-600/90 dark:text-lime-300/90 border-l-2 border-lime-200 dark:border-lime-800/60 pl-2">
            {ev.data.text}
          </div>
        </Row>
      );
    case 'tool_call':
      return <ToolCallStep data={ev.data} />;
    case 'fallback':
      return (
        <Row icon="⚠" label={<span className="text-amber-600 not-italic">Fallback: {ev.data.reason}</span>} />
      );
    default:
      return null;
  }
}

export function AgentTrace({ trace, running }: { trace: TraceEvent[]; running?: boolean }) {
  // Default open so the reasoning never just disappears; a header toggle lets the
  // user fold it away once they've seen it (Claude/ChatGPT-style "show thinking").
  const [open, setOpen] = useState(true);
  if (trace.length === 0) return null;

  return (
    <div className="rounded-lg border border-lime-200/70 dark:border-lime-900/50 bg-lime-50/50 dark:bg-lime-950/20 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-lime-600 dark:text-lime-300 hover:bg-lime-100/40 dark:hover:bg-lime-900/20 transition"
      >
        <span className={running ? 'animate-pulse' : ''}>✦</span>
        <span className="font-mono text-[10px] uppercase tracking-[0.16em]">
          {running ? 'Thinking…' : `Thought process · ${trace.length} steps`}
        </span>
        <span className="ml-auto text-lime-400">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-3 pt-0.5">
          {trace.map((ev, i) => (
            <Step key={i} ev={ev} />
          ))}
        </div>
      )}
    </div>
  );
}
