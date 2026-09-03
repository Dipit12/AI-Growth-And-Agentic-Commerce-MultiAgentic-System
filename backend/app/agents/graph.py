"""Assembles every agent node into a LangGraph state machine. Layer 2 (agents) — the orchestrator.

Router is the entry point and conditionally dispatches to discovery, cart_manager, checkout,
recommender, or support based on `current_intent`. Every non-router node is terminal *within one
graph invocation*: each HTTP turn to /chat is one `ainvoke` call over the full message history, and
the "loop back to the router for follow-up turns" behavior described in plan.md happens naturally
because the *next* HTTP request re-enters at router with the updated history — not via an in-graph
back-edge, which would have no new input to react to and could spin forever.
"""

from langgraph.graph import END, StateGraph

from app.agents.cart_manager import cart_manager_node
from app.agents.checkout import checkout_node
from app.agents.discovery import discovery_node
from app.agents.recommender import recommender_node
from app.agents.router import router_node
from app.agents.state import AgentState
from app.agents.support import support_node

_INTENT_TO_NODE = {
    "discover": "discovery",
    "recommend": "recommender",
    "cart": "cart_manager",
    "checkout": "checkout",
    "support": "support",
}


def _route_from_router(state: AgentState) -> str:
    return _INTENT_TO_NODE.get(state.get("current_intent", "support"), "support")


def build_graph() -> object:
    graph: StateGraph = StateGraph(AgentState)

    graph.add_node("router", router_node)
    graph.add_node("discovery", discovery_node)
    graph.add_node("cart_manager", cart_manager_node)
    graph.add_node("checkout", checkout_node)
    graph.add_node("recommender", recommender_node)
    graph.add_node("support", support_node)

    graph.set_entry_point("router")
    graph.add_conditional_edges("router", _route_from_router, _INTENT_TO_NODE)

    for node_name in _INTENT_TO_NODE.values():
        graph.add_edge(node_name, END)

    return graph.compile()


chat_graph = build_graph()
