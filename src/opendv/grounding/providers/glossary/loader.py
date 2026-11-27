"""Bounded, anchor/alias-free YAML glossary loader with ReDoS regex validation (Layer-2a)."""

from __future__ import annotations

import hashlib
import importlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast

import yaml
from pydantic import Field, ValidationError, field_validator

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import ErrorCode, OpenDVError

MAX_GLOSSARY_BYTES: Final[int] = 2 * 1024 * 1024
_MAX_REGEX_LEN: Final[int] = 512
_MAX_UNBOUNDED_QUANTIFIERS: Final[int] = 1
_MAX_BOUNDED_STATE_PRODUCT: Final[int] = 512
_MAX_UNBOUNDED_STATE_PRODUCT: Final[int] = 4
_MAX_UNBOUNDED_WORK: Final[int] = 256
_MAX_BOUNDED_WORK: Final[int] = 65536
_MAX_MATCH_LEN: Final[int] = 10000
_CAPTURE_SUBPATTERN_COST: Final[int] = 16

GlossaryKind = Literal["entity", "event", "identifier", "attribute", "code", "relationship"]
GlossaryExpectedType = Literal[
    "string", "integer", "number", "decimal", "boolean", "date", "timestamp"
]


class _NoAliasSafeLoader(yaml.SafeLoader):
    """YAML SafeLoader that rejects both YAML anchors (&a) and aliases (*a)."""

    def compose_node(self, parent: yaml.Node | None, index: Any) -> yaml.Node:
        if self.check_event(yaml.AliasEvent) or self.anchors:
            raise yaml.composer.ComposerError(
                "YAML anchors and aliases are forbidden in glossary files"
            )
        node = super().compose_node(parent, index)
        if node is None or self.anchors or getattr(node, "anchor", None):
            raise yaml.composer.ComposerError(
                "YAML anchors and aliases are forbidden in glossary files"
            )
        return node


class GlossaryTermSpec(StrictModel):
    """Single term definition inside a structured or flat enterprise glossary YAML."""

    label: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    kind: GlossaryKind | None = None
    domain_entity: str | None = None
    target_entity: str | None = None
    parent: str | None = None
    expected_type: GlossaryExpectedType | None = None
    identifies: str | None = None
    identifier_scope: Literal["global_standard", "enterprise_sor", "local_source"] | None = None
    pii_category_hint: str | None = None
    allowed_values: list[str] = Field(default_factory=list)
    pattern: str | None = None


class GlossaryDocument(StrictModel):
    """Root glossary YAML document schema."""

    version: Literal[1]
    terms: dict[str, GlossaryTermSpec]

    @field_validator("terms")
    @classmethod
    def _validate_non_empty_terms(
        cls, value: dict[str, GlossaryTermSpec]
    ) -> dict[str, GlossaryTermSpec]:
        if not value:
            raise ValueError("Glossary 'terms' mapping must not be empty")
        return value


@dataclass(frozen=True, slots=True)
class LoadedGlossary:
    """Validated glossary document paired with SHA-256 digest and compiled safe regexes."""

    document: GlossaryDocument
    sha256: str
    compiled_patterns: dict[str, re.Pattern[str]]


def get_example_glossary_path() -> Path:
    """Return path to the bundled SaaS/billing example_glossary.yaml."""
    return Path(__file__).resolve().parent / "example_glossary.yaml"


_ALLOWED_LEAF_OPS: Final[frozenset[str]] = frozenset(
    {"LITERAL", "NOT_LITERAL", "ANY", "AT", "IN", "CATEGORY"}
)
_ALLOWED_IN_OPS: Final[frozenset[str]] = frozenset(
    {"NEGATE", "LITERAL", "RANGE", "CATEGORY"}
)
_REPEAT_OPS: Final[frozenset[str]] = frozenset({"MAX_REPEAT", "MIN_REPEAT"})
_ALLOWED_GLOBAL_FLAGS: Final[int] = (
    re.IGNORECASE | re.MULTILINE | re.DOTALL | re.UNICODE | re.ASCII
)


class _RegexStateProto(Protocol):
    @property
    def flags(self) -> int: ...


class _RegexSubPatternProto(Protocol):
    @property
    def data(self) -> Sequence[tuple[object, object]]: ...

    @property
    def state(self) -> _RegexStateProto: ...


class _RegexParserProto(Protocol):
    MAXREPEAT: int

    def parse(self, pattern: str) -> _RegexSubPatternProto: ...


_RE_PARSER = cast(_RegexParserProto, importlib.import_module("re._parser"))


