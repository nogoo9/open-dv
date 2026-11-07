"""Unit tests for RFC 8785 JSON Canonicalization Scheme (JCS)."""

from __future__ import annotations

import pytest

from opendv_contracts.dtos.common import RenderedOutput
from opendv_contracts.jcs import (
    FloatDomainError,
    IntegerDomainError,
    dumps_jcs,
    dumps_jcs_bytes,
    loads_jcs,
)


def test_jcs_primitives() -> None:
    assert dumps_jcs(None) == "null"
    assert dumps_jcs(True) == "true"
    assert dumps_jcs(False) == "false"
    assert dumps_jcs(0) == "0"
    assert dumps_jcs(42) == "42"
    assert dumps_jcs(-17) == "-17"


def test_jcs_numbers_floats_and_integers() -> None:
    # Trailing .0 stripped per ECMAScript 7.1.12.1
    assert dumps_jcs(1.0) == "1"
    assert dumps_jcs(12.80) == "12.8"
    assert dumps_jcs(0.0) == "0"
    # Negative zero must NOT serialize with minus sign
    assert dumps_jcs(-0.0) == "0"
    assert dumps_jcs(100.0) == "100"
    assert dumps_jcs(-5.0) == "-5"

    # Exponential notation threshold tests
    assert dumps_jcs(1e20) == "100000000000000000000"
    assert dumps_jcs(1e21) == "1e+21"
    assert dumps_jcs(-1e21) == "-1e+21"
    assert dumps_jcs(1e-6) == "0.000001"
    assert dumps_jcs(1e-7) == "1e-7"


def test_jcs_number_domain_errors() -> None:
    with pytest.raises(FloatDomainError):
        dumps_jcs(float("nan"))
    with pytest.raises(FloatDomainError):
        dumps_jcs(float("inf"))
    with pytest.raises(FloatDomainError):
        dumps_jcs(float("-inf"))

    # Integers outside safe IEEE 754 float range
    with pytest.raises(IntegerDomainError):
        dumps_jcs(2**53)
    with pytest.raises(IntegerDomainError):
        dumps_jcs(-(2**53))


def test_jcs_strings_and_unicode() -> None:
    # Unicode characters must NOT be escaped
    assert dumps_jcs("hello world") == '"hello world"'
    assert dumps_jcs("café") == '"café"'
    assert dumps_jcs("こんにちは") == '"こんにちは"'
    assert dumps_jcs("🚀") == '"🚀"'
    assert dumps_jcs("alpha/beta\\gamma") == '"alpha/beta\\\\gamma"'

    # Control character escaping
    assert dumps_jcs("\b\f\n\r\t") == '"\\b\\f\\n\\r\\t"'
    assert dumps_jcs("\x00\x1f") == '"\\u0000\\u001f"'
    assert dumps_jcs('"quoted"') == '"\\"quoted\\""'


def test_jcs_key_sorting_utf16_code_units() -> None:
    # Standard alphanumeric sort
    data = {"b": 2, "a": 1, "c": 3}
    assert dumps_jcs(data) == '{"a":1,"b":2,"c":3}'

    # RFC 8785 §3.2.3: Surrogate pairs sort before BMP U+E000..U+FFFF
    # \U00010000 encodes to 0xD800 0xDC00 in UTF-16BE (bytes: \xd8\x00\xdc\x00)
    # \uFFFF encodes to 0xFFFF in UTF-16BE (bytes: \xff\xff)
    # Since 0xD8 < 0xFF, \U00010000 must sort BEFORE \uFFFF
    surrogate_data = {"\uffff": 1, "\U00010000": 2}
    assert dumps_jcs(surrogate_data) == '{"\U00010000":2,"\uffff":1}'


def test_jcs_collections_and_nesting() -> None:
    assert dumps_jcs([]) == "[]"
    assert dumps_jcs({}) == "{}"
    assert dumps_jcs([1, 2, 3]) == "[1,2,3]"
    assert dumps_jcs({"nested": [None, True, False, 3.14]}) == '{"nested":[null,true,false,3.14]}'


def test_jcs_bytes_and_roundtrip() -> None:
    obj = {"message": "OpenDV", "code": 200, "status": "OK", "tags": ["a", "b"]}
    serialized_bytes = dumps_jcs_bytes(obj)
    assert isinstance(serialized_bytes, bytes)
    assert serialized_bytes == b'{"code":200,"message":"OpenDV","status":"OK","tags":["a","b"]}'

    deserialized = loads_jcs(serialized_bytes)
    assert deserialized == obj


def test_jcs_pydantic_model_serialization() -> None:
    model = RenderedOutput(summary="Gate PASS", toon="G0..G5 PASS", text="Scorecard summary")
    expected = '{"summary":"Gate PASS","text":"Scorecard summary","toon":"G0..G5 PASS"}'
    assert dumps_jcs(model) == expected
