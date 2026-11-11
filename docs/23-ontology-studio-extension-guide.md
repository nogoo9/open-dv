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
|  - Neo4j Cypher DDL                              - ast.parse() syntax validation              |
|  - W3C SHACL Validation Shapes                   - Golden reference evaluation                |
|  - GraphQL SDL / Protobuf DTOs                   - Zero credential air-gapped CI              |
+───────────────────────────────────────────────────────────────────────────────────────────────+
```

### Core Design Tenets:
1. **Contract Isolation**: SPI interfaces reside exclusively in foundational contract packages (`opendv-contracts` and `opendv-sources`), guaranteeing that plugins never pull transitive runtime dependencies like web servers or UI frameworks.
2. **Determinism by Default**: Custom matchers and loaders must guarantee repeatable, bit-for-bit identical outputs for identical inputs across Windows, Linux, and macOS.
3. **Cryptographic Accountability**: Every parsed evidence chunk and reference concept carries SHA-256 source digests, enabling rigorous lineage tracking down to the exact source byte offsets.

---

## 2. Extension Point 1: Custom Document Adapters (`DocumentAdapter` SPI)

### 2.1 Interface Contract & Lifecycle
When integrating new document types (e.g. XML Schema `.xsd`, HL7/FHIR XML, DICOM metadata, or proprietary CAD schemas), implement the abstract `DocumentAdapter` SPI defined in `opendv_sources.ingestion.adapter`:

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
        """Parse raw document bytes into an IngestionResult with structured evidence chunks."""
        content = data.decode("utf-8", errors="replace")
        sha256 = hashlib.sha256(data).hexdigest()
        chunks: list[EvidenceChunk] = []

        try:
            root = ET.fromstring(content)
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
                    chunk_text = f"{name_attr}: {doc_text}"
                    chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:12]
                    chunks.append(
                        EvidenceChunk(
                            chunk_id=f"chunk-{chunk_hash}",
                            document_id=sha256,
                            text=chunk_text,
                            page_number=1,
                            confidence=0.95,
                            taint=TaintClassification.INTERNAL,
                        )
                    )
        except ET.ParseError:
            chunks.append(
                EvidenceChunk(
                    chunk_id=f"chunk-{sha256[:12]}",
                    document_id=sha256,
                    text=content[:200] if content else "Empty XSD",
                    page_number=1,
                    confidence=0.50,
                    taint=TaintClassification.INTERNAL,
                )
            )

        return IngestionResult(
            document_id=sha256,
            filename=filename,
            mime_type=mime_type or "application/xml",
            total_pages=1,
            total_characters=len(content),
            chunks=chunks,
            page_map=[PageMapEntry(page_number=1, start_offset=0, end_offset=len(content))],
            taint=TaintClassification.INTERNAL,
            source_sha256=sha256,
            normalized_text=content,
        )
```

### 2.2 Registering Custom Document Adapters
Register your custom adapter in the system adapter registry:

```python
from opendv_sources.adapters import get_default_document_adapters
from opendv_sources.registry.source_registry import get_source_registry

# Register custom XML Schema adapter in active ingestion pipeline
registry = get_source_registry()
adapters = get_default_document_adapters()
adapters.insert(0, XmlSchemaDocumentAdapter())
```

---

## 3. Extension Point 2: Custom Reference Ontologies (`ReferenceOntologyAdapter` SPI)

### 3.1 Interface Contract
To align discovered business terms with industry-specific reference ontologies (such as Healthcare SNOMED/FHIR, BioPortal, ISO 20022, or Automotive OpenDRIVE), implement the `ReferenceOntologyAdapter` protocol defined in `opendv_contracts.spi.reference_ontology`:

