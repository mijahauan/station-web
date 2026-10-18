"""The Test signal page: no GNSS thresholds, no producer floors drawn as data.

- S4 here carries no noise correction, and the 0.3/0.6 lines come from
  L-band GNSS practice, not from HF.  The phase views dropped such
  thresholds in S2C; this page drops them too and labels S4
  "(uncorrected for noise)".
- hf-timestd's WWVTestSignalDetector clamps two of its estimates
  (core/wwv_test_signal.py): the delay spread is the chirp's -3 dB width
  less its 0.2 ms ideal width, floored at 0.0; the coherence time is
  clipped to [0.1, 30] s and set to 10.0 s, a sentinel, when the fading
  variance is too small to model.  Those values read as words, never as
  plotted measurements.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static" / "test_signal.html"


def _text():
    return PAGE.read_text()


def test_no_gnss_s4_thresholds():
    t = _text()
    assert not re.search(r"y0:\s*0\.[36]\b", t)
    assert "s4Color" not in t


def test_s4_labelled_uncorrected():
    t = _text()
    assert "S4 Index'" not in t and 'S4 Index"' not in t
    assert t.count("uncorrected for noise") >= 4


def test_charts_plot_only_measured_values():
    t = _text()
    # both trace builders pass every value through the floor filter
    assert t.count("measuredOrNull(field, m[field])") >= 2
    assert "fmt(r.delay_spread_ms" not in t and "fmt(r.coherence_time_sec" not in t


def _floor_js():
    t = _text()
    m = re.search(r"// -- producer floors --(.*?)// -- end producer floors --", t, re.S)
    assert m, "floor helpers not marked in test_signal.html"
    return m.group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_floor_values_read_as_words():
    expr = ("[floorNote('delay_spread_ms', 0), floorNote('delay_spread_ms', 0.35),"
            " floorNote('coherence_time_sec', 0.1), floorNote('coherence_time_sec', 10),"
            " floorNote('coherence_time_sec', 30), floorNote('coherence_time_sec', 2.5),"
            " measuredOrNull('coherence_time_sec', 0.1), measuredOrNull('delay_spread_ms', 0),"
            " measuredOrNull('coherence_time_sec', 2.5), measuredOrNull('snr_db', 0),"
            " measuredOrNull('delay_spread_ms', null)]")
    r = subprocess.run(["node", "-e", _floor_js() + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "below resolution" in out[0]
    assert out[1] is None
    assert "below resolution" in out[2]
    assert "not measured" in out[3]
    assert "ceiling" in out[4]
    assert out[5] is None
    assert out[6:] == [None, None, 2.5, 0, None]
