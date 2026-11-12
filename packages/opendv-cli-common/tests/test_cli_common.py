"""Unit tests for opendv-cli-common package."""

from __future__ import annotations

import io
import json

from pydantic import BaseModel

from opendv_cli_common import (
    CommandExecutor,
    ExecutionResult,
    build_argument_parser,
    format_jcs,
    format_presenter_output,
    present_result,
    print_jcs,
)


class DummyDTO(BaseModel):
    b: int = 1
    a: str = "test"
    rendered: dict[str, str] = {"text": "Rendered summary", "toon": "@toon_v1 trace"}


class MockExecutor:
    def execute(self, command: str, args: any) -> tuple[int, any]:
        return 0, {"command": command}


def test_command_executor_protocol() -> None:
    executor = MockExecutor()
    assert isinstance(executor, CommandExecutor)
    res = ExecutionResult(exit_code=0, payload={"ok": True})
    assert res.is_success is True


def test_jcs_printer_deterministic() -> None:
    obj = {"z": 10, "a": 1, "m": [3, 2, 1]}
    jcs_str = format_jcs(obj)
    # Keys must be sorted: 'a', 'm', 'z'
    assert jcs_str == '{"a":1,"m":[3,2,1],"z":10}'

    buf = io.StringIO()
    print_jcs(obj, file=buf)
    assert buf.getvalue().strip() == '{"a":1,"m":[3,2,1],"z":10}'

    dto = DummyDTO()
    jcs_dto = format_jcs(dto)
    assert '"a":"test"' in jcs_dto
    assert '"b":1' in jcs_dto


def test_presenter_formats() -> None:
    dto = DummyDTO()

    # 1. TOON format
    toon_out = format_presenter_output(dto, format_type="toon")
    assert toon_out == "@toon_v1 trace"

    # 2. Text format with rendered.text
    text_out = format_presenter_output(dto, format_type="text")
    assert text_out == "Rendered summary"

    # 3. Text fallback for dict
    plain_dict = {"status": "PASS", "run_id": "run-123"}
    plain_out = format_presenter_output(plain_dict, command="test", format_type="text")
    assert "[opendv test] status=PASS run_id=run-123" in plain_out

    # 4. JSON emission
    buf = io.StringIO()
    present_result(dto, format_type="json", file=buf)
    parsed = json.loads(buf.getvalue().strip())
    assert parsed["a"] == "test"
    assert parsed["b"] == 1


def test_parser_builder_generates_all_commands() -> None:
    parser = build_argument_parser(prog="opendv")
    assert parser.prog == "opendv"

    # Test verify with options
    args = parser.parse_args(["verify", "--contract", "test.yaml", "--json"])
    assert args.subcommand == "verify"
    assert args.contract == "test.yaml"
    assert args.json is True

    # Test global option before subcommand
    args2 = parser.parse_args(["--json", "verify", "--contract", "test.yaml"])
    assert args2.subcommand == "verify"
    assert args2.json is True

    # Test nested subcommand: pipeline run
    args3 = parser.parse_args(["pipeline", "run", "--input", "spec.yaml", "--stream"])
    assert args3.subcommand == "pipeline"
    assert args3.pipeline_action == "run"
    assert args3.input == "spec.yaml"
    assert args3.stream is True

    # Test nested subcommand: hold approve with alias resolve
    args4 = parser.parse_args(["hold", "resolve", "--run-id", "r-1"])
    assert args4.subcommand == "hold"
    assert args4.hold_action == "resolve"
    assert args4.run_id == "r-1"

    # Test 3rd and 4th level recursive Studio subcommands from cli-spec.json (P13::MAJ-02)
    args5 = parser.parse_args(
        [
            "ontology",
            "studio",
            "delta",
            "validate",
            "-s",
            "s1",
            "-f",
            "delta.json",
            "--verify-grounding",
        ]
    )
    assert args5.subcommand == "ontology"
    assert args5.ontology_action == "studio"
    assert args5.studio_action == "delta"
    assert args5.studio_delta_action == "validate"
    assert args5.session == "s1"
    assert args5.file == "delta.json"
    assert args5.verify_grounding is True

    args6 = parser.parse_args(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "s1",
            "-p",
            "prop-1",
            "--base-sha256",
            "a" * 64,
            "--low-confidence-waiver",
            "-k",
            "steward.key",
            "--waiver-rationale",
            "Valid rationale text",
            "--ungrounded-concept",
            "urn:ontology:entity:c1",
            "urn:ontology:entity:c2",
        ]
    )
    assert args6.studio_action == "proposal"
    assert args6.studio_proposal_action == "accept"
    assert args6.waiver_key_file == "steward.key"
    assert args6.ungrounded_concept == ["urn:ontology:entity:c1", "urn:ontology:entity:c2"]


def test_build_and_dump_cli_tree() -> None:
    from opendv_cli_common import build_cli_tree, dump_cli_tree

    tree = build_cli_tree(prog="opendv", mode="standalone")
    assert tree["prog"] == "opendv"
    assert "commands" in tree
    assert "verify" in tree["commands"]
    assert "pipeline" in tree["commands"]

    # Standalone mode includes standalone_only 'pipeline report' and 'ontology studio init'
    assert "report" in tree["commands"]["pipeline"]["subcommands"]
    studio_subs = tree["commands"]["ontology"]["subcommands"]["studio"]["subcommands"]
    assert "init" in studio_subs
    assert "validate" in studio_subs["delta"]["subcommands"]

    # Client mode must exclude standalone_only commands 'report' and 'init'
    client_tree = build_cli_tree(prog="opendvctl", mode="client")
    assert "report" not in client_tree["commands"]["pipeline"]["subcommands"]
    client_studio_subs = client_tree["commands"]["ontology"]["subcommands"]["studio"]["subcommands"]
    assert "init" not in client_studio_subs
    assert "validate" in client_studio_subs["delta"]["subcommands"]

    jcs = dump_cli_tree(prog="opendv", mode="standalone")
    assert isinstance(jcs, str)
    assert jcs.startswith('{"commands":{')
