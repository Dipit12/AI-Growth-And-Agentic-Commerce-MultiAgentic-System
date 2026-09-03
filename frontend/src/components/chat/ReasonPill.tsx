import { useState } from "react";

import type { ReasonTrace } from "../../lib/api";

const SOURCE_STYLES: Record<ReasonTrace["approval_source"], string> = {
  auto: "bg-emerald-100 text-emerald-700",
  human: "bg-amber-100 text-amber-700",
  denied: "bg-red-100 text-red-700",
};

export default function ReasonPill({ trace }: { trace: ReasonTrace }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-1.5">
      <button
        onClick={() => setExpanded((v) => !v)}
        className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${SOURCE_STYLES[trace.approval_source]}`}
      >
        why? {expanded ? "▲" : "▼"}
      </button>
      {expanded ? (
        <div className="mt-1.5 rounded-md border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600">
          <p>{trace.summary}</p>
          <dl className="mt-1.5 grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px] text-slate-500">
            <dt>Rules matched</dt>
            <dd>{trace.rules_matched.join(", ") || "—"}</dd>
            <dt>Spend before → after</dt>
            <dd>
              ₹{(trace.session_spend_before / 100).toFixed(2)} → ₹{(trace.session_spend_after / 100).toFixed(2)}
            </dd>
            <dt>Idempotency key</dt>
            <dd className="truncate">{trace.idempotency_key}</dd>
          </dl>
        </div>
      ) : null}
    </div>
  );
}
