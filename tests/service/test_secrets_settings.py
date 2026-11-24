"""UAT-V25-SVC-02: KeyringSecretStore, SettingsStore, precedence, ACLs & 0-skip fallback."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import keyring.errors
from keyring.backends import fail, null
import pytest

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import (
    ProjectSettingsSnapshot,
    TrustedWorkspaceSpec,
    WorkspaceSettings,
)
from opendv.models.sources import ConnectorConfigPayload, ExternalMcpServerSpec
from opendv.service.os_platform import resolve_user_state_dir
from opendv.service.secrets import KeyringSecretStore, probe_keyring_available
from opendv.service.settings_store import SettingsStore
from tests.helpers.secret_backends import (
    FailingKeyringBackend,
    InMemoryKeyringBackend,
    OwnerAclStateHelper,
)


def test_workspace_digest_isolation_and_canonical_paths(tmp_path: Path) -> None:
    ws_a = tmp_path / "Workspace_Alpha"
    ws_b = tmp_path / "Workspace_Beta"
    ws_a.mkdir()
    ws_b.mkdir()
    state_dir = tmp_path / "user_state"

    kr = InMemoryKeyringBackend()
    store_a = KeyringSecretStore(
        ws_a,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="keyring"),
        keyring_backend=kr,
        environ={},
    )
    store_b = KeyringSecretStore(
        ws_b,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="keyring"),
        keyring_backend=kr,
        environ={},
    )

    store_a.set_secret("DATABRICKS_TOKEN", "dapi-secret-alpha-123")
    assert store_a.get_secret("DATABRICKS_TOKEN") == "dapi-secret-alpha-123"
    assert store_b.get_secret("DATABRICKS_TOKEN") is None

    # Canonical alias variant (relative segment + forward slash; case variant on Windows)
    alias_raw = str(ws_a / ".").replace("\\", "/") + "/"
    if os.name == "nt":
        alias_raw = alias_raw.upper()
    store_a_alias = KeyringSecretStore(
        alias_raw,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="keyring"),
        keyring_backend=kr,
        environ={},
    )
    assert store_a_alias.workspace_digest == store_a.workspace_digest
    assert store_a_alias.get_secret("DATABRICKS_TOKEN") == "dapi-secret-alpha-123"

    # Also verify isolation in user_state_file mode
    file_a = KeyringSecretStore(
        ws_a,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="user_state_file"),
        environ={},
    )
    file_b = KeyringSecretStore(
        ws_b,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="user_state_file"),
        environ={},
    )
    file_a.set_secret("OPENAI_API_KEY", "sk-alpha-file-secret")
    assert file_a.get_secret("OPENAI_API_KEY") == "sk-alpha-file-secret"
    assert file_b.get_secret("OPENAI_API_KEY") is None


def test_secret_precedence_matrix(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"
    kr = InMemoryKeyringBackend()
    env = {
        "DATABRICKS_TOKEN": "env-token-val",
        "GEMINI_API_KEY": "env-only-gemini-val",
    }

    ws_settings = WorkspaceSettings(secret_backend="keyring", secret_precedence="store_first")
    store = KeyringSecretStore(
        ws,
        state_dir=state_dir,
        settings=ws_settings,
        keyring_backend=kr,
        environ=env,
    )
    store.set_secret("DATABRICKS_TOKEN", "stored-token-val")

    # 1. Default store_first: store beats env
    assert store.get_secret("DATABRICKS_TOKEN") == "stored-token-val"
    # Fallback when store lacks key: returns env
    assert store.get_secret("GEMINI_API_KEY") == "env-only-gemini-val"

    # 2. ProjectSettingsSnapshot overrides WorkspaceSettings
    proj_env = ProjectSettingsSnapshot(secret_precedence="env_first")
    assert store.get_secret("DATABRICKS_TOKEN", project_precedence=proj_env.secret_precedence) == "env-token-val"

    # 3. OPENDV_SECRET_PRECEDENCE overrides Project & Workspace
    env["OPENDV_SECRET_PRECEDENCE"] = "store_first"
    assert store.get_secret("DATABRICKS_TOKEN", project_precedence="env_first") == "stored-token-val"

    # 4. --env-first overrides OPENDV_SECRET_PRECEDENCE, Project, and Workspace
    assert (
        store.get_secret(
            "DATABRICKS_TOKEN",
            project_precedence="store_first",
            cli_env_first=True,
        )
        == "env-token-val"
    )

    # Summaries reflect active precedence
    summaries_store = {
        s.key: s
        for s in store.list_secret_summaries(["DATABRICKS_TOKEN", "GEMINI_API_KEY", "MISSING_KEY"])
    }
    assert summaries_store["DATABRICKS_TOKEN"].backend == "keyring"
    assert summaries_store["GEMINI_API_KEY"].backend == "env"
    assert summaries_store["MISSING_KEY"].present is False
    assert summaries_store["MISSING_KEY"].backend == "unset"

    store.delete_secret("DATABRICKS_TOKEN")
    after_del = {s.key: s for s in store.list_secret_summaries(["DATABRICKS_TOKEN"])}
    assert after_del["DATABRICKS_TOKEN"].backend == "env"

    # Invalid OPENDV_SECRET_PRECEDENCE raises SYNTAX_INVALID_ARGUMENT
    env["OPENDV_SECRET_PRECEDENCE"] = "bogus_mode"
    with pytest.raises(OpenDVError) as exc_info:
        store.get_secret("DATABRICKS_TOKEN")
    assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_keyring_and_headless_user_state_file_fallback(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"

    assert probe_keyring_available(fail.Keyring()) is False
    assert probe_keyring_available(null.Keyring()) is False

    # Pinned user_state_file never calls keyring backend
    kr_unused = InMemoryKeyringBackend()
    pinned_file_store = KeyringSecretStore(
        ws,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="user_state_file"),
        keyring_backend=kr_unused,
        environ={},
    )
    summary = pinned_file_store.set_secret("ANTHROPIC_API_KEY", "sk-ant-pinned-1")
    assert summary.backend == "user_state_file"
    assert kr_unused.calls == []

    # Headless keyring errors cleanly fall back to user_state_file with 0 skips
    for err_cls in (
        keyring.errors.NoKeyringError,
        keyring.errors.InitError,
        keyring.errors.KeyringLocked,
    ):
        failing = FailingKeyringBackend(error_cls=err_cls)
        fallback_store = KeyringSecretStore(
            ws,
            state_dir=state_dir,
            settings=WorkspaceSettings(secret_backend="keyring"),
            keyring_backend=failing,
            environ={},
            allow_headless_fallback=True,
        )
        res = fallback_store.set_secret("FALLBACK_KEY", f"val-{err_cls.__name__}")
        assert res.backend == "user_state_file"
        assert fallback_store.get_secret("FALLBACK_KEY") == f"val-{err_cls.__name__}"


def test_owner_only_acl_and_non_owner_rejection(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"
    acl_helper = OwnerAclStateHelper(state_dir)

    store = KeyringSecretStore(
        ws,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="user_state_file"),
        environ={},
    )
    store.set_secret("DB_TOKEN", "super-secret-value")
    assert acl_helper.verify_owner_only(store.secrets_path) is True

    # Grant non-owner access -> read, write, and list MUST fail closed
    acl_helper.grant_non_owner_access(store.secrets_path)
    assert acl_helper.verify_owner_only(store.secrets_path) is False

    for action in (
        lambda: store.get_secret("DB_TOKEN"),
        lambda: store.set_secret("DB_TOKEN", "new-val"),
        lambda: store.list_secret_summaries(["DB_TOKEN"]),
    ):
        with pytest.raises(OpenDVError) as exc_info:
            action()
        assert exc_info.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR

    # Restore owner-only ACL and verify corrupt JSON fails closed
    acl_helper.apply_owner_only(store.secrets_path)
    store.secrets_path.write_text("{corrupt json", encoding="utf-8")
    with pytest.raises(OpenDVError) as json_exc:
        store.get_secret("DB_TOKEN")
    assert json_exc.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR


def test_missing_secret_binding_preserves_connector_config_and_zero_secrets_on_disk(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"
    settings_store = SettingsStore(user_state_dir=state_dir)

    profile = ConnectorConfigPayload(
        adapter_id="databricks_uc",
        display_name="Databricks UC Prod",
        options={
            "host": "https://adb-123456.azuredatabricks.net",
            "http_path": "/sql/1.0/warehouses/wh1",
            "catalog": "prod",
            "schema": "core",
        },
        secret_refs=["DATABRICKS_TOKEN"],
        selected_tables=["holdings"],
    )
    saved_settings = settings_store.save_settings(
        WorkspaceSettings(
            secret_backend="user_state_file",
            connection_profiles={"dbx_prod": profile},
        )
    )
    assert "dbx_prod" in saved_settings.connection_profiles

    store = KeyringSecretStore(
        ws,
        state_dir=state_dir,
        settings_store=settings_store,
        environ={},
    )

    with pytest.raises(OpenDVError) as exc_info:
        store.require_secret("DATABRICKS_TOKEN")
    assert exc_info.value.error_code == ErrorCode.NEEDS_LOCAL_SECRET_BINDING
    assert exc_info.value.http_status == 422
    assert int(exc_info.value.exit_code) == 2

    # Connector config remains intact even when secret is missing
    reloaded = settings_store.load_settings()
    assert reloaded.connection_profiles["dbx_prod"].secret_refs == ["DATABRICKS_TOKEN"]
    assert reloaded.connection_profiles["dbx_prod"].selected_tables == ["holdings"]

    secret_val = "dapi-synthetic-test-token-01"
    store.set_secret("DATABRICKS_TOKEN", secret_val)
    assert store.require_secret("DATABRICKS_TOKEN") == secret_val

    # Zero secrets in settings.json or workspace OPENDV_HOME
    assert secret_val not in settings_store.settings_path.read_text(encoding="utf-8")
    for p in ws.rglob("*"):
        if p.is_file():
            assert secret_val not in p.read_text(encoding="utf-8", errors="ignore")

    # Plaintext secret keys inside connection_profiles.options must be rejected
    bad_profile = ConnectorConfigPayload(
        adapter_id="databricks_uc",
        display_name="Bad",
        options={"host": "https://adb.azuredatabricks.net", "access_token": secret_val},
        secret_refs=["DATABRICKS_TOKEN"],
    )
    with pytest.raises(OpenDVError) as bad_exc:
        settings_store.save_settings(WorkspaceSettings(connection_profiles={"bad": bad_profile}))
    assert bad_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_settings_and_mcp_servers_persistence_and_user_state_env_resolution(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "user_state"
    acl_helper = OwnerAclStateHelper(state_dir)
    settings_store = SettingsStore(user_state_dir=state_dir)

    ws_settings = WorkspaceSettings(
        secret_backend="user_state_file",
        secret_precedence="env_first",
        harness_provider="claude-cli",
        model_overrides={"ship-vault": "claude-sonnet-4-6"},
        trusted_workspaces=[
            TrustedWorkspaceSpec(
                workspace_root=str(tmp_path / "ws"),
                agent_pack_sha256="e" * 64,
                trusted_at="2026-11-24T00:00:00Z",
            )
        ],
    )
    saved = settings_store.save_settings(ws_settings)
    assert saved.updated_at != ""
    assert acl_helper.verify_owner_only(settings_store.settings_path) is True
    assert settings_store.load_settings().model_overrides == {"ship-vault": "claude-sonnet-4-6"}

    servers = [
        ExternalMcpServerSpec(
            server_id="fibo-lookup",
            transport="stdio",
            command=["uv", "run", "fibo-mcp"],
            secret_refs=["FIBO_TOKEN"],
            allow_scout_calls=True,
            allow_document_pages=False,
        ),
        ExternalMcpServerSpec(
            server_id="sec-edgar",
            transport="streamable_http",
            url="https://api.example.com/mcp",
            secret_refs=["EDGAR_KEY"],
            allow_scout_calls=True,
            allow_document_pages=True,
        ),
    ]
    settings_store.save_mcp_servers(servers)
    assert acl_helper.verify_owner_only(settings_store.mcp_servers_path) is True
    loaded_servers = settings_store.load_mcp_servers()
    assert [s.server_id for s in loaded_servers] == ["fibo-lookup", "sec-edgar"]

    # Verify duplicate server_id fails cleanly
    with pytest.raises(OpenDVError) as dup_exc:
        settings_store.save_mcp_servers([servers[0], servers[0]])
    assert dup_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Env resolution without Path.home()/.expanduser()
    assert resolve_user_state_dir(
        env={"LOCALAPPDATA": str(tmp_path / "AppLocal")}, platform_name="nt"
    ) == (tmp_path / "AppLocal" / "opendv")
    assert resolve_user_state_dir(
        env={"XDG_STATE_HOME": "/tmp/xdg_state"}, platform_name="posix"
    ) == Path("/tmp/xdg_state/opendv")
    raw_json = json.loads(settings_store.settings_path.read_text(encoding="utf-8"))
    assert raw_json["schema_version"] == "2.0"


def test_concurrent_user_state_file_set_and_get_preserves_all_keys(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"
    acl_helper = OwnerAclStateHelper(state_dir)

    num_threads = 8
    total_keys = 16
    barrier = threading.Barrier(num_threads)
    errors: list[BaseException] = []
    err_lock = threading.Lock()

    def worker(worker_idx: int) -> None:
        store = KeyringSecretStore(
            ws,
            state_dir=state_dir,
            settings=WorkspaceSettings(secret_backend="user_state_file"),
            environ={},
        )
        try:
            barrier.wait(timeout=10.0)
            for offset in range(worker_idx, total_keys, num_threads):
                key = f"K_{offset}"
                val = f"V_{offset}"
                store.set_secret(key, val)
                assert store.get_secret(key) == val
        except BaseException as exc:
            with err_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
        assert not t.is_alive()

    assert errors == []
    verifier_store = KeyringSecretStore(
        ws,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="user_state_file"),
        environ={},
    )
    for idx in range(total_keys):
        assert verifier_store.get_secret(f"K_{idx}") == f"V_{idx}"
    assert acl_helper.verify_owner_only(verifier_store.secrets_path) is True


def test_shared_instance_concurrent_set_and_delete_keeps_known_keys_consistent(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"
    shared_store = KeyringSecretStore(
        ws,
        state_dir=state_dir,
        settings=WorkspaceSettings(secret_backend="user_state_file"),
        environ={},
    )
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []
    err_lock = threading.Lock()

    def setter(start: int) -> None:
        try:
            barrier.wait(timeout=10.0)
            for idx in range(start, 20, 2):
                shared_store.set_secret(f"RACE_{idx}", f"val-{idx}")
        except BaseException as exc:
            with err_lock:
                errors.append(exc)

    def deleter() -> None:
        try:
            barrier.wait(timeout=10.0)
            for idx in range(0, 20, 2):
                shared_store.delete_secret(f"RACE_{idx}")
        except BaseException as exc:
            with err_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=setter, args=(0,)),
        threading.Thread(target=setter, args=(1,)),
        threading.Thread(target=deleter),
        threading.Thread(target=deleter),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
        assert not t.is_alive()

    assert errors == []
    summaries = {s.key: s for s in shared_store.list_secret_summaries()}
    for key, summary in summaries.items():
        disk_val = shared_store.get_secret(key)
        assert summary.present is (disk_val is not None)
    for idx in range(1, 20, 2):
        assert summaries[f"RACE_{idx}"].present is True


def test_concurrent_settings_and_mcp_servers_save_load_zero_errors(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"
    acl_helper = OwnerAclStateHelper(state_dir)

    num_threads = 6
    iterations = 25
    barrier = threading.Barrier(num_threads)
    errors: list[BaseException] = []
    err_lock = threading.Lock()

    def worker(worker_idx: int) -> None:
        store = SettingsStore(user_state_dir=state_dir)
        secrets = KeyringSecretStore(
            ws,
            state_dir=state_dir,
            settings_store=store,
            environ={},
        )
        try:
            barrier.wait(timeout=10.0)
            for step in range(iterations):
                if worker_idx % 2 == 0:
                    store.save_settings(
                        WorkspaceSettings(
                            secret_backend="user_state_file",
                            model_overrides={"ship-vault": f"m-{worker_idx}-{step}"},
                        )
                    )
                    store.save_mcp_servers(
                        [
                            ExternalMcpServerSpec(
                                server_id=f"srv-{worker_idx}-{step}",
                                transport="stdio",
                                command=["uv", "run", "srv"],
                                allow_scout_calls=True,
                            )
                        ]
                    )
                else:
                    loaded_settings = store.load_settings()
                    assert loaded_settings.schema_version == "2.0"
                    loaded_servers = store.load_mcp_servers()
                    assert isinstance(loaded_servers, list)
                    assert secrets.get_secret("MISSING_KEY") is None
        except BaseException as exc:
            with err_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
        assert not t.is_alive()

    assert errors == []
    verifier = SettingsStore(user_state_dir=state_dir)
    assert verifier.load_settings().secret_backend == "user_state_file"
    assert len(verifier.load_mcp_servers()) == 1
    assert acl_helper.verify_owner_only(verifier.settings_path) is True
    assert acl_helper.verify_owner_only(verifier.mcp_servers_path) is True
