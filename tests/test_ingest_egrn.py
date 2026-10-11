"""ingest КПТ: порядок осей, словари видов, персональные данные, участки без границ, система координат."""

import xml.etree.ElementTree as ET

import pytest

from cm_carto import gpkg
from test_ingest_support import SAMPLES, ingest, origin, project_in

OWNER = "Иванов Иван Иванович"


def kpt(project, parcels="", zones="", coord_system=None) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<extract_cadastral_plan_territory>
  <details_statement><group_top_requisites>
    <organ_registr_rights>тестовый образец</organ_registr_rights>
    <date_formation>2026-10-11</date_formation>
    <registration_number>T-1</registration_number>
  </group_top_requisites></details_statement>
  <coord_system>{coord_system or project.crs_name}</coord_system>
  <cadastral_blocks><cadastral_block><record_data><base_data><land_records>{parcels}</land_records></base_data></record_data></cadastral_block></cadastral_blocks>
  <zones_and_borders>{zones}</zones_and_borders>
</extract_cadastral_plan_territory>"""


def ring(east, north, size=100.0, swap=False):
    """Кольцо участка в ординатах Росреестра: x — север, y — восток; swap — перепутанные оси."""
    pts = [(east, north), (east + size, north), (east + size, north + size), (east, north + size), (east, north)]
    return "".join(f"<ordinate><x>{(e if swap else n):.2f}</x><y>{(n if swap else e):.2f}</y></ordinate>" for e, n in pts)


def land(cad, rings="", area=10000, extra=""):
    spatial = "".join(f"<spatial_element><ordinates>{r}</ordinates></spatial_element>" for r in rings)
    location = f"<contours_location><contours><contour><entity_spatial><spatials_elements>{spatial}</spatials_elements></entity_spatial></contour></contours></contours_location>" if rings else ""
    return (f"<land_record><object><common_data><cad_number>{cad}</cad_number></common_data></object>"
            f"<params><category><type><value>Земли населенных пунктов</value></type></category>"
            f"<permitted_use><permitted_use_established><by_document>Магазины</by_document></permitted_use_established></permitted_use>"
            f"<area><value>{area}</value></area></params>{location}{extra}</land_record>")


def zone(reg, boundary, type_name, ring_xml, index=None, name="зона"):
    idx = f"<index>{index}</index>" if index else ""
    return (f"<zone_and_border><reg_numb_border>{reg}</reg_numb_border><type_boundary><value>{boundary}</value></type_boundary>"
            f"<type_zone><value>{type_name}</value></type_zone><name_by_doc>{name}</name_by_doc>{idx}"
            f"<contours_location><contours><contour><entity_spatial><spatials_elements><spatial_element><ordinates>{ring_xml}</ordinates></spatial_element></spatials_elements></entity_spatial></contour></contours></contours_location></zone_and_border>")


ZOUIT = "Зона с особыми условиями использования территории"


def run(tmp_path, build):
    project = project_in(tmp_path)
    east, north = origin(project)
    file = tmp_path / "kpt.xml"
    file.write_text(build(project, east, north), encoding="utf-8")
    out, report = ingest(project, file)
    return gpkg.read(out), report, (east, north)


def test_axes_x_is_north_y_is_east(tmp_path):
    frames, _, (east, north) = run(tmp_path, lambda p, e, n: kpt(p, land("00:00:0000000:1", [ring(e, n)])))
    parcel = frames["parcels"].iloc[0]
    assert parcel.object_id == "parcels:00-00-0000000-1" and parcel.cadastral_number == "00:00:0000000:1"
    assert parcel.geometry.bounds == pytest.approx((east, north, east + 100, north + 100), abs=0.01)
    assert parcel.status == "cadastral"


def test_swapped_axes_fail_loudly(tmp_path):
    with pytest.raises(ValueError, match="вне охвата проекта"):
        run(tmp_path, lambda p, e, n: kpt(p, land("00:00:0000000:1", [ring(e, n, swap=True)])))


def test_coordinate_system_must_match_project(tmp_path):
    with pytest.raises(ValueError, match="система координат выписки EPSG:32638"):
        run(tmp_path, lambda p, e, n: kpt(p, land("00:00:0000000:1", [ring(e, n)]), coord_system="EPSG:32638"))


def test_owner_data_never_reaches_output(tmp_path):
    rights = (f"<right_records><right_record><right_holders><right_holder><individual><surname>{OWNER}</surname>"
              f"</individual></right_holder></right_holders></right_record></right_records>")
    frames, report, _ = run(tmp_path, lambda p, e, n: kpt(p, land("00:00:0000000:1", [ring(e, n)], extra=rights)))
    text = " ".join(str(v) for frame in frames.values() for col in frame.columns if col != "geometry" for v in frame[col])
    assert OWNER not in text and "Иванов" not in text and "Иванов" not in report.render()
    assert {c for c in frames["parcels"].columns} <= set(gpkg.FIELDS) | set(gpkg.layers()["parcels"][1]) | {"geometry"}


def test_sample_has_no_personal_data():
    tags = {el.tag for el in ET.parse(SAMPLES / "kpt" / "kpt-sample.xml").getroot().iter()}
    assert not tags & {"right_records", "right_holder", "right_holders", "individual", "surname", "first_name", "passport"}


def test_parcel_without_boundary_goes_to_report(tmp_path):
    frames, report, _ = run(tmp_path, lambda p, e, n: kpt(p, land("00:00:0000000:1", [ring(e, n)]) + land("00:00:0000000:2")))
    assert frames["parcels"].object_id.tolist() == ["parcels:00-00-0000000-1"]
    assert report.unrecognized == ["участок 00:00:0000000:2: границы в выписке не установлены"]


def test_hole_and_area_check(tmp_path):
    def build(p, e, n):
        return kpt(p, land("00:00:0000000:1", [ring(e, n, 100), ring(e + 40, n + 40, 20)], area=9600)
                   + land("00:00:0000000:2", [ring(e + 200, n, 100)], area=5000))
    frames, report, _ = run(tmp_path, build)
    parcels = frames["parcels"].set_index("cadastral_number")
    assert parcels.loc["00:00:0000000:1"].geometry.area == pytest.approx(9600)          # внутреннее кольцо — дыра
    assert any("00:00:0000000:2: площадь по выписке 5000 м2, по контуру 10000 м2" in line for line in report.confirm)
    assert not any("0000000:1:" in line for line in report.confirm)


def test_zouit_and_zone_dictionaries(tmp_path):
    def build(p, e, n):
        zones = (zone("00:00-6.1", ZOUIT, "Санитарно-защитная зона", ring(e, n))
                 + zone("00:00-6.2", ZOUIT, "Зона охраны объекта культурного наследия", ring(e + 200, n))
                 + zone("00:00-6.3", ZOUIT, "Зона затопления", ring(e + 400, n))
                 + zone("00:00-7.1", "Территориальная зона", "Зона застройки", ring(e + 600, n), index="ОЖ-2")
                 + zone("00:00-7.2", "Территориальная зона", "Производственная зона", ring(e + 800, n))
                 + zone("00:00-7.3", "Территориальная зона", "Зона лесов", ring(e + 1000, n), index="Л-1")
                 + zone("00:00-9.1", "Береговая линия", "Прочее", ring(e + 1200, n)))
        return kpt(p, zones=zones)
    frames, report, _ = run(tmp_path, build)
    zouit = frames["zouit"].set_index("registry_number")
    assert zouit.loc["00:00-6.1", "zouit_type"] == "sanitary"
    assert zouit.loc["00:00-6.2", "zouit_type"] == "heritage_protection"
    assert zouit.loc["00:00-6.3", "zouit_type"] == "other"
    assert any("00:00-6.3" in line and "не в словаре" in line for line in report.confirm)
    pzz = frames["zones_pzz"].set_index("object_id")
    assert pzz.loc["zones_pzz:00-00-7.1", "zone"] == "ОЖ" and pzz.loc["zones_pzz:00-00-7.1", "code"] == "ОЖ-2"
    assert pzz.loc["zones_pzz:00-00-7.2", "zone"] == "П"             # по названию, индекса нет
    assert "zones_pzz:00-00-7.3" not in pzz.index
    assert any("00-7.3" in line or "00:00-7.3" in line for line in report.unrecognized)
    assert any("00:00-9.1" in line and "тип границы" in line for line in report.unrecognized)


def test_sample_kpt(tmp_path):
    project = project_in(tmp_path)
    file = SAMPLES / "kpt" / "kpt-sample.xml"
    out, report = ingest(project, file)
    first = out.read_bytes()
    out, _ = ingest(project, file)
    assert out.read_bytes() == first
    assert gpkg.check(out) == []
    frames = gpkg.read(out)
    assert {k: len(v) for k, v in frames.items()} == {"parcels": 16, "zouit": 3, "zones_pzz": 3}
    assert set(frames["zouit"].zouit_type) == {"sanitary", "utility_protection", "heritage_protection"}
    assert set(frames["zones_pzz"].zone) == {"Р", "ОД", "Ж"}
    for frame in frames.values():
        assert set(frame.status) == {"cadastral"} and frame.source_label.str.contains("синтетический образец").all()
    assert report.unrecognized == ["участок 00:00:0000000:17: границы в выписке не установлены"]
    assert report.confirm == [] and report.fixed == []
