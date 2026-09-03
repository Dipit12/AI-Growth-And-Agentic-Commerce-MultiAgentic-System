import type { AuditEvent } from "../../lib/api";
import EventCard from "./EventCard";

export default function Timeline({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-slate-400">No events found for this trace.</p>;
  }

  const baseMs = new Date(events[0].timestamp).getTime();

  return (
    <div className="space-y-2">
      {events.map((event) => (
        <EventCard key={event.id} event={event} baseMs={baseMs} />
      ))}
    </div>
  );
}
