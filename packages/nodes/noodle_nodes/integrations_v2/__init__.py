"""Spec-driven integration runtime foundation.

This package is the v2 replacement path for provider-specific HTTP wrappers.
It keeps node execution Python-native while centralizing provider transport,
errors, retries, and future operation specs.
"""

from noodle_nodes.integrations_v2.dynamic_options import call_loader, register_loader
from noodle_nodes.integrations_v2.errors import ProviderError
from noodle_nodes.integrations_v2.registry import (
    get_registered_provider_trigger,
    register_operation,
    register_provider_trigger,
    registered_provider_trigger_specs,
)
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationSpec,
    ProviderTriggerSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport, RetryPolicy

__all__ = [
    "IntegrationSpec",
    "OperationSpec",
    "ProviderError",
    "ProviderTriggerSpec",
    "ProviderTransport",
    "RetryPolicy",
    "call_loader",
    "get_registered_provider_trigger",
    "register_loader",
    "register_operation",
    "register_provider_trigger",
    "registered_provider_trigger_specs",
]
