import { useState } from "react";

import type { AuditEvent } from "../../lib/api";

const ACTOR_STYLES: Record<string, string> = {
  router: "border-cyan-300 bg-cyan-50 text-cyan-800",
  discovery: "border-blue-300 bg-blue-50 text-blue-800",
  recommender: "border-purple-300 bg-purple-50 text-purple-800",
  cart_manager: "border-amber-300 bg-amber-50 text-amber-800",
  support: "border-slate-300 bg-slate-50 text-slate-700",
  checkout: "border-emerald-400 bg-emerald-50 text-emerald-800",
  guardrail: "border-red-400 bg-red-50 text-red-800",
  razorpay: "border-emerald-600 bg-emerald-100 text-emerald-900",
  human: "border-indigo-400 bg-indigo-50 text-indigo-800",
};

function formatRelative(baseMs: number, timestamp: string): string {
  const deltaSeconds = (new Date(timestamp).getTime() - baseMs) / 1000;
  return `+${deltaSeconds.toFixed(3)}s`;
}

export default function EventCard({ event, baseMs }: { event: AuditEvent; baseMs: number }) {
  const [expanded, setExpanded] = useState(false);
  const style = ACTOR_STYLES[event.actor] ?? "border-slate-300 bg-white text-slate-700";

  return (
    <div className={`rounded-lg border-l-4 p-3 shadow-sm ${style}`}>
      <button className="flex w-full items-start justify-between text-left" onClick={() => setExpanded((v) => !v)}>
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded bg-white/70 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
              {event.actor}
            </span>
            <span className="text-sm font-medium">{event.event_type}</span>
          </div>
          {event.reason ? <p className="mt-1 text-xs opacity-80">{event.reason}</p> : null}
        </div>
        <span className="ml-3 flex-shrink-0 text-[11px] font-mono opacity-70">
          {formatRelative(baseMs, event.timestamp)}
        </span>
      </button>
      {expanded ? (
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-white/60 p-2 text-[11px] leading-relaxed">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
