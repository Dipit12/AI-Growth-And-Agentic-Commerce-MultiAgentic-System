import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import Timeline from "../components/audit/Timeline";
import { getAuditTrail, type AuditEvent } from "../lib/api";

export default function AuditRoute() {
  const { traceId } = useParams<{ traceId?: string }>();
  const navigate = useNavigate();
  const [inputValue, setInputValue] = useState(traceId ?? "");
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchTrace = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getAuditTrail(id);
      setEvents(result);
    } catch (err) {
      setError((err as Error).message);
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (traceId) {
      setInputValue(traceId);
      void fetchTrace(traceId);
    }
  }, [traceId, fetchTrace]);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (inputValue) navigate(`/audit/${inputValue}`);
  }

  function handleDownload() {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `trace-${traceId ?? "export"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 overflow-y-auto p-6">
      <h1 className="text-lg font-semibold text-slate-900">Audit Viewer</h1>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="Paste a trace_id…"
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm font-mono focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
        />
        <button
          type="submit"
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          Load trace
        </button>
        {events.length > 0 ? (
          <button
            type="button"
            onClick={handleDownload}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100"
          >
            Download JSON
          </button>
        ) : null}
      </form>

      {loading ? <p className="text-sm text-slate-400">Loading…</p> : null}
      {error ? <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}

      {!loading && !error ? <Timeline events={events} /> : null}
    </div>
  );
}
