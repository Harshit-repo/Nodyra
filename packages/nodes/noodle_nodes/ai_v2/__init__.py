"""AI v2 node implementations — typed supplier/executable nodes.

Importing this package registers all AI v2 supplier nodes into the default
registry.

Submodules:
- providers/openai.py     — OpenAI, Azure OpenAI, Ollama, OpenRouter adapters
- providers/anthropic.py  — Anthropic adapter
- providers/embeddings.py — OpenAI-compatible + Cohere embedding adapters
- agents.py               — Engine-mediated AI Agent v2
- document_loaders.py     — Text/file/URL document loader suppliers
- models.py               — Chat Model supplier nodes
- embeddings.py           — Embedding Model supplier node
- memory.py               — Buffer Memory supplier node
- tools.py                — HTTP / Workflow tool supplier nodes
- text_splitters.py       — Recursive text splitter
- vectorstores.py         — Vector store suppliers and upsert nodes
- retrievers.py           — Vector retriever and RAG chain nodes
- output_parsers.py       — Structured output parser supplier node
- guardrails.py           — Guardrail supplier node
"""

from noodle_nodes.ai_v2 import agent_tools as agent_tools
from noodle_nodes.ai_v2 import agents as agents
from noodle_nodes.ai_v2 import mcp as mcp
from noodle_nodes.ai_v2 import document_loaders as document_loaders
from noodle_nodes.ai_v2 import embeddings as embeddings
from noodle_nodes.ai_v2 import guardrails as guardrails
from noodle_nodes.ai_v2 import memory as memory
from noodle_nodes.ai_v2 import model_options as model_options
from noodle_nodes.ai_v2 import models as models
from noodle_nodes.ai_v2 import output_parsers as output_parsers
from noodle_nodes.ai_v2 import retrievers as retrievers
from noodle_nodes.ai_v2 import text_splitters as text_splitters
from noodle_nodes.ai_v2 import tools as tools
from noodle_nodes.ai_v2 import vectorstores as vectorstores

__all__ = [
    "agent_tools",
    "document_loaders",
    "embeddings",
    "agents",
    "mcp",
    "guardrails",
    "memory",
    "model_options",
    "models",
    "output_parsers",
    "retrievers",
    "text_splitters",
    "tools",
    "vectorstores",
]
