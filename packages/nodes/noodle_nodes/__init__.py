"""Noodle built-in node library.

Importing this package registers all built-in nodes into the default registry.
"""

from noodle_nodes import ai_extra as ai_extra
from noodle_nodes import builtin as builtin
from noodle_nodes import charts as charts
from noodle_nodes import cloud_devops as cloud_devops
from noodle_nodes import communication as communication
from noodle_nodes import datasets as datasets
from noodle_nodes import integrations as integrations
from noodle_nodes import ml as ml
from noodle_nodes import saas as saas
from noodle_nodes import storage as storage
from noodle_nodes import system as system
from noodle_nodes import transform_extra as transform_extra

__version__ = "0.0.1"
__all__ = [
    "ai_extra",
    "builtin",
    "charts",
    "cloud_devops",
    "communication",
    "datasets",
    "integrations",
    "ml",
    "saas",
    "storage",
    "system",
    "transform_extra",
]
