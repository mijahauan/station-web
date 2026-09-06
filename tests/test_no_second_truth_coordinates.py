#!/usr/bin/env python3
"""Station coordinates resolve through the catalogue, or they are a second truth.

WWV's position lived in eight places across three repos with five distinct
latitudes, 1.93 us of predicted delay apart.  `hamsci_dsp.stations.BUILTIN_CATALOG`
is the single source.  Anything that keeps its own copy is a fact waiting to
diverge, so compare it.

`station_web.broadcast.DEFAULT_STATIONS` already reads its coordinates from
`BUILTIN_CATALOG` at construction time (see the module's DEFAULT STATION
DEFINITIONS block) rather than carrying a second literal table, so this test
is a regression guard rather than a live discrepancy check: it fails loudly if
a future edit reintroduces a hardcoded coordinate that drifts from the
catalogue.
"""

import pytest

from hamsci_dsp.stations import BUILTIN_CATALOG


def _cat(name):
    return BUILTIN_CATALOG.get(name).coordinates


def test_default_broadcast_stations_agree_with_the_catalogue():
    from station_web.broadcast import DEFAULT_STATIONS

    for s in DEFAULT_STATIONS:
        want = _cat(s.name)
        assert (s.latitude, s.longitude) == want, (
            f"{s.name} disagrees with the catalogue")
