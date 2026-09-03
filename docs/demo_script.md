# Demo video script (5 minutes)

Beats below assume the stack is already running (`docker compose up -d`, catalog seeded and
indexed, backend and frontend up) and a terminal with `python -m scripts.replay_audit` handy. Don't
show real API keys on screen — use the demo `.env` values or blur the terminal when `.env` is
sourced.

## 0:00 – 0:45 — The problem

> "Agentic commerce means letting an AI — a chat widget, or an external agent like Claude or
> ChatGPT connecting over MCP — actually spend a user's money. That's the scary part. Most demos
> stop at 'the AI can add things to a cart.' We built the part that comes after: what stops an LLM
> from placing an order it shouldn't, what happens when Razorpay times out mid-charge, and what a
> merchant sees when they need to approve something themselves."

Show the architecture diagram from `ARCHITECTURE.md` for ~15 seconds — five layers, and point at the
guardrails layer: "Every single write to Razorpay passes through one function.
`gate_money_action`. That's not a slide — it's an enforced invariant with a test that fails the
build if anyone routes around it."

## 0:45 – 2:15 — Happy path (chat widget)

1. Open the Shopper Chat. Type: *"I'm looking for a wireless keyboard."*
   Show the product cards rendering, click "Add to cart."
2. Type: *"what goes well with this?"* — recommender surfaces a mouse, one sentence of reasoning
   each.
3. Type: *"checkout"*. Response comes back with a confirmation. Click the **why?** pill under the
   response — expand it to show the reason trace: rules matched, spend before/after, idempotency
   key.
4. Copy the `trace_id` shown under the message, switch to the **Audit Viewer** tab, paste it in.
   Narrate the timeline as it renders: `guardrail.intent_declared` → `guardrail.policy.allow` →
   `razorpay.create_order.succeeded` — color-coded, expandable, relative timestamps. *"This is the
   same trail for every purchase, whether a human typed it or an AI agent called it through MCP."*

## 2:15 – 3:00 — MCP demo

Switch to a terminal (or Claude Desktop connected to the MCP server). Show `python -m
app.mcp_server.server` running, then drive it with an MCP client:

```
search_catalog(query="yoga mat")
create_cart()
add_to_cart(cart_id=..., sku="FIT-001", qty=1)
checkout_cart(cart_id=..., customer_email="demo@example.com")
```

Pull up the returned `trace_id` in the Audit Viewer next to the chat-widget trace from earlier.
*"Same guardrails, same audit shape, same checkout node under the hood — an external AI buyer gets
exactly the same money firewall a human does."*

## 3:00 – 4:15 — Failure demos (the actual pitch)

Run `python -m scripts.demo_denial` in a visible terminal. Narrate as it prints:

> "This merchant capped transactions at ₹500. I'm attempting a ₹5000 checkout. No Razorpay call
> happens — policy catches it before the network. The cart survives untouched."

Paste the printed `trace_id` into the Audit Viewer. Point at the `guardrail.policy.deny` event and
its reason text.

Then run `python -m scripts.demo_timeout`:

> "Now I'm simulating Razorpay timing out three times in a row — real failure-injection code, not a
> mocked demo. Watch: three timeout events, a `retry_exhausted`, then an automatic fallback to a
> payment link — same idempotency key throughout, so there's no risk of a double charge."

Show the audit trail for that trace_id: the three retry attempts, the exhaustion, the fallback
success — one clean story, not a stack trace the user never sees.

## 4:15 – 4:45 — Merchant console + human-in-the-loop

Set the auto-approve threshold low in the Merchant Console, trigger a chat checkout that exceeds it,
show the pending-approval banner appear in the chat widget in real time, then switch to the
Merchant Console (already open in a second window) and click **Approve** — show the chat widget
unblock within about a second, backed by the WebSocket push, not polling.

## 4:45 – 5:00 — Close

> "Five layers, one money firewall, one audit trail — whether a human or an AI agent is spending
> the money. Repo's public, this whole demo is reproducible from a fresh clone in under fifteen
> minutes. Thanks for watching."

Cut to the repo README on screen for the last few seconds.
