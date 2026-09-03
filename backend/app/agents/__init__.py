"""Agents package — Layer 2, the LangGraph orchestrator. Router dispatches to discovery, cart_manager,
recommender, checkout, or support. Only checkout.py may touch Razorpay, and only via guardrails.gate_money_action
(see CLAUDE.md invariants #1-#2)."""
