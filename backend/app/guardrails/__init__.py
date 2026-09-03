"""Guardrails package — Layer 3, the money firewall. Every Razorpay write passes through gate.py here
before app/agents/checkout.py ever sees a response. Sub-modules are pure/deterministic where possible;
gate.py is the only public entry point agents should import (see CLAUDE.md invariant #2)."""

from app.guardrails.gate import gate_money_action

__all__ = ["gate_money_action"]
