"""Noodle built-in node library.

Importing this package registers all built-in nodes into the default registry.
"""

from noodle_nodes import builtin as builtin
from noodle_nodes import integrations as integrations

__version__ = "0.0.1"
__all__ = ["builtin", "integrations"]
