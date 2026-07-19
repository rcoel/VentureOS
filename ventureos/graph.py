"""LangGraph assembly — wires the 8 nodes with conditional edges.

Graph shape:
    START → intake → screening --FAIL--> END
                        │ PASS
                        ▼
                     sourcing → extraction → verification
                        → attributes_rollup → market_research
                        → activation → END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ventureos.nodes.activation import activation_node
from ventureos.nodes.attributes_rollup import attributes_rollup_node
from ventureos.nodes.extraction import extraction_node
from ventureos.nodes.intake import intake_node
from ventureos.nodes.market_research import market_research_node
from ventureos.nodes.screening import screening_node
from ventureos.nodes.sourcing import sourcing_node
from ventureos.nodes.verification import verification_node
from ventureos.state import GraphState


def _screening_router(state: GraphState) -> str:
    """PASS → sourcing; FAIL → END."""
    return "sourcing" if state.get("screen_status") == "PASS" else END


def build_graph():
    """Compile and return the LangGraph pipeline."""
    g = StateGraph(GraphState)

    # Nodes
    g.add_node("intake", intake_node)
    g.add_node("screening", screening_node)
    g.add_node("sourcing", sourcing_node)
    g.add_node("extraction", extraction_node)
    g.add_node("verification", verification_node)
    g.add_node("attributes_rollup", attributes_rollup_node)
    g.add_node("market_research", market_research_node)
    g.add_node("activation", activation_node)

    # Entry
    g.add_edge(START, "intake")
    g.add_edge("intake", "screening")

    # Screening → PASS goes to sourcing, FAIL goes straight to END
    g.add_conditional_edges(
        "screening",
        _screening_router,
        {"sourcing": "sourcing", END: END},
    )

    # Linear pipeline through the intelligence layer
    g.add_edge("sourcing", "extraction")
    g.add_edge("extraction", "verification")
    g.add_edge("verification", "attributes_rollup")
    g.add_edge("attributes_rollup", "market_research")
    g.add_edge("market_research", "activation")
    g.add_edge("activation", END)

    return g.compile()