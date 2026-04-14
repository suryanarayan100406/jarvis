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
from .physical_connector_sdk import (
	PhysicalCapabilityDefinition,
	PhysicalConnectorAdapter,
	PhysicalConnectorExecutionResult,
	PhysicalConnectorManifest,
	PhysicalConnectorRegistration,
	PhysicalConnectorSDK,
	PhysicalConnectorSdkError,
)
from .physical_device_registry import (
	PhysicalDeviceRecord,
	PhysicalDeviceRegistry,
	PhysicalDeviceRegistryError,
)
from .connector_health import (
	ConnectorHealthError,
	ConnectorHealthMonitor,
	ConnectorHealthSummary,
	HostHealthResult,
	RetryPolicy,
)
from .dry_run_gate import (
	DryRunClassification,
	DryRunExecutionGate,
	DryRunExecutionOutcome,
	DryRunGateError,
	DryRunPreview,
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
from .parallel_orchestrator import (
	HostOperationRequest,
	HostOperationResult,
	ParallelExecutionResult,
	ParallelHostOrchestrator,
	ParallelOrchestratorError,
)
from .rollback_actions import (
	RollbackAction,
	RollbackActionError,
	RollbackActionManager,
	RollbackRoutinePlan,
	RollbackRoutineResult,
)
from .result_reporter import (
	AggregatedControlPlaneReport,
	ControlPlaneResultReporter,
	HostReportEntry,
	ResultReporterError,
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
	"PhysicalConnectorAdapter",
	"PhysicalCapabilityDefinition",
	"PhysicalConnectorManifest",
	"PhysicalConnectorRegistration",
	"PhysicalConnectorExecutionResult",
	"PhysicalConnectorSDK",
	"PhysicalConnectorSdkError",
	"PhysicalDeviceRecord",
	"PhysicalDeviceRegistry",
	"PhysicalDeviceRegistryError",
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
	"DryRunClassification",
	"DryRunPreview",
	"DryRunExecutionOutcome",
	"DryRunExecutionGate",
	"DryRunGateError",
	"RollbackAction",
	"RollbackRoutinePlan",
	"RollbackRoutineResult",
	"RollbackActionManager",
	"RollbackActionError",
	"HostOperationRequest",
	"HostOperationResult",
	"ParallelExecutionResult",
	"ParallelHostOrchestrator",
	"ParallelOrchestratorError",
	"HostReportEntry",
	"AggregatedControlPlaneReport",
	"ControlPlaneResultReporter",
	"ResultReporterError",
	"RetryPolicy",
	"HostHealthResult",
	"ConnectorHealthSummary",
	"ConnectorHealthMonitor",
	"ConnectorHealthError",
]
