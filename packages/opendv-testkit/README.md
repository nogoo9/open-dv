# `opendv-testkit`

Development-only testkit providing offline deterministic harness adapters, in-memory vendor SDK shims (Google Antigravity & Anthropic Claude Agent SDK), minimal schema instance synthesis, pipeline runner test seams, and canonical test fixtures.

## Key Modules
- `opendv_testkit.synthesis`: Minimal schema instantiation and default generation for Pydantic models.
- `opendv_testkit.shims`: In-memory emulators for `google.antigravity` and `claude_agent_sdk`.
- `opendv_testkit.harness_adapter`: `DeterministicHarnessAdapter` implementing `BaseVaultAgentHarness`.
- `opendv_testkit.runner`: `build_deterministic_pipeline_runner` helper and `deterministic_pipeline_seam` context manager.
- `opendv_testkit.fixtures`: Ephemeral project/run state helpers and `seed_canonical_pe_demo(state)`.

## Strict Boundary Rules
- Dev-only package: Never imported by production modules in `packages/*/src` or `apps/*/src`.
- Never declared in production `pyproject.toml` runtime dependencies.