@dataclass(frozen=True, slots=True)
class _AstStats:
    unbounded: int
    states: int
    max_consume: int
    work_cost: int


def _syntax_error(msg: str) -> OpenDVError:
    return OpenDVError(msg, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)


def _has_inline_comment(pattern: str) -> bool:
    """Return True if pattern contains an unescaped (?#...) outside character classes."""
    in_char_class = False
    char_class_start = -1
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if in_char_class:
            if ch == "]" and i > char_class_start:
                in_char_class = False
            i += 1
            continue
        if ch == "[":
            in_char_class = True
            char_class_start = i + 2 if (i + 1 < n and pattern[i + 1] == "^") else i + 1
            i += 1
            continue
        if pattern.startswith("(?#", i):
            return True
        i += 1
    return False


def _check_budget(stats: _AstStats) -> None:
    if stats.unbounded > _MAX_UNBOUNDED_QUANTIFIERS:
        raise _syntax_error(
            "Too many unbounded quantifiers "
            f"(>{_MAX_UNBOUNDED_QUANTIFIERS}) in glossary regex pattern."
        )
    if stats.max_consume > _MAX_MATCH_LEN or stats.work_cost > _MAX_MATCH_LEN:
        raise _syntax_error(
            f"Regex match length (>{_MAX_MATCH_LEN}) too large in glossary regex pattern."
        )
    if stats.states > _MAX_BOUNDED_STATE_PRODUCT:
        raise _syntax_error(
            "Backtracking state-space product "
            f"(>{_MAX_BOUNDED_STATE_PRODUCT}) too large in glossary regex pattern."
        )
    work = stats.states * max(1, stats.work_cost)
    if stats.unbounded >= 1:
        if stats.states > _MAX_UNBOUNDED_STATE_PRODUCT:
            raise _syntax_error(
                "Backtracking state-space product with unbounded quantifier "
                f"(>{_MAX_UNBOUNDED_STATE_PRODUCT}) too large in glossary regex pattern."
            )
        if work > _MAX_UNBOUNDED_WORK:
            raise _syntax_error(
                "Backtracking work budget with unbounded quantifier "
                f"(>{_MAX_UNBOUNDED_WORK}) exceeded in glossary regex pattern."
            )
    elif work > _MAX_BOUNDED_WORK:
        raise _syntax_error(
            f"Backtracking work budget (>{_MAX_BOUNDED_WORK}) exceeded in glossary regex pattern."
        )


def _bounded_mul(a: int, b: int, limit: int) -> int:
    if a == 0 or b == 0:
        return 0
    if a > limit // b:
        return limit + 1
    return a * b


def _bounded_pow(base: int, exp: int, limit: int) -> int:
    if exp == 0:
        return 1
    if base <= 1:
        return base
    acc = 1
    for _ in range(exp):
        acc = _bounded_mul(acc, base, limit)
        if acc > limit:
            return limit + 1
    return acc


def _analyze_seq(nodes: Sequence[tuple[object, object]]) -> _AstStats:
    unbounded = 0
    states = 1
    max_consume = 0
    work_cost = 0
    for op_obj, av in nodes:
        node_stats = _analyze_node(str(op_obj), av)
        unbounded += node_stats.unbounded
        states = _bounded_mul(states, node_stats.states, _MAX_BOUNDED_STATE_PRODUCT)
        max_consume = min(_MAX_MATCH_LEN + 1, max_consume + node_stats.max_consume)
        work_cost = min(_MAX_MATCH_LEN + 1, work_cost + node_stats.work_cost)
        _check_budget(_AstStats(unbounded, states, max_consume, work_cost))
    return _AstStats(unbounded, states, max_consume, work_cost)


