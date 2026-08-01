"""Hold command path: status read + missing setScheduleHold → EX_DEVICE.

Drives shipped cmd_hold with a monkeypatched session (no network).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import lennox_s40 as m


def _zone(**kwargs):
    base = dict(
        id=0,
        name="Downstairs",
        temperature=70,
        humidity=40,
        systemMode="cool",
        hsp=65,
        csp=74,
        sp=70,
        fanMode="auto",
        humidityMode="off",
        scheduleHold=True,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _args(state: str, zone=None, first_zone: bool = False):
    return argparse.Namespace(
        state=state,
        zone=zone,
        first_zone=first_zone,
        ip=None,
        app_id=None,
        no_rediscover=True,
        lan_scan=False,
        full=False,
        cmd="hold",
    )


def test_hold_status_read_path(monkeypatch, capsys):
    """hold status: session + pick_zone + zone_dict JSON (no setScheduleHold call)."""
    z = _zone(scheduleHold=True)

    @asynccontextmanager
    async def fake_session(args):
        system = SimpleNamespace(zone_list=[z], name="Home")
        yield None, system, {}, True

    monkeypatch.setattr(m, "session", fake_session)
    rc = asyncio.run(m.cmd_hold(_args("status")))
    assert rc == m.EX_OK
    out = json.loads(capsys.readouterr().out)
    assert out["schedule_hold"] is True
    assert out["zone"]["name"] == "Downstairs"
    assert out["zone"]["active"] is True


def test_hold_on_missing_setScheduleHold_is_device(monkeypatch, capsys):
    """Zone without setScheduleHold → EX_DEVICE on hold on/off."""
    z = _zone(scheduleHold=False)  # no setScheduleHold attribute

    @asynccontextmanager
    async def fake_session(args):
        system = SimpleNamespace(zone_list=[z], name="Home")
        yield None, system, {}, True

    monkeypatch.setattr(m, "session", fake_session)
    with pytest.raises(m.CliError) as e:
        asyncio.run(m.cmd_hold(_args("on", zone="Downstairs")))
    assert e.value.code == m.EX_DEVICE
    assert "setScheduleHold" in e.value.message


def test_hold_on_calls_setScheduleHold(monkeypatch, capsys):
    """When setScheduleHold exists, hold on invokes it."""
    calls: list[bool] = []

    async def set_hold(val: bool):
        calls.append(val)

    z = _zone(scheduleHold=False)
    z.setScheduleHold = set_hold  # type: ignore[attr-defined]

    @asynccontextmanager
    async def fake_session(args):
        system = SimpleNamespace(zone_list=[z], name="Home")
        yield SimpleNamespace(), system, {}, True

    async def no_pump(api, n):
        return None

    monkeypatch.setattr(m, "session", fake_session)
    monkeypatch.setattr(m, "_pump_n", no_pump)
    rc = asyncio.run(m.cmd_hold(_args("on", zone="Downstairs")))
    assert rc == m.EX_OK
    assert calls == [True]
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
