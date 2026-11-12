# 23 — Open-DV Ontology Construction Studio: Developer Extension Guide

**Document Status:** Normative Developer & Extension Guide  
**Milestone:** Phase 6 Wave 3 Integration & Release (Task 6C.2 / Issue #234)  
**Governing Documents:** `docs/19-ontology-studio-architecture.md`, `docs/20-ontology-studio-spec.md`, `docs/21-ontology-studio-implementation-plan.md`  
**Target Audience:** Core Contributors, Enterprise Integrators, Domain Ontologists, Machine Learning Engineers  

---

## 1. Extension Architecture & Philosophy

The Open-DV Ontology Construction Studio is designed around the principle of **Extensibility without Monkeypatching**. All major capabilities—ingesting exotic document layouts, integrating external industry vocabularies, tuning lexical ranking algorithms, configuring specialized agent personas, and serializing to custom target schemas—are exposed as formal **Service Provider Interfaces (SPIs)** or declarative protocols.

```
+───────────────────────────────────────────────────────────────────────────────────────────────+
|                           Open-DV Studio Extensibility Architecture                           |
+───────────────────────────────────────────────────────────────────────────────────────────────+
|                                                                                               |
|  [ SPI-1: DocumentAdapter ] ───────────────────► [ SPI-2: ReferenceOntologyAdapter ]          |
|  - Raw stream / bytes ingestion                  - Direct JSON pack loader (< 70ms)           |
|  - Character offset & layout mapping             - O(1) URI lookup & candidate search         |
|  - Taint classification clearance                - Zero runtime rdflib imports                |
|                                                                                               |
|                                         │                                                     |
|                                         ▼                                                     |
|  [ SPI-3: ConceptMatcher ] ────────────────────► [ SPI-4: AgentPersona & Harness ]            |
|  - Robertson BM25 parameter tuning               - 3-tier cognitive subagent delegation       |
|  - Multi-field weighting (name, syn, def)        - Domain prompt templates                    |
|  - Domain acronym & synonym expansion            - HMAC-SHA256 PII pseudonymization           |
|                                                                                               |
|                                         │                                                     |
|                                         ▼                                                     |
|  [ SPI-5: Custom Export Serializer ] ──────────► [ TestKit & Conformance Harness ]            |
|  - Neo4j Cypher DDL (escaped identifiers)        - ast.parse() & runtime validation           |
|  - W3C SHACL Validation Shapes                   - Golden reference evaluation                |
|  - GraphQL SDL / Protobuf DTOs                   - Zero credential air-gapped CI              |
+───────────────────────────────────────────────────────────────────────────────────────────────+
```

### Core Design Tenets:
1. **Contract Isolation**: SPI interfaces and `extra="forbid"` Pydantic v2 DTOs reside exclusively in foundational contract packages (`opendv-contracts` and `opendv-sources`), guaranteeing that plugins never pull transitive runtime dependencies like web servers or UI frameworks.
2. **Determinism by Default**: Custom matchers and loaders must guarantee repeatable, bit-for-bit identical outputs and NFC/LF-normalized Unicode character offsets across Windows, Linux, and macOS.
3. **Cryptographic Accountability & Fail-Closed Security**: Every parsed evidence chunk and reference concept carries SHA-256 source digests, while custom exporters treat all ingested document text and LLM-synthesized identifiers as untrusted input.

---

## 2. Extension Point 1: Custom Document Adapters (`DocumentAdapter` SPI)

### 2.1 Interface Contract & Lifecycle
When integrating new document formats (e.g. XML Schema `.xsd`, HL7/FHIR XML, DICOM metadata, or proprietary CAD schemas), subclass the abstract `DocumentAdapter` SPI defined in `opendv_sources.ingestion.adapter`.

All returned `EvidenceChunk`, `PageMapEntry`, and `IngestionResult` objects inherit `BaseContractModel` (`extra="forbid"`):
- Normalize text with `normalize_text()` (NFC + LF line endings) so offsets never drift across OS platforms.
- Whenever `start_char` and `end_char` are supplied on `EvidenceChunk`, `end_char - start_char` must equal `len(text)`.
- Whenever `text` is populated on `PageMapEntry`, `end_offset - start_offset` must equal `len(text)`.

```python
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from typing import Any

from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    IngestionResult,
    PageMapEntry,
    TaintClassification,
)
from opendv_sources.ingestion.adapter import DocumentAdapter
from opendv_sources.ingestion.normalization import normalize_text


class XmlSchemaDocumentAdapter(DocumentAdapter):
    """Custom DocumentAdapter parsing W3C XML Schema (.xsd) files into conceptual evidence."""

    @property
    def adapter_name(self) -> str:
        """Unique identifier for this document adapter."""
        return "XmlSchemaDocumentAdapter"

    def supports_mime_type(self, mime_type: str) -> bool:
        """Check if this adapter supports the given MIME media type."""
        normalized = mime_type.lower().split(";")[0].strip()
        return normalized in {"application/xml", "text/xml", "application/xsd+xml"}

    def supports_extension(self, extension: str) -> bool:
        """Check if this adapter supports the given file extension."""
        ext = extension.lower().strip()
        return ext in {".xsd", "xsd", ".xml", "xml"}

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw document bytes into an IngestionResult with exact span offsets."""
        raw_text = data.decode("utf-8", errors="replace")
        sha256 = hashlib.sha256(data).hexdigest()
        taint = TaintClassification(kwargs.get("taint", TaintClassification.INTERNAL))

        segments: list[str] = []
        try:
            root = ET.fromstring(raw_text)
            for elem in root.iter():
                tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                name_attr = elem.attrib.get("name")
                if tag_name in {"complexType", "element", "simpleType"} and name_attr:
                    doc_elem = elem.find(".//{*}documentation")
                    doc_text = (
                        doc_elem.text.strip()
                        if doc_elem is not None and doc_elem.text
                        else f"XSD declaration of {name_attr}"
                    )
                    segments.append(normalize_text(f"{name_attr}: {doc_text}"))
        except ET.ParseError:
            fallback = normalize_text(raw_text[:200]).strip() or "Empty XSD"
            segments.append(fallback)

        if not segments:
            segments.append(normalize_text(raw_text[:200]).strip() or "Empty XSD")

        normalized_doc = "\n".join(segments)
        chunks: list[EvidenceChunk] = []
        cursor = 0
        for seg in segments:
            start_pos = cursor
            end_pos = start_pos + len(seg)
            chunk_hash = hashlib.sha256(f"{sha256}:{start_pos}:{seg}".encode("utf-8")).hexdigest()[:12]
            chunks.append(
                EvidenceChunk(
                    chunk_id=f"chunk-{chunk_hash}",
                    document_id=sha256,
                    text=seg,
                    page_number=1,
                    start_char=start_pos,
                    end_char=end_pos,
                    confidence=0.95,
                    taint=taint,
                    source_sha256=sha256,
                )
            )
            cursor = end_pos + 1  # account for joining '\n'

        return IngestionResult(
            document_id=sha256,
            filename=filename,
            mime_type=mime_type or "application/xml",
            total_pages=1,
            total_characters=len(normalized_doc),
            chunks=chunks,
            page_map=[
                PageMapEntry(
                    page_number=1,
                    text=normalized_doc,
                    start_offset=0,
                    end_offset=len(normalized_doc),
                )
            ],
            taint=taint,
            metadata={"adapter": self.adapter_name},
            source_sha256=sha256,
            normalized_text=normalized_doc,
        )
```

### 2.2 Registering Custom Document Adapters in `IngestionService`
Register your `DocumentAdapter` directly with `IngestionService` (`opendv_sources.ingestion.service`) using `priority=True` so it takes precedence over generic text/markup fallbacks. (When exposing a brand-new file extension through the REST upload endpoint, also add the extension to `SUPPORTED_EXTENSIONS` in `opendv_platform_api/routers/ontology_studio.py`.)

```python
from opendv_sources.adapters import get_default_document_adapters
from opendv_sources.ingestion.service import IngestionService

# Build an IngestionService with default adapters and register XmlSchemaDocumentAdapter at highest priority
ingestion_service = IngestionService(adapters=get_default_document_adapters())
ingestion_service.register_adapter(XmlSchemaDocumentAdapter(), priority=True)
```

---

## 3. Extension Point 2: Custom Reference Ontologies (`ReferenceOntologyAdapter` SPI)

### 3.1 Interface Contract & `ReferenceConcept` DTO Alignment
To align discovered business terms with industry-specific reference ontologies (such as Healthcare SNOMED/FHIR, ISO 20022, or BIAN), implement the `@runtime_checkable` `ReferenceOntologyAdapter` protocol defined in `opendv_contracts.spi.reference_ontology` (`lookup_by_uri` and `search_candidates`) and materialize typed `ReferenceConcept` DTOs (`opendv_contracts.dtos.matching`, `extra="forbid"`) for BM25 indexing:

```python
from __future__ import annotations

from typing import Any

from opendv_contracts.dtos.matching import ReferenceConcept
from opendv_contracts.spi.reference_ontology import ReferenceOntologyAdapter


class HealthcareReferenceOntologyLoader(ReferenceOntologyAdapter):
    """Custom ReferenceOntologyAdapter loading curated SNOMED CT and FHIR terminology."""

    def __init__(self, concepts: list[ReferenceConcept] | None = None) -> None:
        default_concepts = concepts or [
            ReferenceConcept(
                concept_uri="http://snomed.info/id/116154003",
                concept_name="Patient",
                module="SNOMED-CORE",
                definition="Person receiving or registered to receive healthcare services.",
                synonyms=["CareRecipient", "HealthcareSubject"],
                parent_concept_uris=[],
            ),
            ReferenceConcept(
                concept_uri="http://hl7.org/fhir/StructureDefinition/Observation",
                concept_name="Clinical Observation",
                module="FHIR-R4",
                definition="Measurements and simple assertions made about a patient.",
                synonyms=["VitalSignObservation"],
                parent_concept_uris=[],
            ),
        ]
        self._concepts: dict[str, ReferenceConcept] = {
            c.concept_uri: c for c in default_concepts
        }

    def to_reference_concepts(self) -> list[ReferenceConcept]:
        """Return typed ReferenceConcept models ready for ConceptMatcher.index_concepts()."""
        return list(self._concepts.values())

    def lookup_by_uri(self, uri: str) -> dict[str, Any] | None:
        """O(1) dictionary lookup by exact standard URI."""
        concept = self._concepts.get(uri)
        return concept.model_dump(mode="json") if concept is not None else None

    def search_candidates(self, term: str, limit: int = 10) -> list[dict[str, Any]]:
        """Fast deterministic substring/synonym candidate lookup."""
        query = term.lower().strip()
        matches: list[dict[str, Any]] = []
        for concept in self._concepts.values():
            haystack = " ".join(
                [concept.concept_name, concept.definition, *concept.synonyms]
            ).lower()
            if query in haystack:
                matches.append(concept.model_dump(mode="json"))
            if len(matches) >= limit:
                break
        return matches
```

### 3.2 Compiling High-Speed JSON Reference Packs
Following ADR-003 and ADR-005, reference ontologies are pre-compiled offline into canonical JSON packs (`<pack_id>-<sha256_8>.json`) and anchored in `schemas/v1/baseline-digests.json` to guarantee cold-load times under $70$ ms without runtime `rdflib` parsing overhead:

```json
{
  "pack_id": "hl7-fhir-r4-core",
  "pack_version": "1.0.0",
  "ontology_iri": "http://hl7.org/fhir/ontology",
  "concepts": [
    {
      "concept_uri": "http://hl7.org/fhir/StructureDefinition/Patient",
      "concept_name": "Patient",
      "module": "FHIR-ADMIN",
      "definition": "Demographics and other administrative information about an individual.",
      "synonyms": ["Subject", "CareRecipient"],
      "parent_concept_uris": []
    }
  ]
}
```

---

## 4. Extension Point 3: Custom BM25 Tokenization & Matchers (`ConceptMatcher` SPI)

### 4.1 Interface Contract & Robertson BM25 Formulation
The Studio employs Robertson/Okapi BM25 with multi-field weighting (`concept_name + synonyms`, never scoring opaque URI slugs) to align candidate terms against reference packs. Subclass `ConceptMatcher` (`opendv_service.ontology.matching.bm25`) to inject domain-specific stop words, acronym expansions, or custom tokenization while preserving deterministic `(score DESC, concept_uri ASC)` tie-breaking:

```python
from __future__ import annotations

from decimal import Decimal

from opendv_contracts.dtos.matching import ReferenceConcept, ScoredConceptMatch
from opendv_service.ontology.matching.bm25 import ConceptMatcher


class DomainAwareConceptMatcher(ConceptMatcher):
    """Custom ConceptMatcher providing domain-tuned lexical scoring with acronym expansion."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1: Decimal = Decimal(str(k1))
        self.b: Decimal = Decimal(str(b))
        self._concepts: dict[str, ReferenceConcept] = {}
        self._synonym_map: dict[str, str] = {
            "lp": "limited partner",
            "gp": "general partner",
            "nav": "net asset value",
        }

    def index_concepts(self, concepts: list[ReferenceConcept]) -> None:
        """Index concepts into memory in deterministic URI order."""
        self._concepts = {
            c.concept_uri: c for c in sorted(concepts, key=lambda item: item.concept_uri)
        }

    def lookup_concept(self, uri: str) -> ReferenceConcept | None:
        """Retrieve indexed concept by URI."""
        return self._concepts.get(uri)

    def match(
        self,
        query: str,
        top_k: int = 5,
        module_filter: list[str] | None = None,
        min_score: float = 0.0,
    ) -> list[ScoredConceptMatch]:
        """Rank and return top-k scored concept matches with deterministic URI tie-breaking."""
        q_clean = self._expand_acronyms(query.lower().strip())
        results: list[ScoredConceptMatch] = []
        for c in self._concepts.values():
            if module_filter and c.module not in module_filter:
                continue
            scored = self.score(q_clean, c.concept_uri)
            if scored and scored.normalized_score >= min_score:
                results.append(scored)

        results.sort(key=lambda x: (-x.normalized_score, x.concept_uri))
        return results[:top_k]

    def score(self, query: str, concept_uri: str) -> ScoredConceptMatch | None:
        """Compute normalized match score over human-readable concept_name and synonyms."""
        concept = self._concepts.get(concept_uri)
        if not concept:
            return None

        q_terms = set(self._expand_acronyms(query.lower()).split())
        if not q_terms:
            return None
        lexical_fields = [concept.concept_name, *concept.synonyms]
        c_terms = set(" ".join(lexical_fields).lower().split())
        overlap = len(q_terms.intersection(c_terms))
        total = max(1, len(q_terms.union(c_terms)))
        jaccard = overlap / total

        norm_score = min(1.0, max(0.0, round(jaccard, 4)))
        return ScoredConceptMatch(
            concept_uri=concept.concept_uri,
            concept_name=concept.concept_name,
            module=concept.module,
            raw_score=float(overlap * 2.5),
            normalized_score=norm_score,
            matched_term=query,
            definition=concept.definition,
        )

    def _expand_acronyms(self, text: str) -> str:
        words = text.split()
        return " ".join(self._synonym_map.get(w, w) for w in words)
```

---

## 5. Extension Point 4: Specialized Task Builders & Agent Harness (`opendv-agent-harness`)

### 5.1 Task Specification Builder (`AgentTaskSpec`)
The Studio's neuro-symbolic harness (`opendv_agent_harness.models`) dispatches work across closed cognitive personas (`AgentPersona.TERM_SCOUT`, `AgentPersona.DOMAIN_CLARIFIER`, `AgentPersona.ONTOLOGY_ARCHITECT`) via typed `AgentTaskSpec` envelopes. Integrators can author domain-specialized task builders (e.g. regulatory compliance scouting for GDPR/HIPAA/SEC) that attach tailored system instructions, stages, and model tiers without mutating the core persona enum:

```python
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    ModelTier,
    PipelineStageId,
)


def build_compliance_scout_task_spec(
    focus_domains: list[str] | None = None,
) -> AgentTaskSpec[Any]:
    """Build task specification for a domain-specialized regulatory compliance term scout."""
    domains = focus_domains or ["SEC", "FINRA"]
    task_id = f"task-compliance-{uuid.uuid4().hex[:8]}"
    trace_id = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:32]

    return AgentTaskSpec(
        task_id=task_id,
        run_id=f"run-{task_id}",
        trace_id=trace_id,
        persona=AgentPersona.TERM_SCOUT,
        stage_id=PipelineStageId.STAGE_01_SCOUT_SOURCE,
        prompt=f"Extract and classify compliance terms for regulatory domains: {', '.join(domains)}.",
        system_instructions=(
            "You are a specialized Compliance Term Scout. Extract regulatory mandates, "
            "authorities, filing rules, and compliance metrics from pre-redacted evidence chunks. "
            "Never emit raw SQL DDL or unverified external URIs."
        ),
        model_tier=ModelTier.FAST_TIER,
    )
```

### 5.2 Keyed HMAC-SHA256 Pseudonymization & `snake_case` Boundaries
Before any chunk reaches an external LLM provider, documents marked `do_not_send_to_agent=True` are blocked by sticky taint tracking (`taint_index.json`), and sensitive entity tokens are pseudonymized using workspace-derived $K_{\text{pseudo}}$ (HKDF-SHA256):

$$\text{Pseudonym} = \text{HMAC-SHA256}(K_{\text{pseudo}}, \text{EntityName})[:16]$$

When writing custom redaction or pre-processing filters, always match tokens with alphanumeric lookarounds `(?<![A-Za-z0-9])...(?![A-Za-z0-9])` rather than regex `\b`, because `_` is a word character in `\w` and `\b` fails to match confidential terms embedded inside `snake_case` SQL/Data Vault table and column identifiers.

---

## 6. Extension Point 5: Custom Export Serializers (`opendv_service.export`)

### 6.1 Serializer Architecture & Untrusted Input Escaping
Built-in ontology serialization lives in `packages/opendv-service/src/opendv_service/export/rdf_exporter.py` (`SUPPORTED_EXPORT_FORMATS`: `turtle`/`ttl`, `jsonld`/`json-ld`, `ntriples`/`nt`, `yaml`/`yml`, and canonical RFC 8785 JCS `json`/`jcs`/`rfc8785`). If you expose a new format on the CLI/REST surface, register it in both `SUPPORTED_EXPORT_FORMATS` and `schemas/v1/cli-spec.json` (`ontology studio export --export-format`).

> [!IMPORTANT]
> **Security Invariant — Treat Ontology Text & Identifiers as Untrusted Input:**  
> In Ontology Studio, `description`, `definition`, `grain_semantics`, `concept_name`, `attribute_name`, and `relationship_name` originate from LLM-synthesized proposals and ingested third-party documents (PDFs, contracts, schemas). Interpolating them raw into a target query language allows **comment-breakout statement injection** (e.g., an embedded `\n` in a `//` comment followed by `MATCH (n) DETACH DELETE n;`) or **identifier label injection** (via `` ` ``, `:`, `)`, or `{`). Every custom exporter MUST:
> 1. Validate or canonicalize identifiers against a strict allowlist (`^[A-Za-z_][A-Za-z0-9_]*$`) and backtick-quote every emitted Cypher label, property, and relationship type with embedded backticks doubled.
> 2. Strip/replace CR (`\r`), LF (`\n`), and comment-closing tokens (`*/`) from all text emitted inside single-line or block comments.

```python
from __future__ import annotations

import re
from typing import Any

from opendv_core.models.graph_a import GraphAOntology

_SAFE_CYPHER_IDENT_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_cypher_identifier(raw_ident: str, *, field_name: str = "identifier") -> str:
    """Validate an identifier against strict ASCII identifier rules and backtick-quote it."""
    candidate = raw_ident.strip()
    if candidate.startswith("urn:ontology:"):
        candidate = candidate.rsplit(":", 1)[-1]
    if not _SAFE_CYPHER_IDENT_RE.fullmatch(candidate):
        raise ValueError(
            f"Unsafe Cypher {field_name} rejected: {raw_ident!r} "
            "(must match ^[A-Za-z_][A-Za-z0-9_]*$)"
        )
    escaped = candidate.replace("`", "``")
    return f"`{escaped}`"


def _sanitize_cypher_comment(raw_text: str | None, default: str = "") -> str:
    """Collapse CR/LF newlines and neutralize block-comment terminators in '//' comments."""
    text = raw_text if raw_text is not None and raw_text != "" else default
    collapsed = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return collapsed.replace("*/", "* /").strip()


class CypherDDLExporter:
    """Custom export serializer translating GraphAOntology into injection-safe Neo4j Cypher DDL."""

    def export_cypher(self, ontology: GraphAOntology | dict[str, Any]) -> str:
        """Convert a GraphAOntology into executable, escaped Cypher DDL scripts."""
        ont = GraphAOntology.model_validate(ontology) if isinstance(ontology, dict) else ontology
        safe_ns = _sanitize_cypher_comment(ont.domain_namespace, default="general")
        safe_ver = _sanitize_cypher_comment(ont.ontology_version, default="0.0.0")
        safe_desc = _sanitize_cypher_comment(ont.description, default="No description provided")

        lines: list[str] = [
            f"// Neo4j Cypher Schema for Open-DV Ontology: {safe_ns} v{safe_ver}",
            f"// Description: {safe_desc}",
            "",
        ]

        # Generate Node Constraints for Canonical Business Concepts (Hubs)
        for concept in ont.canonical_business_concepts:
            raw_label = concept.target_hub or concept.concept_name.replace(" ", "_")
            label = _quote_cypher_identifier(raw_label, field_name="node label")
            lines.append(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.`business_key` IS UNIQUE;"
            )
            if concept.attributes:
                for attr in concept.attributes:
                    prop_name = _quote_cypher_identifier(
                        attr.attribute_name, field_name="attribute_name"
                    )
                    dtype = _sanitize_cypher_comment(getattr(attr, "data_type", "string"), "string")
                    lines.append(f"// Attribute: (:{label}) -> {prop_name} ({dtype})")

        lines.append("")
        # Generate Relationship Documentation & Validated Endpoint Types for Links
        for rel in ont.canonical_relationships:
            raw_rel_type = (rel.target_link or rel.relationship_name).upper().replace(" ", "_")
            rel_type = _quote_cypher_identifier(raw_rel_type, field_name="relationship type")
            source_raw = rel.endpoints[0] if getattr(rel, "endpoints", None) else "ConceptSource"
            target_raw = (
                rel.endpoints[1]
                if getattr(rel, "endpoints", None) and len(rel.endpoints) > 1
                else "ConceptTarget"
            )
            source_label = _quote_cypher_identifier(source_raw, field_name="source endpoint")
            target_label = _quote_cypher_identifier(target_raw, field_name="target endpoint")
            grain = _sanitize_cypher_comment(rel.grain_semantics, default="1:N")
            lines.append(
                f"// Relationship: (:{source_label})-[:{rel_type}]->(:{target_label}) [Grain: {grain}]"
            )

        return "\n".join(lines) + "\n"
```

---

## 7. Extension Testing & Conformance Verification

To guarantee that your custom extensions maintain system stability, pass `extra="forbid"` contract validation, and resist injection payloads, run the documentation conformance suite in CI:

```bash
# Run documentation snippet & injection guard validation
uv run pytest tests/docs/test_doc_code_snippets.py -vv

# Run ruff code quality checks
uv run ruff check tests/docs/
```

### Best Practices Checklist:
- [x] All custom `DocumentAdapter` classes normalize text to NFC/LF, preserve exact character offsets (`end - start == len(text)`), and register via `IngestionService.register_adapter(..., priority=True)`.
- [x] Reference ontology loaders implement `ReferenceOntologyAdapter` and emit validated `ReferenceConcept` DTOs from pre-compiled JSON packs in $< 70$ ms without runtime RDF parsing.
- [x] Custom concept matchers score human-readable lexical fields (`concept_name + synonyms`) and break ties deterministically by `concept_uri`.
- [x] Custom task specs and filters enforce sticky confidential tainting and `(?<![A-Za-z0-9])` `snake_case`-safe HMAC-SHA256 pseudonymization.
- [x] Custom export serializers strictly validate/quote identifiers and strip CR/LF/`*/` from untrusted ontology text.