def _analyze_node(op: str, av: object) -> _AstStats:
    if op in _ALLOWED_LEAF_OPS:
        leaf_work = 1
        if op == "IN":
            if not isinstance(av, Sequence):
                raise _syntax_error("Malformed character class AST in glossary regex pattern.")
            has_category = False
            for item in av:
                if (
                    not isinstance(item, tuple)
                    or len(item) != 2
                    or str(item[0]) not in _ALLOWED_IN_OPS
                ):
                    raise _syntax_error(
                        "Unsupported character class construct in glossary regex pattern."
                    )
                if str(item[0]) == "CATEGORY":
                    has_category = True
            leaf_work = 3 if has_category else 2
        elif op == "NOT_LITERAL":
            leaf_work = 2
        elif op == "CATEGORY":
            leaf_work = 3
        consume = 0 if op == "AT" else 1
        return _AstStats(unbounded=0, states=1, max_consume=consume, work_cost=leaf_work)

    if op == "SUBPATTERN":
        if not isinstance(av, tuple) or len(av) != 4:
            raise _syntax_error("Malformed group AST in glossary regex pattern.")
        group_id, add_flags, _, sub = av
        if isinstance(add_flags, int):
            if add_flags & re.VERBOSE:
                raise _syntax_error(
                    "Verbose (?x) regex flag is forbidden in glossary regex patterns."
                )
            if add_flags & ~_ALLOWED_GLOBAL_FLAGS:
                raise _syntax_error(
                    "Unsupported inline regex flag in glossary regex pattern."
                )
        sub_stats = _analyze_seq(cast(_RegexSubPatternProto, sub).data)
        if group_id is not None:
            work_cost = min(_MAX_MATCH_LEN + 1, sub_stats.work_cost + _CAPTURE_SUBPATTERN_COST)
            res = _AstStats(
                unbounded=sub_stats.unbounded,
                states=sub_stats.states,
                max_consume=sub_stats.max_consume,
                work_cost=work_cost,
            )
            _check_budget(res)
            return res
        return sub_stats

    if op == "BRANCH":
        if not isinstance(av, tuple) or len(av) != 2 or not isinstance(av[1], Sequence):
            raise _syntax_error("Malformed alternation AST in glossary regex pattern.")
        branches = cast(Sequence[_RegexSubPatternProto], av[1])
        max_unbounded = 0
        sum_states = 0
        max_consume = 0
        max_work = 0
        for branch in branches:
            b_stats = _analyze_seq(branch.data)
            max_unbounded = max(max_unbounded, b_stats.unbounded)
            sum_states = min(_MAX_BOUNDED_STATE_PRODUCT + 1, sum_states + b_stats.states)
            max_consume = max(max_consume, b_stats.max_consume)
            max_work = max(max_work, b_stats.work_cost)
        res = _AstStats(max_unbounded, sum_states, max_consume, max_work)
        _check_budget(res)
        return res

    if op in _REPEAT_OPS:
        if not isinstance(av, tuple) or len(av) != 3:
            raise _syntax_error("Malformed quantifier AST in glossary regex pattern.")
        lo_obj, hi_obj, body_obj = av
        if not isinstance(lo_obj, int) or not isinstance(hi_obj, int):
            raise _syntax_error("Malformed quantifier bounds in glossary regex pattern.")
        body = _analyze_seq(cast(_RegexSubPatternProto, body_obj).data)

        if body.unbounded > 0:
            raise _syntax_error(
                "Nested unbounded quantifiers are forbidden in glossary regex patterns."
            )

        if hi_obj == _RE_PARSER.MAXREPEAT:
            if body.states > 1:
                raise _syntax_error(
                    "Quantified alternation or nested variable quantifier is forbidden."
                )
            if body.max_consume == 0:
                raise _syntax_error(
                    "Unbounded quantifier over zero-width subpattern is forbidden."
                )
            unit_work = (body.work_cost + body.max_consume - 1) // body.max_consume
            min_consume = _bounded_mul(lo_obj, body.max_consume, _MAX_MATCH_LEN)
            min_work = max(unit_work, _bounded_mul(lo_obj, body.work_cost, _MAX_MATCH_LEN))
            res = _AstStats(
                unbounded=1, states=1, max_consume=min_consume, work_cost=min_work
            )
            _check_budget(res)
            return res

        if hi_obj > 1 and body.max_consume == 0:
            raise _syntax_error(
                "Repeated zero-width subpattern is forbidden in glossary regex patterns."
            )

        if hi_obj == 0:
            return _AstStats(unbounded=0, states=1, max_consume=0, work_cost=0)

        if body.states > 1:
            if lo_obj == hi_obj:
                rep_states = _bounded_pow(body.states, lo_obj, _MAX_BOUNDED_STATE_PRODUCT)
            else:
                rep_states = 0
                for k in range(lo_obj, hi_obj + 1):
                    rep_states = min(
                        _MAX_BOUNDED_STATE_PRODUCT + 1,
                        rep_states
                        + _bounded_pow(body.states, k, _MAX_BOUNDED_STATE_PRODUCT),
                    )
                    if rep_states > _MAX_BOUNDED_STATE_PRODUCT:
                        break
        else:
            rep_states = hi_obj - lo_obj + 1

        rep_consume = _bounded_mul(hi_obj, body.max_consume, _MAX_MATCH_LEN)
        rep_work = _bounded_mul(hi_obj, body.work_cost, _MAX_MATCH_LEN)
        res = _AstStats(
            unbounded=0, states=rep_states, max_consume=rep_consume, work_cost=rep_work
        )
        _check_budget(res)
        return res

    if op in ("ASSERT", "ASSERT_NOT"):
        raise _syntax_error("Lookarounds are forbidden in glossary regex patterns.")
    if op in ("GROUPREF", "GROUPREF_EXISTS"):
        raise _syntax_error(
            "Backreferences and conditionals are forbidden in glossary regex patterns."
        )
    if op in ("ATOMIC_GROUP", "POSSESSIVE_REPEAT"):
        raise _syntax_error(
            "Atomic groups and possessive quantifiers are forbidden in glossary regex patterns."
        )
    raise _syntax_error(f"Unsupported regex construct '{op}' in glossary regex pattern.")


