"""CLI entry-point exit taxonomy against shipped main()."""
from __future__ import annotations

import lennox_s40 as m


def test_version_exit_ok():
    assert m.main(["version"]) == m.EX_OK


def test_flag_version_exit_ok():
    assert m.main(["--version"]) == m.EX_OK


def test_argparse_usage_is_bad_req_not_deps():
    # invalid choice must not look like missing lennoxs30api (EX_DEPS=2)
    # (maps argparse SystemExit 2 → EX_BAD_REQ; not an unreachable validator test)
    rc = m.main(["away", "onn"])
    assert rc == m.EX_BAD_REQ


def test_config_path_no_deps(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    rc = m.main(["config", "path"])
    assert rc == m.EX_OK
    assert str(tmp_path / "c.json") in capsys.readouterr().out


def test_dead_ip_no_rediscover(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.setenv("LENNOX_NO_LAN_SCAN", "1")
    monkeypatch.delenv("LENNOX_IP", raising=False)
    rc = m.main(["--ip", "203.0.113.1", "--no-rediscover", "status"])
    assert rc == m.EX_NOT_FOUND
    err = capsys.readouterr().err
    assert "Connect failed for 203.0.113.1" in err


def test_missing_cmd_is_bad_req():
    rc = m.main([])
    assert rc == m.EX_BAD_REQ