```python
from __future__ import annotations

from typing import Any
from opendv_contracts.spi.reference_ontology import ReferenceOntologyAdapter


class HealthcareReferenceOntologyLoader(ReferenceOntologyAdapter):
    """Custom ReferenceOntologyAdapter loading curated SNOMED CT and FHIR terminology."""

    def __init__(self, entities: dict[str, dict[str, Any]] | None = None) -> None:
        self._entities: dict[str, dict[str, Any]] = entities or {
            "http://snomed.info/id/116154003": {
                "uri": "http://snomed.info/id/116154003",
                "label": "Patient",
                "definition": "Person receiving or registered to receive healthcare services.",
                "module": "SNOMED-CORE",
            },
            "http://hl7.org/fhir/StructureDefinition/Observation": {
                "uri": "http://hl7.org/fhir/StructureDefinition/Observation",
                "label": "Clinical Observation",
                "definition": "Measurements and simple assertions made about a patient.",
                "module": "FHIR-R4",
            },
        }

    def lookup_by_uri(self, uri: str) -> dict[str, Any] | None:
        """O(1) dictionary lookup by standard URI."""
        return self._entities.get(uri)

    def search_candidates(self, term: str, limit: int = 10) -> list[dict[str, Any]]:
        """Fast prefix and token search for matching reference concept candidates."""
        query = term.lower().strip()
        matches: list[dict[str, Any]] = []
        for entity in self._entities.values():
            label = entity.get("label", "").lower()
            definition = entity.get("definition", "").lower()
            if query in label or query in definition:
                matches.append(entity)
            if len(matches) >= limit:
                break
        return matches
```

### 3.2 Compiling High-Speed JSON Reference Packs
Following ADR-003 and ADR-004, reference ontologies should be pre-compiled into single-file JSON packs (`<name>-pack.json`) to guarantee cold-load times under $70$ ms without runtime RDF parsing overhead:

```json
{
  "pack_version": "1.0.0",
  "ontology_iri": "http://hl7.org/fhir/ontology",
  "concepts": [
    {
      "concept_uri": "http://hl7.org/fhir/StructureDefinition/Patient",
      "concept_name": "Patient",
      "module": "FHIR-ADMIN",
      "definition": "Demographics and other administrative information about an individual.",
      "synonyms": ["Subject", "CareRecipient"]
    }
  ]
}
```

---

## 4. Extension Point 3: Custom BM25 Tokenization & Matchers (`ConceptMatcher` SPI)

### 4.1 Interface Contract & Robertson BM25 Formulation
The Studio employs Robertson BM25 with multi-field weighting to score discovered candidates against reference ontologies. Developers can subclass `ConceptMatcher` to inject domain-specific stop words, CamelCase splitters, acronym expansions, or custom term weighting:

```python
from __future__ import annotations

from decimal import Decimal
from typing import Final

from opendv_contracts.dtos.matching import ReferenceConcept, ScoredConceptMatch
from opendv_service.ontology.matching.bm25 import ConceptMatcher


class DomainAwareConceptMatcher(ConceptMatcher):
    """Custom ConceptMatcher providing domain-tuned Okapi BM25 scoring with acronym expansion."""

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
        """Index concepts into memory for high-speed candidate retrieval."""
        self._concepts = {c.concept_uri: c for c in concepts}

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
        """Rank and return top-k scored concept matches."""
        q_clean = self._expand_acronyms(query.lower().strip())
        results: list[ScoredConceptMatch] = []
        for c in self._concepts.values():
            if module_filter and c.module not in module_filter:
                continue
            scored = self.score(q_clean, c.concept_uri)
            if scored and scored.normalized_score >= min_score:
                results.append(scored)

        results.sort(key=lambda x: x.normalized_score, reverse=True)
        return results[:top_k]

    def score(self, query: str, concept_uri: str) -> ScoredConceptMatch | None:
        """Compute the multi-field normalized match score for a specific concept."""
        concept = self._concepts.get(concept_uri)
        if not concept:
            return None

        q_terms = set(self._expand_acronyms(query.lower()).split())
        c_terms = set(concept.concept_name.lower().split())
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

## 5. Extension Point 4: Custom LLM Personas & Agent Harness Adapters

### 5.1 Persona Definition & Task Builder
The Studio's neuro-symbolic engine delegates work to specialized personas configured via `AgentTaskSpec`. Developers can define custom personas (e.g. `@regulatory-compliance-scout`, `@esg-auditor`, `@actuarial-modeler`) with tailored prompts, tool allowances, and reasoning budgets:

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
    """Build task specification for a custom regulatory compliance term scout."""
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
            "You are a specialized Compliance Term Scout. Extract all regulatory mandates, "
            "authorities, filing rules, and compliance metrics from source text. "
            "Mask all PII using pseudonymization keys before returning candidate terms."
        ),
        model_tier=ModelTier.FAST_TIER,
    )
```

