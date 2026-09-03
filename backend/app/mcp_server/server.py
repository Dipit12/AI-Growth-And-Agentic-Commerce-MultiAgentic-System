"""MCP server bootstrap. Layer 1 (surfaces). Registers app/mcp_server/tools.py's functions onto an
MCPServer instance and runs it — `stdio` for local testing (e.g. Claude Desktop), `streamable-http`
for the demo so a remote agentic client can connect.

Run with: python -m app.mcp_server.server            (stdio)
          python -m app.mcp_server.server --http      (streamable-http, for the demo)
"""

import sys

from mcp.server.mcpserver import MCPServer

from app.mcp_server import tools

mcp_server = MCPServer(
    name="razorpay-buildathon-commerce",
    instructions=(
        "Tools for shopping a demo storefront: search the catalog, manage a cart, and check out "
        "via a Razorpay test-mode payment. Every checkout is gated by merchant policy and may "
        "require human approval for large amounts — a slow checkout_cart response is expected in "
        "that case, not a failure."
    ),
)

for tool_fn in (
    tools.search_catalog,
    tools.get_product,
    tools.create_cart,
    tools.add_to_cart,
    tools.remove_from_cart,
    tools.checkout_cart,
):
    mcp_server.add_tool(tool_fn)


def main() -> None:
    transport = "streamable-http" if "--http" in sys.argv else "stdio"
    mcp_server.run(transport=transport)


if __name__ == "__main__":
    main()
