"""discover --probe-only must not write config (zero FS write)."""
from __future__ import annotations

from pathlib import Path

import lennox_s40 as m


def test_probe_only_fresh_config_no_file(tmp_path, monkeypatch):
    cfg_path = tmp_path / "c.json"
    monkeypatch.setenv("LENNOX_CONFIG", str(cfg_path))
    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    monkeypatch.delenv("LENNOX_IP", raising=False)
    monkeypatch.setattr(m, "discover_candidates", lambda **k: [("lab", "203.0.113.10")])
    monkeypatch.setattr(m, "probe_connect", lambda ip, app_id: True)
    monkeypatch.setattr(m, "_scan_lan_lennox", lambda app_id: [])

    rc = m.main(["discover", "--probe-only"])
    assert rc == m.EX_OK
    assert not cfg_path.exists()
    assert not Path(str(cfg_path) + ".lock").exists()


def test_probe_only_existing_config_unchanged(tmp_path, monkeypatch):
    cfg_path = tmp_path / "c.json"
    payload = (
        b'{\n  "app_id": "mappCONFIG000000000000001",\n'
        b'  "ip": "203.0.113.9",\n  "version": 1\n}\n'
    )
    cfg_path.write_bytes(payload)
    monkeypatch.setenv("LENNOX_CONFIG", str(cfg_path))
    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    monkeypatch.setattr(m, "discover_candidates", lambda **k: [("lab", "203.0.113.10")])
    monkeypatch.setattr(m, "probe_connect", lambda ip, app_id: True)

    rc = m.main(["discover", "--probe-only"])
    assert rc == m.EX_OK
    assert cfg_path.read_bytes() == payload


def test_effective_app_id_persist_false_no_write(tmp_path, monkeypatch):
    cfg_path = tmp_path / "c.json"
    monkeypatch.setenv("LENNOX_CONFIG", str(cfg_path))
    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    import argparse

    args = argparse.Namespace(app_id=None)
    cfg = m.load_config()
    new_id = m.effective_app_id(args, cfg, persist=False)
    assert new_id.startswith("mapp")
    assert not cfg_path.exists()
