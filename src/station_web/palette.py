"""Station colours for the series the API colours itself (phase, ionogram).

A copy of ``PALETTE.station`` in static/js/common.js, the one palette the
pages draw with; tests/test_stage2_frontend.py holds the two equal.  A
SHARED channel or a series from several stations takes ``COMPOSITE``.
"""

STATION_COLORS = {
    "WWV": "#56B4E9",
    "WWVH": "#E69F00",
    "BPM": "#CC79A7",
}
COMPOSITE = "#A0A7B4"
