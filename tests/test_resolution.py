"""Unit tests: resolve helpers (no network)."""
from __future__ import annotations

import argparse

import pytest

import lennox_s40 as m

APP = "mappTEST000000000000000001"


def test_is_ip():
    assert m._is_ip("192.168.1.1")
    assert not m._is_ip("192.168.1")
    assert not m._is_ip("host.local")


def test_env_int_bad(monkeypatch):
    monkeypatch.setenv("LENNOX_TIMEOUT", "abc")
    assert m._env_int("LENNOX_TIMEOUT", 90) == 90


def test_generate_app_id_unique():
    a = m.generate_app_id()
    b = m.generate_app_id()
    assert a.startswith("mapp")
    assert a != b
    assert len(a) == 4 + 24


def test_effective_app_id_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    m.save_config({"app_id": "mappCONFIG000000000000001"})
    cfg = m.load_config()
    args = argparse.Namespace(app_id="mappCLI000000000000000001")
    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    assert m.effective_app_id(args, cfg) == "mappCLI000000000000000001"

    args = argparse.Namespace(app_id=None)
    monkeypatch.setenv("LENNOX_APP_ID", "mappENV0000000000000000001")
    assert m.effective_app_id(args, cfg) == "mappENV0000000000000000001"

    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    assert m.effective_app_id(args, cfg) == "mappCONFIG000000000000001"


def test_migrate_legacy_app_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    m.save_config({"app_id": m.LEGACY_SHARED_APP_ID})
    cfg = m.load_config()
    args = argparse.Namespace(app_id=None)
    new_id = m.effective_app_id(args, cfg)
    assert new_id != m.LEGACY_SHARED_APP_ID
    assert m.load_config()["app_id"] == new_id


def test_mode_map():
    assert m.MODE_MAP["auto"] == "heat and cool"
    assert m.MODE_MAP["cool"] == "cool"


def test_finite_temp():
    assert m._finite_temp(72, "t") == 72
    with pytest.raises(m.CliError) as e:
        m._finite_temp(float("nan"), "t")
    assert e.value.code == m.EX_BAD_REQ
    with pytest.raises(m.CliError):
        m._finite_temp(120, "t")


def test_remember_identity_clears_host(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    m.save_config({"host": "old.local", "ip": "1.2.3.4"})
    cfg = m.load_config()
    cfg = m.remember_identity(cfg, ip="5.6.7.8", app_id="mappX", clear_host=True)
    assert "host" not in cfg or cfg.get("host") in (None, "")
    assert cfg["ip"] == "5.6.7.8"


def test_resolve_target_explicit_ip_probe_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.delenv("LENNOX_IP", raising=False)
    m.save_config({})
    monkeypatch.setattr(m, "probe_connect", lambda ip, app_id: True)
    args = argparse.Namespace(
        ip="203.0.113.10", app_id=APP, no_rediscover=True, lan_scan=False
    )
    ip, app_id, _cfg = m.resolve_target(args)
    assert ip == "203.0.113.10"
    assert app_id == APP


def test_resolve_target_dead_ip_no_rediscover(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.delenv("LENNOX_IP", raising=False)
    m.save_config({})
    monkeypatch.setattr(m, "probe_connect", lambda ip, app_id: False)
    args = argparse.Namespace(
        ip="203.0.113.1", app_id=APP, no_rediscover=True, lan_scan=False
    )
    with pytest.raises(m.CliError) as e:
        m.resolve_target(args)
    assert e.value.code == m.EX_NOT_FOUND
    assert "Connect failed for 203.0.113.1" in e.value.message


def test_resolve_target_rediscover_after_probe_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.delenv("LENNOX_IP", raising=False)
    m.save_config({"ip": "203.0.113.1", "app_id": APP})
    monkeypatch.setattr(m, "probe_connect", lambda ip, app_id: False)
    monkeypatch.setattr(
        m,
        "discover_live",
        lambda *a, **k: {"ip": "203.0.113.99", "host": ""},
    )
    args = argparse.Namespace(ip=None, app_id=APP, no_rediscover=False, lan_scan=False)
    ip, app_id, _cfg = m.resolve_target(args)
    assert ip == "203.0.113.99"
    assert app_id == APP


def test_resolve_target_no_address_no_rediscover(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.delenv("LENNOX_IP", raising=False)
    m.save_config({})
    args = argparse.Namespace(ip=None, app_id=APP, no_rediscover=True, lan_scan=False)
    with pytest.raises(m.CliError) as e:
        m.resolve_target(args)
    assert e.value.code == m.EX_NOT_FOUND


def test_discover_candidates_order(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.delenv("LENNOX_IP", raising=False)
    monkeypatch.setattr(m, "_mdns_s40_hosts", lambda: [])
    monkeypatch.setattr(m, "resolve_host", lambda h: h if m._is_ip(h) else f"resolved-{h}")
    cands = m.discover_candidates(
        prefer="203.0.113.5",
        cfg={"ip": "203.0.113.6", "host": "Lennox-S40-X.local", "serial": "SER1"},
    )
    labels = [lab for lab, _ip in cands]
    assert labels[0] == "203.0.113.5"
    assert "203.0.113.6" in labels
    assert any("SER1" in lab for lab in labels)


def test_discover_live_returns_first_ok(monkeypatch):
    monkeypatch.setattr(
        m,
        "discover_candidates",
        lambda **k: [("a", "203.0.113.1"), ("b", "203.0.113.2")],
    )
    calls = []

    def probe(ip, app_id):
        calls.append(ip)
        return ip.endswith(".2")

    monkeypatch.setattr(m, "probe_connect", probe)
    found = m.discover_live(APP, quiet=True, allow_lan_scan=False)
    assert found == {"ip": "203.0.113.2", "host": "b"}
    assert calls == ["203.0.113.1", "203.0.113.2"]


def test_discover_live_none(monkeypatch):
    monkeypatch.setattr(m, "discover_candidates", lambda **k: [("a", "203.0.113.1")])
    monkeypatch.setattr(m, "probe_connect", lambda ip, app_id: False)
    assert m.discover_live(APP, quiet=True, allow_lan_scan=False) is None


def test_discover_live_no_lan_scan_env_suppresses_scan(monkeypatch):
    """LENNOX_NO_LAN_SCAN / ENV_NO_LAN_SCAN forces allow_lan_scan off — _scan_lan never runs."""
    monkeypatch.setattr(m, "ENV_NO_LAN_SCAN", True)
    monkeypatch.setattr(m, "discover_candidates", lambda **k: [])
    monkeypatch.setattr(m, "probe_connect", lambda ip, app_id: False)
    scan_calls: list[str] = []

    def fake_scan(app_id: str):
        scan_calls.append(app_id)
        yield ("lan-scan:203.0.113.50", "203.0.113.50")

    monkeypatch.setattr(m, "_scan_lan_lennox", fake_scan)
    # Caller opts into LAN scan, but env guard must suppress it
    found = m.discover_live(APP, quiet=True, allow_lan_scan=True)
    assert found is None
    assert scan_calls == []
