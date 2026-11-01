"""The broker rewriter covers Crew-owned crewmate specs as extra sources.

Crewmate specs live under ``~/.kiro/mates/agents``, outside the user-level
``~/.kiro/agents`` the pass historically walked. A member session runs one of
those specs, so a managed server it declares must get the same host-side stub
as a user-level spec; otherwise it launches raw inside the agent sandbox and
dies on the credential/policy mask.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from kiro_crew.mcp_gateway.rewriter import _WRAPPER_MARKER, rewrite_agents


def _write_spec(path: Path, name: str, servers: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"name": name, "mcpServers": servers}),
        encoding="utf-8",
    )


def _rewrite(
    tmp_path: Path,
    *,
    extra_source_dirs: tuple[Path, ...],
    stubs: frozenset[str],
) -> tuple[dict[str, int], dict[str, str]]:
    return rewrite_agents(
        source_dir=tmp_path / "agents",
        extra_source_dirs=extra_source_dirs,
        overlay_dir=tmp_path / "overlay" / "agents",
        socket_path=tmp_path / "gw.sock",
        work_dir=tmp_path / "wd",
        sandbox_mode="auto",
        approval_mode="interactive",
        stub_servers=stubs,
        pooling_enabled=False,
    )


def _overlay(tmp_path: Path, name: str) -> dict:
    return json.loads((tmp_path / "overlay" / "agents" / name).read_text(encoding="utf-8"))


def test_extra_source_spec_is_rewritten_and_stubbed(tmp_path: Path) -> None:
    extra = tmp_path / "mates" / "agents"
    _write_spec(
        extra / "KiroCrew-Advisor.json",
        "KiroCrew-Advisor",
        {"advisor-srv": {"command": sys.executable, "args": ["mcp-advisor"]}},
    )

    _results, target_env = _rewrite(
        tmp_path, extra_source_dirs=(extra,), stubs=frozenset({"advisor-srv"})
    )

    entry = _overlay(tmp_path, "KiroCrew-Advisor.json")["mcpServers"]["advisor-srv"]
    assert entry[_WRAPPER_MARKER] is True
    assert "kiro_crew.mcp_gateway.stub" in " ".join(entry["args"])
    assert any("ADVISOR_SRV" in key for key in target_env)


def test_extra_source_shadows_a_primary_spec_with_the_same_name(tmp_path: Path) -> None:
    primary = tmp_path / "agents"
    extra = tmp_path / "mates" / "agents"
    _write_spec(
        primary / "same-name.json",
        "same-name",
        {"primary-only": {"command": sys.executable, "args": ["primary"]}},
    )
    _write_spec(
        extra / "same-name.json",
        "same-name",
        {"extra-only": {"command": sys.executable, "args": ["extra"]}},
    )

    _rewrite(
        tmp_path,
        extra_source_dirs=(extra,),
        stubs=frozenset({"primary-only", "extra-only"}),
    )

    servers = _overlay(tmp_path, "same-name.json")["mcpServers"]
    assert "extra-only" in servers
    assert "primary-only" not in servers


def test_extra_source_change_invalidates_the_fingerprint(tmp_path: Path) -> None:
    extra = tmp_path / "mates" / "agents"
    spec = extra / "KiroCrew-Advisor.json"
    _write_spec(spec, "KiroCrew-Advisor", {"first": {"command": sys.executable}})
    _rewrite(tmp_path, extra_source_dirs=(extra,), stubs=frozenset({"first", "second"}))
    assert "first" in _overlay(tmp_path, "KiroCrew-Advisor.json")["mcpServers"]

    _write_spec(spec, "KiroCrew-Advisor", {"second": {"command": sys.executable}})
    _rewrite(tmp_path, extra_source_dirs=(extra,), stubs=frozenset({"first", "second"}))

    servers = _overlay(tmp_path, "KiroCrew-Advisor.json")["mcpServers"]
    assert "second" in servers
    assert "first" not in servers


def test_removing_an_extra_source_prunes_its_overlay(tmp_path: Path) -> None:
    extra = tmp_path / "mates" / "agents"
    spec = extra / "KiroCrew-Advisor.json"
    _write_spec(spec, "KiroCrew-Advisor", {"srv": {"command": sys.executable}})
    _rewrite(tmp_path, extra_source_dirs=(extra,), stubs=frozenset({"srv"}))
    assert (tmp_path / "overlay" / "agents" / "KiroCrew-Advisor.json").is_file()

    spec.unlink()
    _rewrite(tmp_path, extra_source_dirs=(extra,), stubs=frozenset({"srv"}))

    assert not (tmp_path / "overlay" / "agents" / "KiroCrew-Advisor.json").exists()


def test_rewrite_kwargs_includes_the_mate_agents_directory(tmp_path: Path, monkeypatch) -> None:
    from kiro_crew.config import paths as paths_mod
    from kiro_crew.mcp_gateway.launch_resolve import rewrite_kwargs

    mates = tmp_path / "mates" / "agents"
    monkeypatch.setattr(paths_mod, "mate_agents_dir", lambda: mates)
    cfg = SimpleNamespace(
        mcp_gateway=SimpleNamespace(overlay_dir="", socket_path="", enabled=False),
        agent=SimpleNamespace(sandbox="auto", approval_mode="interactive"),
    )

    kwargs = rewrite_kwargs(cfg, frozenset({"srv"}))

    assert kwargs["extra_source_dirs"] == (mates,)
