"""MCP server package — Layer 1 (surfaces). Exposes the merchant catalog and checkout to external
AI buyers (Claude, ChatGPT, agentic browsers) as callable tools, wired to the same session_store,
guardrails, and catalog services the chat widget uses (see app/mcp_server/tools.py).
"""
