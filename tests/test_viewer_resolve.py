"""resolve(): a viewer name to a localhost port, from the viewers snapshot."""
import pytest

from station_web.services.viewer_proxy import Refusal, resolve


def test_resolve_known_viewer(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web", "port": 8081, "active": "active"}])
    t = resolve("ka9q-web", tmp_settings)
    assert (t.name, t.port, t.kind) == ("ka9q-web", 8081, "ka9q-web")


def test_resolve_second_ka9q_web_kind(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web-2", "port": 8083, "active": "active"}])
    assert resolve("ka9q-web-2", tmp_settings).kind == "ka9q-web"


def test_resolve_gmag_kind(tmp_settings, write_viewers):
    write_viewers([{"name": "gmag-webui", "port": 8082, "active": "active"}])
    assert resolve("gmag-webui", tmp_settings).kind == "gmag"


def test_resolve_other_kind(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-webby", "port": 8090}])
    assert resolve("ka9q-webby", tmp_settings).kind == "other"


def test_unknown_name_is_404(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web", "port": 8081}])
    with pytest.raises(Refusal) as e:
        resolve("nope", tmp_settings)
    assert e.value.status == 404 and e.value.message == "no such viewer on this station"


def test_shared_port_is_409_naming_both(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web-2", "port": 8082}, {"name": "gmag-webui", "port": 8082}],
                  conflicts=[{"port": 8082, "names": ["ka9q-web-2", "gmag-webui"]}])
    with pytest.raises(Refusal) as e:
        resolve("gmag-webui", tmp_settings)
    assert e.value.status == 409
    assert e.value.message == "port 8082 is claimed by ka9q-web-2 and gmag-webui"


def test_missing_snapshot_is_503(tmp_settings):
    with pytest.raises(Refusal) as e:
        resolve("ka9q-web", tmp_settings)
    assert e.value.status == 503 and e.value.message == "viewer list unavailable"


@pytest.mark.parametrize("port", [0, -1, 65536, "8081", None, True])
def test_bad_port_is_502(tmp_settings, write_viewers, port):
    write_viewers([{"name": "ka9q-web", "port": port}])
    with pytest.raises(Refusal) as e:
        resolve("ka9q-web", tmp_settings)
    assert e.value.status == 502 and e.value.message == "viewer has no usable port"


def _age(tmp_settings):
    import json
    p = tmp_settings.snapshot_dir / "viewers.json"
    env = json.loads(p.read_text())
    env["ran_at"] = "2020-01-01T00:00:00Z"
    p.write_text(json.dumps(env))


def test_stale_snapshot_still_resolves(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web", "port": 8081}])
    _age(tmp_settings)
    assert resolve("ka9q-web", tmp_settings).port == 8081


def test_unknown_name_on_stale_snapshot_is_404(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web", "port": 8081}])
    _age(tmp_settings)
    with pytest.raises(Refusal) as e:
        resolve("nope", tmp_settings)
    assert e.value.status == 404


def test_malformed_data_is_503(tmp_settings):
    import json
    (tmp_settings.snapshot_dir / "viewers.json").write_text(json.dumps(
        {"ran_at": "2020-01-01T00:00:00Z", "data": None}))
    with pytest.raises(Refusal) as e:
        resolve("ka9q-web", tmp_settings)
    assert e.value.status == 503
