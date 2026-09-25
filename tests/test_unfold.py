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
