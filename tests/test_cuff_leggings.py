from pathlib import Path

import pytest

from sewing_optimiser.nest import nest
from sewing_optimiser.pdf_import import extract_pieces

PDF = Path(__file__).parent.parent / "examples/Cuff leggings/bt12-BW-projector-pattern.pdf"


@pytest.mark.skipif(not PDF.exists(), reason="example patterns are not in the repository")
def test_cuff_leggings_2_3t():
    pieces = {p.name: p for p in extract_pieces(PDF, "2-3t")}
    assert {n: p.copies for n, p in pieces.items()} == {"RIGHT LEG": 1, "LEFT LEG": 1, "ANKLE CUFF": 2}
    assert all(abs(p.grain_deg) == 90 for p in pieces.values())

    placements, length, _ = nest(list(pieces.values()), width=900)
    assert len(placements) == 4
    for i, a in enumerate(placements):
        assert 0 <= a.outline.bounds[0] and a.outline.bounds[2] <= 900
        for b in placements[i + 1:]:
            assert a.outline.distance(b.outline) >= 3 - 1e-6
