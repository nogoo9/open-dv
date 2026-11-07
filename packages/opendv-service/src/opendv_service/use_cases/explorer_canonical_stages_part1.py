"""Stages 00 through 03 builders for Explorer canonical pipeline.

Conforms to docs/design/open-dv-workbench/HANDOFF.md, Decision D3, and Directive R9.
"""

from __future__ import annotations

from typing import Literal

from opendv_contracts.dtos.explorer import (
    StageDTO,
    StatusTag,
    StepDTO,
)


def _step(
    name: str,
    desc: str,
    reads: list[str],
    writes: list[str],
    kind: Literal["action", "decision", "loop"] | None = None,
    branch: str | None = None,
) -> StepDTO:
    return StepDTO(name=name, kind=kind, branch=branch, reads=reads, writes=writes, desc=desc)


def _stage(
    key: str,
    phase: Literal[0, 1, 2, 3],
    name: str,
    persona: str,
    tier: str,
    tag: StatusTag,
    blurb: str,
    desc: str,
    ins: list[str],
    outs: list[str],
    steps: list[StepDTO],
    comps: list[tuple[str, str, str, str]],
    note: str | None = None,
) -> StageDTO:
    return StageDTO(
        key=key,
        phase=phase,
        name=name,
        persona=persona,
        tier=tier,
        tag=tag,
        blurb=blurb,
        desc=desc,
        note=note,
        ins=ins,
        outs=outs,
        steps=steps,
        comps=comps,
    )


