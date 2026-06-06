"""Noodle built-in node library.

Importing this package registers all built-in nodes into the default registry.
"""

from noodle_nodes import ai_extra as ai_extra
from noodle_nodes import document_intelligence as document_intelligence
from noodle_nodes import ai_v2 as ai_v2
from noodle_nodes import builtin as builtin
from noodle_nodes import charts as charts
from noodle_nodes import cloud_devops as cloud_devops
from noodle_nodes import communication as communication
from noodle_nodes import datasets as datasets
from noodle_nodes import integrations as integrations
from noodle_nodes import llm as llm
from noodle_nodes import llm_evals as llm_evals
from noodle_nodes import llm_training as llm_training
from noodle_nodes import ml as ml
from noodle_nodes import synthetic_data as synthetic_data
from noodle_nodes import saas as saas
from noodle_nodes import storage as storage
from noodle_nodes import system as system
from noodle_nodes import transform_extra as transform_extra
from noodle_nodes.integrations_v2.providers import airtable as airtable_v2
from noodle_nodes.integrations_v2.providers import github as github_v2
from noodle_nodes.integrations_v2.providers import google_sheets as google_sheets_v2
from noodle_nodes.integrations_v2.providers import microsoft_outlook as microsoft_outlook_v2
from noodle_nodes.integrations_v2.providers import notion as notion_v2
from noodle_nodes.integrations_v2.providers import slack as slack_v2
from noodle_nodes.integrations_v2.providers import stripe as stripe_v2

__version__ = "0.0.1"
__all__ = [
    "ai_extra",
    "document_intelligence",
    "ai_v2",
    "builtin",
    "charts",
    "cloud_devops",
    "communication",
    "datasets",
    "integrations",
    "airtable_v2",
    "github_v2",
    "google_sheets_v2",
    "microsoft_outlook_v2",
    "notion_v2",
    "slack_v2",
    "stripe_v2",
    "llm",
    "llm_evals",
    "llm_training",
    "ml",
    "saas",
    "synthetic_data",
    "storage",
    "system",
    "transform_extra",
]
