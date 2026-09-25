"""A saved pattern project: the PDF, the chosen size, reviewed pieces and fabric settings (JSON)."""

import json
import re
from dataclasses import asdict
from pathlib import Path

import pymupdf
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .layout import FabricSettings
from .pdf_import import JOIN_GAP, _linework, _notches, extract_pieces, pieces_from_outlines

PROJECTS = Path(__file__).parent.parent / "projects"
PIECE_FIELDS = ("name", "copies", "include", "fabric", "cross_grain", "mirror", "cut_on_fold", "match_y", "grain_deg")


def default(pdf, size=None):
    return {
        "pdf": str(pdf),
        "size": size,  # PDF layer, or None for every line
        "picked": None,  # {page: [[[x, y], ...], ...]} outlines picked by hand, mm; None to find them
        "pieces": [],  # review choices by piece index, keys from PIECE_FIELDS
        "fabric": asdict(FabricSettings()) | {"shape": None},
    }


def path_for(pdf, size):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{Path(pdf).stem}-{size or 'all'}").strip("-").lower()
    return PROJECTS / f"{slug}.json"


def load(pdf, size=None):
    path = path_for(pdf, size)
    return json.loads(path.read_text()) if path.exists() else default(pdf, size)


def save(project):
    PROJECTS.mkdir(exist_ok=True)
    path_for(project["pdf"], project["size"]).write_text(json.dumps(project, indent=1))


def pieces(project):
    """Pieces read from the PDF with the saved review choices applied."""
    if project.get("picked"):
        doc = pymupdf.open(project["pdf"])
        found = []
        for number, outlines in project["picked"].items():
            page = doc[int(number)]
            layers = None if project["size"] is None else {project["size"]}
            lines, stroke = _linework(page, layers)
            # picked regions that touch form one piece; cut inside the drawn line
            merged = unary_union([Polygon(o).buffer(JOIN_GAP) for o in outlines]).buffer(-JOIN_GAP - stroke / 2)
            polys = [Polygon(p.exterior) for p in getattr(merged, "geoms", [merged]) if not p.is_empty]
            marks = _notches(lines, polys)
            for piece in pieces_from_outlines(page, polys, marks):
                piece.page = int(number)
                found.append(piece)
    else:
        found = extract_pieces(project["pdf"], project["size"])
    for piece, choices in zip(found, project["pieces"]):
        for key in PIECE_FIELDS:
            if key in choices:
                setattr(piece, key, choices[key])
    return found


def fabric_settings(project):
    f = dict(project["fabric"])
    shape = f.pop("shape", None)
    return FabricSettings(**f, shape=Polygon(shape) if shape else None)
