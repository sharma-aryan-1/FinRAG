'use client';

import { useState } from 'react';
import { TraceEvent } from '@/lib/types';

// Route → badge colour. Mirrors the agent's three routes (vector | sql | both).
const ROUTE_STYLES: Record<string, string> = {
  vector: 'bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-300',
  sql: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300',
  both: 'bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300',
};

function Row({
  icon,
  label,
  children,
}: {
  icon: string;
  label: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex gap-2 text-xs">
      <span className="select-none text-neutral-400 mt-px">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-neutral-600 dark:text-neutral-400">{label}</div>
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
          className="text-left hover:text-neutral-900 dark:hover:text-neutral-100"
        >
          Called <span className="font-mono font-medium">{data.tool}</span>
          <span className="ml-1 text-neutral-400">{open ? '▾' : '▸'}</span>
        </button>
      }
    >
      {open && (
        <div className="mt-1 space-y-1">
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
          <div className="text-neutral-500 italic truncate">{ev.data.rewritten}</div>
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
                className={`px-1.5 py-0.5 rounded font-medium ${ROUTE_STYLES[ev.data.route] ?? ''}`}
              >
                {ev.data.route}
              </span>
            </span>
          }
        />
      );
    case 'retrieve':
      return <Row icon="▤" label={`Retrieved ${ev.data.n_chunks} chunks`} />;
    case 'tool_call':
      return <ToolCallStep data={ev.data} />;
    case 'fallback':
      return (
        <Row icon="⚠" label={<span className="text-amber-600">Fallback: {ev.data.reason}</span>} />
      );
    default:
      return null;
  }
}

export function AgentTrace({ trace }: { trace: TraceEvent[] }) {
  if (trace.length === 0) return null;
  return (
    <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-900/50 p-3 space-y-2">
      {trace.map((ev, i) => (
        <Step key={i} ev={ev} />
      ))}
    </div>
  );
}