def build_stages_00_to_03() -> list[StageDTO]:
    """Construct canonical stages STAGE_00 through STAGE_03."""
    return [
        _stage(
            "STAGE_00_ORCHESTRATION",
            0,
            "Orchestration & Grounding",
            "@vault-first-mate",
            "pro · T=0.1",
            "PASS",
            "Validates inputs, grounds source tables onto Graph A concepts, and schedules waves.",
            "Checks input files and paths, maps source tables to canonical business concepts.",
            ["harness", "runopts", "spec", "ontology"],
            ["grounding"],
            [
                _step(
                    "Pre-flight input validation",
                    "Checks YAML syntax and sources.",
                    ["spec", "harness", "runopts"],
                    [],
                    "decision",
                    "invalid path or schema → abort run",
                ),
                _step(
                    "Load Graph A ontology",
                    "Parses canonical concepts, key tiers and relationships.",
                    ["ontology"],
                    [],
                ),
                _step(
                    "Ground source tables to concepts",
                    "Maps source tables across waves to Graph A.",
                    ["spec", "ontology"],
                    ["grounding"],
                ),
                _step(
                    "Plan wave execution schedule",
                    "Orders wave execution schedule.",
                    ["spec", "grounding"],
                    ["grounding"],
                ),
            ],
            [
                (
                    "vault-first-mate",
                    "Agent",
                    "Orchestrator. Owns wave scheduling and holds.",
                    "packages/opendv-agent-harness/src/opendv_agent_harness/personas.py",
                ),
                (
                    "validate_pipeline_inputs",
                    "Verifier",
                    "Deterministic pre-flight validator.",
                    "packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py",
                ),
                (
                    "ContractLoader",
                    "Parser",
                    "Strict YAML/JSON schema loader.",
                    "packages/opendv-core/src/opendv_core/contract_loader.py",
                ),
            ],
        ),
        _stage(
            "STAGE_01_SCOUT_SOURCE",
            0,
            "Relational Profiling & Drift",
            "@scout-source",
            "flash · T=0.0",
            "AUTO-FIX",
            "Profiles columns across sources, quarantines row_seq_id, and classifies drift.",
            (
                "Computes uniqueness and null ratios, flags surrogate sequences, "
                "and runs Algorithm A.9a."
            ),
            ["crm", "erp", "stripe", "crm2"],
            ["profile", "drift"],
            [
                _step(
                    "Scan headers & infer SQL types",
                    "Reads CSV headers and sample rows.",
                    ["crm", "erp", "stripe", "crm2"],
                    [],
                ),
                _step(
                    "Compute uniqueness U(C), null ratio & entropy",
                    "Information-theoretic column metrics.",
                    ["crm", "erp", "stripe"],
                    ["profile"],
                ),
                _step(
                    "Quarantine monotonic surrogate keys",
                    "Flags row_seq_id to block Hub key use.",
                    ["profile"],
                    ["profile"],
                    "decision",
                    "Δ=1 sequence → DROPPED_SURROGATE",
                ),
                _step(
                    "Flag sparse foreign keys (Algorithm A.4)",
                    "Flags optional FKs for isolating Link.",
                    ["erp", "profile"],
                    ["profile"],
                    "decision",
                    "U_nn < 0.95 → OPTIONAL_FK_REF",
                ),
                _step(
                    "Classify intra-source drift (Algorithm A.9a)",
                    "Compares source versions for drift.",
                    ["crm", "crm2"],
                    ["drift"],
                ),
            ],
            [
                (
                    "scout-source",
                    "Agent",
                    "Read-only relational profiler.",
                    "packages/opendv-agent-harness/src/opendv_agent_harness/personas.py",
                ),
                (
                    "GraphBProfiler",
                    "Algorithm",
                    "Computes U(C) and inclusion dependencies.",
                    "packages/opendv-core/src/opendv_core/profiler.py",
                ),
                (
                    "SchemaDriftClassifier",
                    "Algorithm",
                    "Algorithm A.9a drift classifier.",
                    "packages/opendv-core/src/opendv_core/schema_drift.py",
                ),
            ],
            note="Wave 2 drift: intra-source schema evolution",
        ),
        _stage(
            "STAGE_02_SCOUT_UNSTRUCTURED",
            0,
            "Unstructured Document Extraction",
            "@scout-unstructured",
            "flash · T=0.0",
            "ASK-USER → resolved",
            "Extracts verbatim spans from unstructured text with 3-tier entity resolution.",
            "Runs Algorithm A.9b over unstructured docs and routes ambiguity to steward hold.",
            ["msa", "ontology", "grounding"],
            ["extract"],
            [
                _step(
                    "Read unstructured document text",
                    "Loads document as immutable UTF-8 buffer.",
                    ["msa"],
                    [],
                ),
                _step(
                    "Extract candidate entity spans",
                    "Proposes verbatim quotes for counterparties.",
                    ["msa", "ontology"],
                    ["extract"],
                ),
                _step(
                    "Verify exact substring offsets (Algorithm A.9b)",
                    "Checks offsets against source buffer.",
                    ["msa", "extract"],
                    ["extract"],
                    "decision",
                    "doc[start:end] != quote → reject span",
                ),
                _step(
                    "Score 3-tier entity resolution (U1 / U2 / U3)",
                    "Scores confidence across ER tiers.",
                    ["extract", "grounding"],
                    ["extract"],
                ),
                _step(
                    "Route Tier-U3 ambiguity to steward hold",
                    "Raises STEWARD_HOLD per configured policy.",
                    ["runopts", "extract"],
                    ["extract"],
                    "decision",
                    "policy: auto_approve_top_candidate",
                ),
            ],
            [
                (
                    "scout-unstructured",
                    "Agent",
                    "Unstructured span extractor.",
                    "packages/opendv-agent-harness/src/opendv_agent_harness/personas.py",
                ),
                (
                    "SubstringSpanVerifier",
                    "Algorithm",
                    "Algorithm A.9b verifier & ER scorer.",
                    "packages/opendv-core/src/opendv_core/unstructured_er.py",
                ),
            ],
        ),
        _stage(
            "STAGE_03_SHIP_VAULT_PASS1",
            1,
            "Pass 1 · Column Triage",
            "@ship-vault",
            "pro · T=0.1",
            "PASS",
            "Assigns source columns to explicit Data Vault roles (100% column coverage).",
            "Produces exhaustive column triage ledger so no source column is silently dropped.",
            ["ontology", "grounding", "profile", "drift", "extract"],
            ["triage"],
            [
                _step(
                    "Enumerate all columns across sources",
                    "Builds column universe for Gate 1.",
                    ["profile", "drift", "extract"],
                    [],
                ),
                _step(
                    "Classify each column into roles",
                    "Assigns BUSINESS_KEY, FK_REF, PAYLOAD, etc.",
                    ["ontology", "profile"],
                    ["triage"],
                ),
                _step(
                    "Tombstone surrogate keys",
                    "Records explicit tombstone for surrogates.",
                    ["profile"],
                    ["triage"],
                ),
            ],
            [
                (
                    "ship-vault",
                    "Agent",
                    "Declarative DV 2.0 synthesiser.",
                    "packages/opendv-agent-harness/src/opendv_agent_harness/personas.py",
                ),
                (
                    "ColumnTriageBijection",
                    "Verifier",
                    "Checks 1-to-1 column accounting.",
                    (
                        "packages/opendv-axi-verifier/src/opendv_axi_verifier/"
                        "rules/gate1_schema_grain.py"
                    ),
                ),
            ],
        ),
    ]
