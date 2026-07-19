"""LangGraph nodes.

Each node is an async function `(state: GraphState) -> dict` that returns
a partial state (LangGraph merges it into the running state).
"""