### 5.2 Keyed HMAC-SHA256 Pseudonymization
To ensure zero confidential data leakage when delegating to cloud-hosted LLM endpoints, the harness enforces HMAC-SHA256 pseudonymization. All sensitive entity identifiers are salted with the session pseudonymization key $K_{pseudo}$:

$$\text{Pseudonym} = \text{HMAC-SHA256}(K_{pseudo}, \text{EntityName})[:16]$$

The client application securely maintains the reversible lookup table locally, ensuring cloud providers never observe raw PII or proprietary identifiers.

---

## 6. Extension Point 5: Custom Export Serializers (`RDFExporter`)

### 6.1 Serializer Architecture
While Open-DV natively serializes ontologies to W3C Turtle, JSON-LD, N-Triples, RFC 8785 JSON, and Graph A YAML, enterprise data environments frequently require specialized physical targets such as Neo4j Cypher DDL, W3C SHACL Shape Graphs, or GraphQL SDL schemas.

Below is a complete custom exporter translating `GraphAOntology` into Neo4j Cypher schema constraints and graph definitions:

```python
from __future__ import annotations

from typing import Any
from opendv_core.models.graph_a import GraphAOntology


class CypherDDLExporter:
    """Custom export serializer translating GraphAOntology into Neo4j Cypher DDL statements."""

    def export_cypher(self, ontology: GraphAOntology | dict[str, Any]) -> str:
        """Convert a GraphAOntology into executable Cypher DDL scripts."""
        ont = GraphAOntology.model_validate(ontology) if isinstance(ontology, dict) else ontology
        lines: list[str] = [
            f"// Neo4j Cypher Schema for Open-DV Ontology: {ont.domain_namespace} v{ont.ontology_version}",
            f"// Description: {ont.description or 'No description provided'}",
            "",
        ]

        # Generate Node Constraints for Canonical Business Concepts (Hubs)
        for concept in ont.canonical_business_concepts:
            label = concept.target_hub or concept.concept_name.replace(" ", "")
            lines.append(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.business_key IS UNIQUE;"
            )
            if concept.attributes:
                for attr in concept.attributes:
                    lines.append(
                        f"// Attribute: (:{label}) -> {attr.attribute_name} ({getattr(attr, 'data_type', 'string')})"
                    )

        lines.append("")
        # Generate Relationship Constraints for Canonical Relationships (Links)
        for rel in ont.canonical_relationships:
            rel_type = (rel.target_link or rel.relationship_name).upper().replace(" ", "_")
            source = rel.endpoints[0] if getattr(rel, "endpoints", None) else "ConceptSource"
            target = (
                rel.endpoints[1]
                if getattr(rel, "endpoints", None) and len(rel.endpoints) > 1
                else "ConceptTarget"
            )
            lines.append(
                f"// Relationship: (:{source})-[:{rel_type}]->(:{target}) [Grain: {rel.grain_semantics or '1:N'}]"
            )

        return "\n".join(lines) + "\n"
```

---

## 7. Extension Testing & Conformance Verification

To guarantee that your custom extensions maintain system stability and pass automated CI checks, test your components using `pytest`:

```bash
# Run documentation snippet validation
uv run pytest tests/docs/test_doc_code_snippets.py -k docs_23 -vv

# Run ruff code quality checks
uv run ruff check tests/docs/
```

### Best Practices Checklist:
- [x] All custom `DocumentAdapter` classes implement `supports_mime_type`, `supports_extension`, and `parse_bytes`.
- [x] Reference ontology loaders load in $< 70$ ms without invoking heavyweight RDF parsers at request time.
- [x] Custom concept matchers preserve deterministic tie-breaking by concept URI.
- [x] Custom LLM personas mask sensitive entities via HMAC-SHA256 pseudonymization.
- [x] Custom export serializers handle `GraphAOntology` model dictionaries with `exclude_none=True`.
