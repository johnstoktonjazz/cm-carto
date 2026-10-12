"""Отказ fetch без сети: понятная ошибка с источником, хостом и путем кеша; запрос подменен ошибкой того типа, что бросает библиотека."""

import pytest
import requests
from fetch_fixtures import RELEASE, project_file

from cm_carto import cli, project
from cm_carto.fetch import osm, overture


def test_osm_without_network(tmp_path, monkeypatch):
    proj = project.load(project_file(tmp_path))

    def refuse(*args, **kwargs):
        raise requests.exceptions.ConnectionError("HTTPSConnectionPool(host='overpass-api.de', port=443):\nMax retries exceeded")

    monkeypatch.setattr(requests, "post", refuse)
    with pytest.raises(ValueError) as caught:
        osm.download(proj)
    message = str(caught.value)
    assert message.startswith("нет доступа к overpass-api.de: fetch osm требует сети или кеша ")
    assert str(osm.cache_dir(proj)) in message and "Max retries exceeded" in message and "\n" not in message
    assert isinstance(caught.value.__cause__, requests.exceptions.ConnectionError)
    assert not (osm.cache_dir(proj) / "SOURCE.yaml").exists()


def test_osm_server_refusal(tmp_path, monkeypatch):
    proj = project.load(project_file(tmp_path))
    answer = requests.Response()
    answer.status_code = 504
    answer.url = "https://overpass-api.de/api/interpreter"
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: answer)
    with pytest.raises(ValueError, match="нет доступа к overpass-api.de.*504"):
        osm.download(proj)


def test_overture_without_network(tmp_path, monkeypatch):
    proj = project.load(project_file(tmp_path, fetch={"overture_release": RELEASE}))

    def refuse(*args, **kwargs):  # так pyarrow отдает отказ S3
        raise OSError("When getting information for key 'release/x' in bucket 'overturemaps-us-west-2': "
                      "AWS Error NETWORK_CONNECTION during HeadObject operation")

    monkeypatch.setattr(overture, "_select", refuse)
    with pytest.raises(ValueError) as caught:
        overture.download(proj, RELEASE)
    message = str(caught.value)
    assert message.startswith("нет доступа к overturemaps-us-west-2 (S3): fetch overture требует сети или кеша ")
    assert str(overture.cache_dir(proj, RELEASE)) in message and "NETWORK_CONNECTION" in message
    assert isinstance(caught.value.__cause__, OSError)


def test_cli_prints_offline_error_in_one_line(tmp_path, monkeypatch, capsys):
    path = project_file(tmp_path)
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError("нет сети")))
    with pytest.raises(SystemExit) as exit_:
        cli.main(["fetch", str(path), "osm"])
    assert exit_.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("ошибка: нет доступа к overpass-api.de: fetch osm") and "Traceback" not in err
    assert len(err.strip().splitlines()) == 1
