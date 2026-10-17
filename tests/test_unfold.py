from pathlib import Path

import pytest

from sewing_optimiser.pdf_import import extract_pieces

RINGER = Path(__file__).parent.parent / "examples/Ringer tee"


@pytest.mark.skipif(not RINGER.exists(), reason="example patterns are not in the repository")
def test_unfolded_halves_match_projector_pieces():
    """The A0 file draws half pieces on the fold; the projector file draws them whole."""
    whole = {p.name.split()[0]: p for p in extract_pieces(RINGER / "bt99-BW-projector-pattern.pdf", "2-3t")}
    for piece in extract_pieces(RINGER / "bt99-A0-pattern.pdf", "2-3t"):
        if "FRONT" in piece.name or piece.name.startswith("BACK"):
            assert piece.unfolded
            ref = whole["FRONT" if "FRONT" in piece.name else "BACK"].outline
            assert piece.outline.area == pytest.approx(ref.area, rel=0.01)


JENSEN = Path(__file__).parent.parent / "examples/Jensen Shirt sewing pattern/WBM Jensen Shirt pattern A0_2 pages.pdf"


@pytest.mark.skipif(not JENSEN.exists(), reason="example patterns are not in the repository")
def test_fold_label_on_the_edge_beats_cut_count_inside():
    """The collar's 'Fold' label is on its short top edge; 'cut 2 ... on the fold' runs down its middle."""
    collar = next(p for p in extract_pieces(JENSEN, "M") if p.name.startswith("collar"))
    (ax, ay), (bx, by) = collar.fold_edge
    assert abs(ay - by) < 1 and ay == pytest.approx(collar.half.bounds[1], abs=1)


@pytest.mark.skipif(not JENSEN.exists(), reason="example patterns are not in the repository")
def test_jensen_back_and_marks():
    """The back's outline has gaps where three paths nearly meet; buttons and the pocket corner are marks."""
    pieces = {p.name: p for p in extract_pieces(JENSEN, "M")}
    back = pieces["Back"]
    assert back.unfolded and back.half.bounds[3] - back.half.bounds[1] > 250
    assert len(pieces["Front"].marks.geoms) > 20


@pytest.mark.skipif(not JENSEN.exists(), reason="example patterns are not in the repository")
def test_chosen_fold_edge():
    """A fold edge chosen in the app replaces the guess; false leaves the piece as drawn."""
    from sewing_optimiser import project as proj
    from sewing_optimiser.edit import _edges
    p = proj.default(JENSEN, "M")
    guessed = proj.pieces(p)
    k = next(i for i, x in enumerate(guessed) if x.name.startswith("collar"))
    side = max(_edges(guessed[k].half), key=lambda e: abs(e[1][1] - e[0][1]))  # a long side, not the top
    p["pieces"] = [{} for _ in guessed]
    p["pieces"][k] = {"fold": [list(side[0]), list(side[1])]}
    chosen = proj.pieces(p)[k]
    assert chosen.fold_edge == side and chosen.outline.area == pytest.approx(guessed[k].outline.area, rel=0.05)
    p["pieces"][k] = {"fold": False}
    drawn = proj.pieces(p)[k]
    assert drawn.half is None and drawn.outline.equals(guessed[k].half)


@pytest.mark.skipif(not JENSEN.exists(), reason="example patterns are not in the repository")
def test_grain_along_fold_when_unlabelled():
    back = next(p for p in extract_pieces(JENSEN, "M") if p.name == "Back")
    assert abs((back.grain_deg + 90) % 180 - 90) < 1  # the fold is horizontal on the sheet


@pytest.mark.skipif(not JENSEN.exists(), reason="example patterns are not in the repository")
def test_slit_from_the_edge_is_a_mark():
    """The sleeve's placket slit runs 12 cm up from the hem."""
    sleeve = next(p for p in extract_pieces(JENSEN, "L") if p.name == "Sleeve")
    assert any(110 < m.length < 130 and m.distance(sleeve.outline.exterior) < 1 for m in sleeve.marks.geoms)
