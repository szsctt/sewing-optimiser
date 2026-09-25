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


XL = NAPPY / "Nappy cover/XLarge-XXXLpatterns.pdf"


@pytest.mark.skipif(not XL.exists(), reason="example patterns are not in the repository")
def test_xl_soaker_is_three_pieces():
    from sewing_optimiser import project

    size = next(v for v, label in list_sizes(XL) if label.startswith("XLarge"))
    found = project.pieces(project.default(str(XL), size))
    assert [(p.name.split(" (")[0], p.copies) for p in found] == [
        ("Front of the soaker + Back of the soaker", 1), ("Waistband", 1), ("Leg Cuffs", 2)]
    assert found[0].half is not None  # the body is cut on the fold
    assert found[1].outline.bounds[2] == pytest.approx(19.5 * 25.4)  # 19.5" by 4" for the XL


SLEEPER = NAPPY / "Sleep onesie/LK Snap Sleeper Pattern Size A0.pdf"


@pytest.mark.skipif(not SLEEPER.exists(), reason="example patterns are not in the repository")
def test_option_part_left_off():
    from sewing_optimiser.edit import trim

    back = next(p for p in extract_pieces(SLEEPER, "2T") if p.name == "Back")
    assert len(back.regions) >= 2  # the leg below the cuff line is a separate part
    smallest = min(range(len(back.regions)), key=lambda i: back.regions[i].area)
    shorter = trim(back, [smallest])
    assert shorter.half is not None and shorter.outline.area < back.outline.area
