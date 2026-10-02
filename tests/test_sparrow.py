import json
import time
from pathlib import Path

import pytest

from sewing_optimiser import sparrow
from sewing_optimiser.layout import FabricSettings
from sewing_optimiser.nest import check
from sewing_optimiser.pdf_import import extract_pieces

PDF = Path(__file__).parent.parent / "examples/Cuff leggings/bt12-BW-projector-pattern.pdf"


@pytest.mark.skipif(not sparrow.available() or not PDF.exists(), reason="Sparrow is not built, or no example patterns")
def test_sparrow_layout_fits_and_keeps_the_gap():
    settings = FabricSettings(width=900)
    [run] = sparrow.start(extract_pieces(PDF, "2-3t"), settings, seconds=10)
    while not run.done():
        time.sleep(0.5)
    layout = run.layout()
    assert len(layout.placements) == 4
    check(layout.placements, layout.fabric_shape, settings.gap - 0.5)  # outlines are simplified by 0.2 mm for Sparrow
    assert all(p.rotation in (0, 180) for p in layout.placements)  # grainline still along the length
