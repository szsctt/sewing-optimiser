"""A saved pattern project: the PDF, the chosen size, reviewed pieces and fabric settings (JSON)."""

import json
import re
from dataclasses import asdict
from pathlib import Path

import pymupdf
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .edit import join, mark_on_fold
from .layout import FabricSettings
from .pdf_import import JOIN_GAP, Piece, list_sizes, size_key, text_rectangles, _notches, extract_pieces, pieces_from_outlines, sheet_lines, sheet_text, sheets

PROJECTS = Path(__file__).parent.parent / "projects"
PIECE_FIELDS = ("name", "copies", "include", "fabric", "cross_grain", "mirror", "cut_on_fold", "match_y", "grain_deg",
                "lengthen", "lengthen_at")  # choices by index in the final list; "on_fold" marks a piece as a half on the fold


def default(pdf, size=None):
    return {
        "pdf": str(pdf),
        "size": size,  # PDF layer, or None for every line
        "picked": None,  # {page: [[[x, y], ...], ...]} outlines picked by hand, mm; None to find them
        "pieces": [],  # review choices by piece index, keys from PIECE_FIELDS
        "rectangles": [],  # pieces given only by size: {"name", "width", "length", "copies"}, mm
        "joins": None,  # [upper, lower]: pieces drawn in two parts, by index as read; None: guess
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
        all_sheets = sheets(doc)
        found = []
        for number, outlines in project["picked"].items():
            sheet = all_sheets[int(number)]
            lines, stroke = sheet_lines(doc, sheet, project["size"])
            # picked regions that touch form one piece; cut inside the drawn line
            merged = unary_union([Polygon(o).buffer(JOIN_GAP) for o in outlines]).buffer(-JOIN_GAP - stroke / 2)
            polys = [Polygon(p.exterior) for p in getattr(merged, "geoms", [merged]) if not p.is_empty]
            marks = _notches(lines, polys)
            for piece in pieces_from_outlines(sheet_text(doc, sheet), polys, marks):
                piece.page = int(number)
                found.append(piece)
    else:
        found = extract_pieces(project["pdf"], project["size"])
    for i, piece in enumerate(found):
        piece.source = i
    joins = project.get("joins")
    if joins is None:
        joins = project["joins"] = _front_back(found)
    for upper, lower in joins:  # indices of pieces as read
        joined = join(found[upper], found[lower]) if max(upper, lower) < len(found) else None
        if joined is not None:
            found[upper], found[lower] = joined, None
    found = [p for p in found if p is not None]
    size_name = dict(list_sizes(project["pdf"])).get(project["size"], project["size"] or "").split(" (")[0]
    for r in text_rectangles(project["pdf"]):  # stretch (across the grain) along the longer side
        if size_key(size_name) in r["sizes"]:
            a, b = r["sizes"][size_key(size_name)]
            # the size given for this size replaces a drawing of the same piece
            found = [p for p in found if not p.name.lower().startswith(r["name"].lower())]
            found.append(Piece(f"{r['name']} ({size_name})", box(0, 0, max(a, b), min(a, b)), 90.0, r["copies"]))
    for r in project.get("rectangles", []):  # length runs along the grain
        found.append(Piece(r["name"], box(0, 0, r["width"], r["length"]), 90.0, r.get("copies", 1)))
    for i, choices in enumerate(project["pieces"][:len(found)]):
        if choices.get("on_fold"):
            found[i] = mark_on_fold(found[i])
        for key in PIECE_FIELDS:
            if key in choices:
                setattr(found[i], key, choices[key])
    return found


def _front_back(found):
    """Guess joins: a 'Front of the X' drawn separately from its 'Back of the X' (one piece in home-made patterns)."""
    named = {}
    for i, p in enumerate(found):
        m = re.search(r"\b(front|back) of (?:the )?(\w+)", p.name, re.I)
        if m:
            named.setdefault(m.group(2).lower(), {})[m.group(1).lower()] = i
    return [[pair["front"], pair["back"]] for pair in named.values() if len(pair) == 2]


def fabric_settings(project):
    f = dict(project["fabric"])
    shape = f.pop("shape", None)
    return FabricSettings(**f, shape=Polygon(shape) if shape else None)
