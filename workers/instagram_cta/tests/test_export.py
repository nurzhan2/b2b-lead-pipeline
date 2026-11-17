import csv

from workers.instagram_cta.main import _export_csv


def test_csv_export_upserts_by_instagram_pk(tmp_path):
    path = tmp_path / "leads.csv"
    assert _export_csv(path, [{"pk": 7, "username": "old", "keyword": "старт"}]) == 1
    assert _export_csv(path, [{"pk": 7, "username": "new", "keyword": "агент"}]) == 1

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["username"] == "new"
    assert rows[0]["keyword"] == "агент"
