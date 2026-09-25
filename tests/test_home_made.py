"""Home-made patterns: sizes by line style, parts taped together, pieces given by size."""
from pathlib import Path

import pymupdf
import pytest

from sewing_optimiser.layout import _align, _lengthened
from sewing_optimiser.pdf_import import extract_pieces, list_sizes, sheets

NAPPY = Path(__file__).parent.parent / "examples"
SOAKER = NAPPY / "Nappy cover/Soaker Patterns for fleece.pdf"
LONGIES = NAPPY / "Nappy cover - longies/Large Longies.pdf"


@pytest.mark.skipif(not SOAKER.exists(), reason="example patterns are not in the repository")
def test_soaker_sizes_by_line_style():
    labels = [label.split(" ")[0] for _, label in list_sizes(SOAKER)]
    assert {"Preemie", "NB", "Small", "Medium", "Large", "XLarge"} <= set(labels)
    names = [p.name for p in extract_pieces(SOAKER, "style:#993300 8 6")]
    assert any(n.startswith("Front of the soaker") for n in names)


@pytest.mark.skipif(not LONGIES.exists(), reason="example patterns are not in the repository")
def test_longies_parts_are_taped_together_and_lengthened():
    assert len(sheets(pymupdf.open(LONGIES))) == 1
    [piece] = extract_pieces(LONGIES)
    height = lambda p: (lambda b: b[3] - b[1])(_align(p.outline, p.grain_deg).bounds)
    piece.lengthen, piece.lengthen_at = 100, 200
    assert height(_lengthened(piece)) == pytest.approx(height(piece) + 100, abs=0.5)
