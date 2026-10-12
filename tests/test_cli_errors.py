"""Ошибки проверок в командной строке: одна строка «ошибка: …» в stderr и код 1, без трассировки; ошибки кода — как есть."""

import pytest
from analysis_fixtures import STOP, lattice, project_in, stop, write_world

from cm_carto import cli
from cm_carto.analysis import isochrone


def test_check_error_is_one_line(tmp_path, capsys):
    project = project_in(tmp_path)
    write_world(project, lattice(range(11), range(11)), [stop("osm-n1", 5, 5)])
    isochrone.save(project, isochrone.rows(project, STOP, "walk", [3]))
    with pytest.raises(SystemExit) as exit_:
        cli.main(["measure", str(tmp_path / "project.yaml"), "isochrones:нет-такого"])
    assert exit_.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ошибка: ") and "isochrones:нет-такого" in captured.err and "Traceback" not in captured.err
    assert len(captured.err.strip().splitlines()) == 1


def test_missing_file_is_one_line(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["measure", str(tmp_path / "нет.yaml"), "x"])
    assert exit_.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("ошибка: ") and "Traceback" not in err and len(err.strip().splitlines()) == 1


def test_code_error_keeps_traceback(monkeypatch):
    def broken(args):
        raise KeyError("ошибка кода")

    monkeypatch.setattr(cli, "doctor", broken)
    with pytest.raises(KeyError):
        cli.main(["doctor"])
