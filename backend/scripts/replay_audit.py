"""CLI that pretty-prints an audit trail for a trace_id — used for debugging and for the demo video.

Run with: python -m scripts.replay_audit <trace_id> [--verbose]
"""

import argparse
import asyncio
import uuid

from rich.console import Console
from rich.text import Text
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.db.session import async_session_factory
from app.models.audit import redact_payload

console = Console()

_ACTOR_COLORS = {
    "router": "cyan",
    "discovery": "blue",
    "recommender": "magenta",
    "cart_manager": "yellow",
    "checkout": "bold green",
    "guardrail": "bold red",
    "razorpay": "bold white on dark_green",
    "human": "bold white on dark_blue",
}


async def fetch_events(trace_id: uuid.UUID) -> list[AuditEvent]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditEvent).where(AuditEvent.trace_id == trace_id).order_by(AuditEvent.timestamp.asc())
        )
        return list(result.scalars().all())


def render(events: list[AuditEvent], verbose: bool) -> None:
    if not events:
        console.print("[bold red]No events found for that trace_id.[/bold red]")
        return

    start = events[0].timestamp
    for event in events:
        delta = (event.timestamp - start).total_seconds()
        color = _ACTOR_COLORS.get(event.actor, "white")

        line = Text()
        line.append(f"+{delta:>7.3f}s  ", style="dim")
        line.append(f"[{event.actor:^12}] ", style=color)
        line.append(event.event_type)
        if event.reason:
            line.append(f"  — {event.reason}", style="italic dim")
        console.print(line)

        if verbose:
            console.print(redact_payload(event.payload), style="dim")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay an audit trail for a trace_id.")
    parser.add_argument("trace_id", type=str)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    events = asyncio.run(fetch_events(uuid.UUID(args.trace_id)))
    render(events, verbose=args.verbose)


if __name__ == "__main__":
    main()
