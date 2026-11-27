"""Grounding eligibility rules, Pack v2 schema/baseline validators, exceptions, and match DTOs."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import ConfigDict, Field

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError
from opendv.models.grounding import ArchetypeKind
from opendv.models.ontology import ExternalMapping

DEFAULT_MAX_DEPTH: Final[int] = 32
BROAD_MATCH_PREDICATE: Final[str] = "skos:broadMatch"
DEFAULT_PROVENANCE: Final[str] = "deterministic"
DEFAULT_SCHEME: Final[str] = "fibo"
GROUNDING_THRESHOLD: Final[Decimal] = Decimal("0.6500")
PACK_V2_SCHEMA_VERSION: Final[str] = "2.0.0"
_DECIMAL_FOUR_PLACES: Final[Decimal] = Decimal("0.0001")
_RESTRICTION_KINDS: Final[frozenset[str]] = frozenset({"card", "min", "max", "some", "all"})


class UngroundedSeedMappingError(OpenDVError, ValueError):
    """Raised when transitive closure is attempted on an ungrounded seed (< 0.6500)."""

    error_code: ErrorCode = ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    def __init__(
        self,
        message: str,
        *,
        concept_uri: str | None = None,
        direct_seed_score: float | Decimal | None = None,
        threshold: float | Decimal = GROUNDING_THRESHOLD,
    ) -> None:
        super().__init__(message, error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION)
        self.concept_uri = concept_uri
        self.direct_seed_score = float(direct_seed_score) if direct_seed_score is not None else None
        self.threshold = float(threshold)


class TaxonomyCycleError(OpenDVError, ValueError):
    """Raised when a circular inheritance relationship is detected in taxonomic hierarchy."""

    error_code: ErrorCode = ErrorCode.SYNTAX_INVALID_ARGUMENT

    def __init__(
        self,
        message: str,
        *,
        cycle_path: list[str] | None = None,
    ) -> None:
        super().__init__(message, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)
        self.cycle_path = list(cycle_path or [])


class SubclassClosureMatch(StrictModel):
    """Inferred taxonomic superclass resolved via transitive subClassOf closure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str = Field(..., description="Target reference ontology superclass URI")
    label: str | None = Field(default=None, description="Concept human-readable label")
    depth: int = Field(..., ge=1, description="Taxonomic distance from direct seed concept")
    predicate: str = Field(
        default=BROAD_MATCH_PREDICATE,
        description="Taxonomic relationship predicate ('skos:broadMatch')",
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Inherited seed score")
    provenance: str = Field(default=DEFAULT_PROVENANCE)
    reference_pack_id: str | None = Field(default=None)
    reference_pack_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    path: tuple[str, ...] = Field(default_factory=tuple)
    definition: str | None = Field(default=None)

    @property
    def satisfies_path_a(self) -> bool:
        return False

    @property
    def is_standalone_grounding_eligible(self) -> bool:
        return False

    def to_external_mapping(
        self,
        *,
        mapping_type: Literal["skos:exactMatch", "skos:closeMatch"] = "skos:closeMatch",
    ) -> ExternalMapping:
        if mapping_type not in ("skos:exactMatch", "skos:closeMatch"):
            raise OpenDVError(
                f"V2 ExternalMapping.mapping_type forbids '{mapping_type}'",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        return ExternalMapping(
            standard_uri=self.uri,
            reference_pack_id=self.reference_pack_id or DEFAULT_SCHEME,
            reference_pack_sha256=self.reference_pack_sha256 or ("0" * 64),
            mapping_type=mapping_type,
            confidence=Decimal(str(self.confidence)).quantize(
                _DECIMAL_FOUR_PLACES, rounding=ROUND_HALF_EVEN
            ),
        )


def normalize_score(score: float | Decimal | str | int) -> Decimal:
    """Normalize and quantize confidence score to 4 decimal places."""
    if isinstance(score, Decimal):
        dec = score
    elif isinstance(score, (float, int, str)):
        try:
            dec = Decimal(str(score))
        except Exception as err:
            raise ValueError(f"Invalid score representation '{score}': {err}") from err
    else:
        raise TypeError(f"Unsupported score type: {type(score).__name__}")

    if dec < Decimal("0.0") or dec > Decimal("1.0"):
        raise ValueError(f"Confidence score must be between 0.0 and 1.0, got {dec}")
    return dec.quantize(_DECIMAL_FOUR_PLACES, rounding=ROUND_HALF_EVEN)


def can_satisfy_path_a_grounding(predicate: str | None) -> bool:
    """Only direct 'skos:exactMatch' and 'skos:closeMatch' satisfy Path A."""
    if predicate is None:
        return False
    return predicate.strip().lower() in (
        "skos:exactmatch",
        "exactmatch",
        "skos:closematch",
        "closematch",
    )


def validate_closure_grounding_eligibility(matches: Sequence[SubclassClosureMatch]) -> None:
    """Assert transitive subclass closure matches cannot satisfy standalone Path A."""
    for match in matches:
        if match.predicate != BROAD_MATCH_PREDICATE:
            raise ValueError(
                f"Invalid closure predicate '{match.predicate}': must be '{BROAD_MATCH_PREDICATE}'"
            )
        if match.satisfies_path_a or match.is_standalone_grounding_eligible:
            raise ValueError("Transitive closure matches cannot serve as standalone grounding")


def find_baseline_digests_path() -> Path | None:
    """Locate schemas/v2/baseline-digests.json or honor OPENDV_BASELINE_DIGESTS_PATH."""
    env_override = os.environ.get("OPENDV_BASELINE_DIGESTS_PATH")
    if env_override:
        p = Path(env_override).resolve()
        if not p.is_file():
            raise OpenDVError(
                f"Configured OPENDV_BASELINE_DIGESTS_PATH not found: {env_override}",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        return p

    current: Path | None = Path(__file__).resolve().parent
    for _ in range(8):
        if current is None:
            break
        candidate = current / "schemas" / "v2" / "baseline-digests.json"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def verify_expected_sha(expected_sha256: str, computed_sha256: str) -> None:
    """Verify full 64-hex or 8-hex digest prefix against computed SHA-256."""
    norm = expected_sha256.strip().lower()
    computed_prefix = computed_sha256[:8]
    if len(norm) == 64 and norm != computed_sha256:
        raise ReferenceOntologyCorruptError(
            f"Pack digest '{computed_sha256}' does not match expected '{norm}'"
        )
    if len(norm) == 8 and norm != computed_prefix:
        raise ReferenceOntologyCorruptError(
            f"Pack prefix '{computed_prefix}' does not match expected '{norm}'"
        )
    if len(norm) not in (8, 64):
        raise ReferenceOntologyCorruptError(
            f"Invalid expected_sha256 length ({len(norm)}): must be 8 or 64"
        )


def verify_pack_against_baselines(
    resolved_path: Path,
    computed_sha256: str,
    class_count: int,
    property_count: int,
    individual_count: int,
) -> None:
    """Verify package-bundled fibo-core-pack.json against schemas/v2/baseline-digests.json."""
    baseline_path = find_baseline_digests_path()
    if baseline_path is None:
        return
    try:
        baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as err:
        raise ReferenceOntologyCorruptError(
            f"Baseline digests at {baseline_path} corrupted: {err}"
        ) from err

    fibo_spec = baseline_data.get("fibo_core_pack") if isinstance(baseline_data, dict) else None
    if not isinstance(fibo_spec, dict):
        raise ReferenceOntologyCorruptError(
            f"Baseline digests missing 'fibo_core_pack' in {baseline_path}"
        )
    expected_sha = fibo_spec.get("sha256")
    if not isinstance(expected_sha, str) or expected_sha.lower() != computed_sha256:
        raise ReferenceOntologyCorruptError(
            f"Pack SHA-256 '{computed_sha256}' mismatches baseline "
            f"'{expected_sha}' ({resolved_path})"
        )
    if fibo_spec.get("concept_count") != class_count:
        raise ReferenceOntologyCorruptError(
            f"Pack concept_count {class_count} mismatches "
            f"baseline '{fibo_spec.get('concept_count')}'"
        )
    if "property_count" in fibo_spec and fibo_spec["property_count"] != property_count:
        raise ReferenceOntologyCorruptError(
            f"Pack property_count {property_count} mismatches '{fibo_spec['property_count']}'"
        )
    if "individual_count" in fibo_spec and fibo_spec["individual_count"] != individual_count:
        raise ReferenceOntologyCorruptError(
            f"Pack individual_count {individual_count} mismatches '{fibo_spec['individual_count']}'"
        )


def _assert_str_list(val: Mapping[str, Any], field: str, kind: str, uri: str) -> None:
    if field not in val:
        return
    items = val[field]
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        raise ReferenceOntologyCorruptError(f"{kind} '{uri}' field '{field}' must be list[str]")


def validate_pack_v2_data(
    data: Any,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Validate Pack v2 (2.0.0) JSON root and all class/property/individual entries."""
    if not isinstance(data, dict):
        raise ReferenceOntologyCorruptError(
            f"Pack root must be a JSON object, got {type(data).__name__}"
        )
    classes = data.get("classes")
    properties = data.get("properties")
    individuals = data.get("individuals")
    metadata = data.get("metadata")
    if (
        not isinstance(classes, dict)
        or not isinstance(properties, dict)
        or not isinstance(individuals, dict)
        or not isinstance(metadata, dict)
        or not classes
        or not properties
    ):
        raise ReferenceOntologyCorruptError(
            "Pack v2 must contain non-empty 'classes', 'properties', 'individuals', and 'metadata'"
        )
    if metadata.get("pack_schema_version") != PACK_V2_SCHEMA_VERSION:
        raise ReferenceOntologyCorruptError(
            f"Unsupported pack_schema_version '{metadata.get('pack_schema_version')}', "
            f"expected '{PACK_V2_SCHEMA_VERSION}'"
        )
    if not isinstance(metadata.get("version"), str) or not isinstance(metadata.get("pack_id"), str):
        raise ReferenceOntologyCorruptError("Pack metadata requires string 'version' and 'pack_id'")

    for uri, val in classes.items():
        if (
            not isinstance(val, dict)
            or val.get("uri") != uri
            or not isinstance(val.get("label"), str)
        ):
            raise ReferenceOntologyCorruptError(f"Malformed Class entry '{uri}'")
        for fld in (
            "synonyms",
            "sub_class_of",
            "disjoint_with",
            "is_played_by",
            "identified_entity_uris",
        ):
            _assert_str_list(val, fld, "Class", uri)
        if "associated_properties" in val:
            assoc = val["associated_properties"]
            if not isinstance(assoc, list):
                raise ReferenceOntologyCorruptError(
                    f"Class '{uri}' associated_properties must be list"
                )
            for item in assoc:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("property_uri"), str)
                    or item.get("restriction_kind") not in _RESTRICTION_KINDS
                    or not isinstance(item.get("target_uris", []), list)
                    or not all(isinstance(t, str) for t in item.get("target_uris", []))
                ):
                    raise ReferenceOntologyCorruptError(
                        f"Class '{uri}' invalid associated_properties item"
                    )

    for uri, val in properties.items():
        if (
            not isinstance(val, dict)
            or val.get("uri") != uri
            or val.get("property_type") not in ("ObjectProperty", "DatatypeProperty")
        ):
            raise ReferenceOntologyCorruptError(f"Malformed Property entry '{uri}'")
        if "label" in val and not isinstance(val["label"], str):
            raise ReferenceOntologyCorruptError(f"Property '{uri}' label must be str")
        _assert_str_list(val, "synonyms", "Property", uri)
        _assert_str_list(val, "sub_property_of", "Property", uri)
        inv = val.get("inverse_of")
        if inv is not None and not (
            isinstance(inv, str) or (isinstance(inv, list) and all(isinstance(x, str) for x in inv))
        ):
            raise ReferenceOntologyCorruptError(f"Property '{uri}' invalid inverse_of")

    for uri, val in individuals.items():
        if (
            not isinstance(val, dict)
            or val.get("uri") != uri
            or not isinstance(val.get("label"), str)
        ):
            raise ReferenceOntologyCorruptError(f"Malformed Individual entry '{uri}'")
        _assert_str_list(val, "aliases", "Individual", uri)
        _assert_str_list(val, "types", "Individual", uri)

    return metadata, classes, properties, individuals


FIBO_ID_SPECS: Final[dict[str, tuple[str | None, str | None, bool]]] = {
    "LegalEntityIdentifier": ("iso_17442_lei", "GLEIF", True),
    "InternationalSecuritiesIdentificationNumber": ("iso_6166_isin", "ISO_6166", True),
    "CommitteeOnUniformSecuritiesIdentificationProceduresNumber": ("cusip", "CUSIP", False),
    "StockExchangeDailyOfficialListCode": ("sedol", "SEDOL", False),
    "InternationalBankAccountIdentifier": ("iban", "ISO_13616", True),
    "RoutingTransitNumber": ("aba_routing", "ABA", False),
    "BusinessIdentifierCode": ("swift_bic", "SWIFT", True),
    "FinancialInstrumentGlobalIdentifier": ("figi", "OPENFIGI", True),
    "MarketIdentifierCode": (None, None, True),
    "CentralIndexKey": (None, "SEC_CIK", False),
    "EntityLegalFormIdentifier": (None, "ISO_20275", False),
    "CurrencyIdentifier": (None, "ISO_4217", False),
}
FIBO_PII_MAP: Final[dict[str, str]] = {
    "PersonName": "PERSON_NAME", "IndividualName": "PERSON_NAME", "DateOfBirth": "DOB",
    "BirthDate": "DOB", "PostalAddress": "ADDRESS", "StreetAddress": "ADDRESS",
    "ConventionalStreetAddress": "ADDRESS", "ElectronicMailAddress": "EMAIL",
    "TelephoneNumber": "PHONE", "SocialSecurityNumber": "SSN",
    "TaxIdentificationNumber": "TAX_ID", "TaxIdentifier": "TAX_ID",
    "NationalIdentificationNumber": "NATIONAL_ID", "PassportNumber": "PASSPORT",
    "BankAccountNumber": "BANK_ACCOUNT", "BankAccountIdentifier": "BANK_ACCOUNT",
    "RoutingTransitNumber": "ABA",
}
CMNS_RANGE_ARCHETYPES: Final[dict[str, ArchetypeKind]] = {
    **dict.fromkeys((
        "ExplicitDate", "Date", "DateTime", "CombinedDateTime", "DateTimeStamp",
        "DatePeriod", "ExplicitDatePeriod", "Duration", "ExplicitDuration",
    ), "temporal"),
    **dict.fromkeys(("Country", "GeopoliticalEntity", "GeographicRegion"), "code_classifier"),
    **dict.fromkeys(("Ratio", "ScalarQuantityValue"), "quantity_measure"),
    **dict.fromkeys(("PartyRole", "OrganizationMember"), "party_role"),
    **dict.fromkeys((
        "StructuredCollection", "Record", "RegistrationAuthority", "Jurisdiction",
    ), "entity"),
}
XSD_TEMPORAL_TYPES: Final[frozenset[str]] = frozenset({
    "date", "datetime", "datetimestamp", "time", "gyear", "gyearmonth",
})
FIBO_INDIVIDUAL_TYPE_PRIORITY: Final[tuple[str, ...]] = (
    "DayCountConvention", "BusinessDayConvention", "InterestRateBenchmark",
    "ReferenceInterestRate", "RegulatoryAgency", "CentralBank", "RegulatedExchange", "Exchange",
)
BENCHMARK_LOCALS: Final[frozenset[str]] = frozenset({
    "ReferenceInterestRate", "InterestRateBenchmark",
})


def uri_local_name(uri: str) -> str:
    return uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def infer_xsd_range_archetype(range_uri: str) -> tuple[str | None, bool]:
    frag = uri_local_name(range_uri).lower()
    if frag == "boolean":
        return (None, True)
    if frag in XSD_TEMPORAL_TYPES:
        return ("temporal", False)
    is_num = "int" in frag or frag in ("decimal", "float", "double", "long", "short")
    return ("quantity_measure" if is_num else "value_object", False)


def infer_property_pii_fallback(prop_uri: str) -> str | None:
    low = uri_local_name(prop_uri).lower()
    if "dateofbirth" in low or "birthdate" in low:
        return "DOB"
    if "streetaddress" in low or "postaladdress" in low:
        return "ADDRESS"
    return None


def extract_individual_keys(uri: str, ind: Mapping[str, Any]) -> list[str]:
    loc = uri_local_name(uri)
    keys = [str(a).strip() for a in ind.get("aliases", ()) if str(a).strip()]
    if lbl := str(ind.get("label", "")).strip():
        keys.append(lbl)
        if lbl.lower().startswith("day-count convention "):
            keys.append(lbl[21:])
    keys.append(loc)
    for pf in ("DayCountConvention-", "BusinessDay", "Facility-"):
        if loc.startswith(pf) and len(loc) > len(pf):
            keys.append(loc[len(pf):])
    if "-" in loc:
        h, t = loc.split("-", 1)
        if len(h) == 3 and h.isupper() and len(t) >= 4:
            keys.append(t)
    return keys


__all__ = [
    "BENCHMARK_LOCALS",
    "BROAD_MATCH_PREDICATE",
    "CMNS_RANGE_ARCHETYPES",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_PROVENANCE",
    "DEFAULT_SCHEME",
    "FIBO_ID_SPECS",
    "FIBO_INDIVIDUAL_TYPE_PRIORITY",
    "FIBO_PII_MAP",
    "GROUNDING_THRESHOLD",
    "PACK_V2_SCHEMA_VERSION",
    "XSD_TEMPORAL_TYPES",
    "SubclassClosureMatch",
    "TaxonomyCycleError",
    "UngroundedSeedMappingError",
    "can_satisfy_path_a_grounding",
    "extract_individual_keys",
    "find_baseline_digests_path",
    "infer_property_pii_fallback",
    "infer_xsd_range_archetype",
    "normalize_score",
    "uri_local_name",
    "validate_closure_grounding_eligibility",
    "validate_pack_v2_data",
    "verify_expected_sha",
    "verify_pack_against_baselines",
]
