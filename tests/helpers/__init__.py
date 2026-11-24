"""Shared V2 test doubles and helpers (strictly isolated from production src/opendv)."""

from __future__ import annotations

from tests.helpers.archive_builder import ArchiveFixtureBuilder
from tests.helpers.fake_external_mcp import LoopbackFakeExternalMcpServer
from tests.helpers.mock_harness import (
    DeterministicMockHarness,
    InMemoryControlSignalReader,
    InMemoryRunCheckpointStore,
    InMemoryRunEventSink,
    InMemoryScoutToolDispatcher,
    MockFaultSpec,
    MockTurnScript,
    RecordsSourceAdapter,
    ScriptSelector,
    default_for_annotation,
    synthesize_minimal_schema_instance,
    validate_candidate_with_schema,
)
from tests.helpers.secret_backends import (
    FailingKeyringBackend,
    InMemoryKeyringBackend,
    OwnerAclStateHelper,
)

__all__ = [
    "ArchiveFixtureBuilder",
    "DeterministicMockHarness",
    "FailingKeyringBackend",
    "InMemoryControlSignalReader",
    "InMemoryKeyringBackend",
    "InMemoryRunCheckpointStore",
    "InMemoryRunEventSink",
    "InMemoryScoutToolDispatcher",
    "LoopbackFakeExternalMcpServer",
    "MockFaultSpec",
    "MockTurnScript",
    "OwnerAclStateHelper",
    "RecordsSourceAdapter",
    "ScriptSelector",
    "default_for_annotation",
    "synthesize_minimal_schema_instance",
    "validate_candidate_with_schema",
]
