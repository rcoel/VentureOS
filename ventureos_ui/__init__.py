"""VentureOS UI — DB, scoring, memo, and Streamlit dashboard (Person B).

Consumes the LangGraph pipeline output (demo_data/**/*.json). Never imports
from ventureos.graph / ventureos.nodes / ventureos.tools — only from the
frozen ventureos.models Pydantic contract.
"""