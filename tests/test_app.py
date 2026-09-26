"""The web app's API, as the browser page uses it."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sewing_optimiser import project, server

EXAMPLES = Path(__file__).parent.parent / "examples"
CUFF = "examples/Cuff leggings/bt12-BW-projector-pattern.pdf"
XL = "examples/Nappy cover/XLarge-XXXLpatterns.pdf"
POCKET = "examples/Pocket Skirt/A0-Pattern_PeppermintxPaper-Theory_Pocket-Skirt-A0.pdf"
pytestmark = pytest.mark.skipif(not EXAMPLES.exists(), reason="example patterns are not in the repository")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "PROJECTS", tmp_path)  # keep saved choices out of projects/
    return TestClient(server.app)


def load(client, pdf, size):
    p = client.get("/api/project", params={"pdf": pdf, "size": size}).json()
    answer = client.post("/api/pieces", json=p).json()
    p["joins"] = answer["joins"]
    return p, answer["pieces"]


def test_pages_load(client):
    assert client.get("/").status_code == 200
    assert client.get("/projector").status_code == 200
    assert CUFF in client.get("/api/files").json()


def test_sizes_differ_between_files(client):
    cuff = client.get("/api/info", params={"pdf": CUFF}).json()
    xl = client.get("/api/info", params={"pdf": XL}).json()
    assert "2-3t" in [v for v, _ in cuff["sizes"]]
    assert {l.split(" ")[0] for _, l in xl["sizes"]} == {"XLarge", "XXLarge", "XXXLarge"}


def test_review_and_lay_out(client):
    p, pieces = load(client, CUFF, "2-3t")
    assert sorted(x["name"] for x in pieces) == ["ANKLE CUFF", "LEFT LEG", "RIGHT LEG"]
    p["pieces"] = [{"include": x["name"] != "ANKLE CUFF"} for x in pieces]
    p["fabric"]["width"] = 900
    [layout] = client.post("/api/layout", json=p).json()
    assert layout["svg"].startswith("<svg") and 540 < layout["length"] < 600
    assert client.get("/api/layout.pdf").content[:4] == b"%PDF"
    assert client.get("/api/layout.svg", params={"i": 0}).status_code == 200


def test_choices_are_saved(client):
    p, pieces = load(client, CUFF, "2-3t")
    p["pieces"] = [{"name": "Leg A"}] + [{} for _ in pieces[1:]]
    client.post("/api/layout", json=p)
    again, pieces = load(client, CUFF, "2-3t")
    assert pieces[0]["name"] == "Leg A"


def test_soaker_front_and_back_join(client):
    size = next(v for v, l in client.get("/api/info", params={"pdf": XL}).json()["sizes"] if l.startswith("XLarge"))
    p, pieces = load(client, XL, size)
    assert [x["name"].split(" (")[0] for x in pieces] == ["Front of the soaker + Back of the soaker", "Waistband", "Leg Cuffs"]
    p["joins"] = []  # undo the guessed join
    p["pieces"] = []
    assert len(client.post("/api/pieces", json=p).json()["pieces"]) == 4


def test_picking_nested_sizes(client):
    regions = client.get("/api/faces", params={"pdf": POCKET, "page": 0}).json()
    p, _ = load(client, POCKET, "")
    # the two largest regions are the smallest size's panel bodies; clicking one gives that piece alone
    p["picked"] = {"0": [regions[0]["at"]]}
    [one] = client.post("/api/pieces", json=p).json()["pieces"]
    # adding the strips that touch it grows the piece
    p["picked"] = {"0": [r["at"] for r in regions[:12]]}
    grown = client.post("/api/pieces", json=p).json()["pieces"]
    assert max(x["w"] * x["h"] for x in grown) > one["w"] * one["h"]


def test_pocket_skirt_a4_pieces_and_notches():
    from sewing_optimiser.pdf_import import extract_pieces

    pieces = extract_pieces(EXAMPLES / "Pocket Skirt/A4-Pattern_PeppermintxPaper-Theory_Pocket-Skirt.pdf", "Size 12")
    assert {p.name: p.copies for p in pieces} == {
        "Centre Front Panel": 1, "Centre Back Panel": 1, "Lower Side Panel": 2, "Upper Side Panel": 2}
    assert all(len(p.marks.geoms) >= 6 for p in pieces)  # notches, as ticks into the piece


def test_combined_layout(client):
    cuff, _ = load(client, CUFF, "2-3t")
    client.post("/api/combined/add", json=cuff)
    ringer, _ = load(client, "examples/Ringer tee/bt99-A0-pattern.pdf", "2-3t")
    entries = client.post("/api/combined/add", json=ringer).json()
    assert len(entries) == 2
    [layout] = client.post("/api/combined/layout", json=cuff["fabric"]).json()
    assert "Cuff leggings 2-3t: LEFT LEG" in layout["svg"] and "Ringer tee 2-3t: BACK" in layout["svg"]
    assert len(client.post("/api/combined/remove", params={"i": 0}).json()) == 1


def test_overlay_shows_pieces_on_their_sheet(client):
    p, pieces = load(client, CUFF, "2-3t")
    drawn = client.post("/api/overlay", json=p).json()
    assert sorted(o["name"] for o in drawn) == sorted(x["name"] for x in pieces)
    assert all(o["sheet"] == 0 and o["d"].startswith("M") for o in drawn)
