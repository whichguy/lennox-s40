"""Zone selection unit tests."""
from __future__ import annotations

from types import SimpleNamespace

import lennox_s40 as m
import pytest


def _sys(zones):
    return SimpleNamespace(zone_list=zones)


def test_pick_zone_multi_requires_name():
    z0 = SimpleNamespace(id=0, name="Downstairs", temperature=70)
    z1 = SimpleNamespace(id=1, name="Upstairs", temperature=71)
    with pytest.raises(m.CliError) as e:
        m.pick_zone(_sys([z0, z1]), None, for_write=True)
    assert e.value.code == m.EX_BAD_REQ


def test_pick_zone_first_zone():
    z0 = SimpleNamespace(id=0, name="Downstairs", temperature=70)
    z1 = SimpleNamespace(id=1, name="Upstairs", temperature=71)
    z = m.pick_zone(_sys([z0, z1]), None, first_zone=True, for_write=True)
    assert z.name == "Downstairs"


def test_pick_zone_exact():
    z0 = SimpleNamespace(id=0, name="Downstairs", temperature=70)
    z = m.pick_zone(_sys([z0]), "Downstairs", for_write=True)
    assert z is z0


def test_pick_zone_inactive_write():
    z0 = SimpleNamespace(id=0, name="Ghost", temperature=None)
    with pytest.raises(m.CliError):
        m.pick_zone(_sys([z0]), "Ghost", for_write=True)


def test_zone_dict_hold():
    z = SimpleNamespace(
        id=0,
        name="Z",
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
    d = m.zone_dict(z)
    assert d["schedule_hold"] is True
    assert d["active"] is True


def test_pick_zone_empty_is_device_error():
    with pytest.raises(m.CliError) as e:
        m.pick_zone(_sys([]), None, for_write=True)
    assert e.value.code == m.EX_DEVICE


def test_pick_zone_no_active_is_device_error():
    z0 = SimpleNamespace(id=0, name="Ghost", temperature=None)
    with pytest.raises(m.CliError) as e:
        m.pick_zone(_sys([z0]), None, for_write=True)
    assert e.value.code == m.EX_DEVICE


def test_pick_zone_substring_disabled_for_write():
    z0 = SimpleNamespace(id=0, name="Downstairs", temperature=70)
    with pytest.raises(m.CliError) as e:
        m.pick_zone(_sys([z0]), "Down", for_write=True)
    assert e.value.code == m.EX_BAD_REQ


def test_pick_zone_substring_ok_for_read():
    z0 = SimpleNamespace(id=0, name="Downstairs", temperature=70)
    z = m.pick_zone(_sys([z0]), "Down", for_write=False)
    assert z is z0
