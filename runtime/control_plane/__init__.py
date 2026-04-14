"""Control plane module exports."""

from .command_templates import (
	CommandTemplateError,
	CommandTemplateLibrary,
	CommandTemplateRecord,
	ResolvedCommand,
)
from .connector_manager import (
	AdapterRegistration,
	ConnectorExecutionResult,
	ConnectorManager,
	ConnectorManagerError,
	TransportAdapter,
)
from .host_inventory import HostInventoryError, HostInventoryService, HostRecord
from .policy_overlay import (
	CommandScopePolicy,
	ControlPlanePolicyDecision,
	ControlPlanePolicyOverlay,
	ControlPlanePolicyOverlayError,
	ControlPlanePolicyRequest,
	HostScopePolicy,
	OperatorScopePolicy,
)
from .ssh_connector import SshConnectorError, SshExecutionRequest, SshHostCredentials, SshRemoteConnector

__all__ = [
	"HostInventoryService",
	"HostRecord",
	"HostInventoryError",
	"TransportAdapter",
	"AdapterRegistration",
	"ConnectorExecutionResult",
	"ConnectorManager",
	"ConnectorManagerError",
	"SshHostCredentials",
	"SshExecutionRequest",
	"SshRemoteConnector",
	"SshConnectorError",
	"CommandTemplateRecord",
	"ResolvedCommand",
	"CommandTemplateLibrary",
	"CommandTemplateError",
	"ControlPlanePolicyRequest",
	"ControlPlanePolicyDecision",
	"HostScopePolicy",
	"OperatorScopePolicy",
	"CommandScopePolicy",
	"ControlPlanePolicyOverlay",
	"ControlPlanePolicyOverlayError",
]
