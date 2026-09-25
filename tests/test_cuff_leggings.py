from pathlib import Path

import pytest

from sewing_optimiser.layout import FabricSettings, make_layouts
from sewing_optimiser.pdf_import import extract_pieces

PDF = Path(__file__).parent.parent / "examples/Cuff leggings/bt12-BW-projector-pattern.pdf"


@pytest.mark.skipif(not PDF.exists(), reason="example patterns are not in the repository")
def test_cuff_leggings_2_3t():
    pieces = extract_pieces(PDF, "2-3t")
    assert {p.name: p.copies for p in pieces} == {"RIGHT LEG": 1, "LEFT LEG": 1, "ANKLE CUFF": 2}
    assert all(abs(p.grain_deg) == 90 for p in pieces)

    [layout] = make_layouts(pieces, FabricSettings(width=900))
    assert len(layout.placements) == 4
    assert layout.length < 600  # a leg is 555 mm long along the grain
    for i, a in enumerate(layout.placements):
        assert 0 <= a.outline.bounds[0] and a.outline.bounds[2] <= 900
        for b in layout.placements[i + 1:]:
            assert a.outline.distance(b.outline) >= 3 - 1e-6


@pytest.mark.skipif(not PDF.exists(), reason="example patterns are not in the repository")
def test_aims():
    pieces = extract_pieces(PDF, "2-3t")
    by_aim = {aim: make_layouts(pieces, FabricSettings(width=1500, aim=aim))[0] for aim in ("length", "width", "compact")}
    assert by_aim["length"].length <= min(l.length for l in by_aim.values()) + 1
    assert by_aim["width"].flat_width <= min(l.flat_width for l in by_aim.values()) + 1
    area = lambda l: l.length * l.flat_width
    assert area(by_aim["compact"]) <= min(area(l) for l in by_aim.values()) + 1