def validate_safe_regex(pattern: str) -> re.Pattern[str]:
    """Validate and compile a regex with AST-level bounded-work ReDoS protection."""
    if not isinstance(pattern, str) or not pattern or len(pattern) > _MAX_REGEX_LEN:
        raise _syntax_error(f"Glossary regex pattern must be 1..{_MAX_REGEX_LEN} chars.")
    if _has_inline_comment(pattern):
        raise _syntax_error("Inline comments are forbidden in glossary regex patterns.")

    try:
        parsed = _RE_PARSER.parse(pattern)
    except (re.error, OverflowError) as exc:
        raise _syntax_error(f"Invalid glossary regex pattern '{pattern}': {exc}") from exc

    flags = parsed.state.flags
    if flags & re.VERBOSE:
        raise _syntax_error("Verbose (?x) regex flag is forbidden in glossary regex patterns.")
    if flags & ~_ALLOWED_GLOBAL_FLAGS:
        raise _syntax_error("Unsupported regex flag in glossary regex pattern.")

    stats = _analyze_seq(parsed.data)
    _check_budget(stats)

    try:
        return re.compile(pattern)
    except (re.error, OverflowError) as exc:
        raise _syntax_error(f"Invalid glossary regex pattern '{pattern}': {exc}") from exc


def load_glossary(
    glossary_path: Path | str | None = None,
    *,
    glossary_bytes: bytes | None = None,
    glossary_text: str | None = None,
) -> LoadedGlossary:
    """Load and validate a bounded, anchor/alias-free YAML glossary."""
    provided = sum(x is not None for x in (glossary_path, glossary_bytes, glossary_text))
    if provided > 1:
        raise OpenDVError(
            "Specify at most one of glossary_path, glossary_bytes, or glossary_text.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    if glossary_bytes is not None:
        raw_bytes = glossary_bytes
    elif glossary_text is not None:
        raw_bytes = glossary_text.encode("utf-8")
    else:
        resolved = Path(glossary_path) if glossary_path is not None else get_example_glossary_path()
        if not resolved.is_file():
            raise OpenDVError(
                f"Glossary file not found: {resolved}",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        try:
            with resolved.open("rb") as fh:
                raw_bytes = fh.read(MAX_GLOSSARY_BYTES + 1)
        except FileNotFoundError as exc:
            raise OpenDVError(
                f"Glossary file not found: {resolved}",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            ) from exc
        except OSError as exc:
            raise OpenDVError(
                f"Failed to read glossary file '{resolved}': {exc}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ) from exc

    if len(raw_bytes) > MAX_GLOSSARY_BYTES:
        raise OpenDVError(
            f"Glossary payload ({len(raw_bytes)} bytes) exceeds limit {MAX_GLOSSARY_BYTES}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        text = raw_bytes.decode("utf-8")
        parsed = yaml.load(text, Loader=_NoAliasSafeLoader)
        doc = GlossaryDocument.model_validate(parsed)
    except (UnicodeDecodeError, yaml.YAMLError, ValidationError) as exc:
        raise OpenDVError(
            f"Invalid glossary YAML payload: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc

    compiled: dict[str, re.Pattern[str]] = {}
    for term_id, spec in doc.terms.items():
        if spec.pattern is not None:
            compiled[term_id] = validate_safe_regex(spec.pattern)

    return LoadedGlossary(document=doc, sha256=sha256, compiled_patterns=compiled)


__all__ = [
    "MAX_GLOSSARY_BYTES",
    "GlossaryDocument",
    "GlossaryExpectedType",
    "GlossaryKind",
    "GlossaryTermSpec",
    "LoadedGlossary",
    "get_example_glossary_path",
    "load_glossary",
    "validate_safe_regex",
]
