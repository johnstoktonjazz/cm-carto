"""Генератор образцов детерминирован: повторный запуск дает те же байты, что лежат в input/."""

import importlib.util

from test_ingest_support import ROOT, SAMPLES


def test_generator_reproduces_committed_samples(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("make_samples", ROOT / "scripts" / "make_samples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OUT", tmp_path)
    module.main()
    for name in ["dxf/ppt-sample.dxf", "dxf/ppt-sample.dxf.source.yaml", "kpt/kpt-sample.xml"]:
        assert (tmp_path / name).read_bytes() == (SAMPLES / name).read_bytes(), name
