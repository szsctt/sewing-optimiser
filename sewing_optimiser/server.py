"""Local web app: review pieces, set up fabric, lay out and project.

Run with `pixi run app`, then open http://localhost:8000.
"""

import re
from pathlib import Path

import pymupdf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from shapely import affinity

from . import project as proj
from .layout import _align, make_layouts
from .nest import Stripes
from .output import layout_svg, write_pdf
from .pdf_import import MM_PER_PT, PICK_MIN_AREA, _reflect, faces, list_sizes, sheet_lines, sheets_of

ROOT = Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"
STATIC = Path(__file__).parent / "static"
app = FastAPI()
last = {"layouts": [], "stripes": None}  # most recent layouts, for the PDF download and projector


def _pdf(path):
    p = (ROOT / path).resolve()
    if ROOT not in p.parents or p.suffix.lower() != ".pdf" or not p.exists():
        raise HTTPException(404, "no such pattern")
    return p


def _path_d(poly):
    return "".join("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in ring.coords) + "Z"
                   for ring in [poly.exterior, *poly.interiors])


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/projector")
def projector():
    return FileResponse(STATIC / "projector.html")


@app.get("/api/files")
def files():
    return sorted(str(p.relative_to(ROOT)) for p in EXAMPLES.rglob("*.pdf") if "__MACOSX" not in p.parts)


def _sheet_rects(doc, sheet):
    """Each tile's rectangle on the sheet, and the sheet's bounds, in points."""
    rects = [pymupdf.Rect(doc[n].rect) + (dx / MM_PER_PT, dy / MM_PER_PT) * 2 for n, dx, dy in sheet]
    bound = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        bound |= r
    return rects, bound


def _sheet_doc(doc, sheet):
    """A one-page PDF with the sheet's tiles placed at their offsets."""
    rects, bound = _sheet_rects(doc, sheet)
    out = pymupdf.open()
    page = out.new_page(width=bound.width, height=bound.height)
    for (n, _, _), r in zip(sheet, rects):
        page.show_pdf_page(r - (bound.x0, bound.y0) * 2, doc, n)
    return out, bound


@app.get("/api/info")
def info(pdf: str):
    doc = pymupdf.open(_pdf(pdf))
    out = []
    for sheet in sheets_of(_pdf(pdf)):
        _, bound = _sheet_rects(doc, sheet)
        out.append({"x": bound.x0 * MM_PER_PT, "y": bound.y0 * MM_PER_PT,
                    "w": bound.width * MM_PER_PT, "h": bound.height * MM_PER_PT, "pages": [n for n, _, _ in sheet]})
    return {"sizes": list_sizes(_pdf(pdf)), "pages": out}


@app.get("/api/project")
def get_project(pdf: str, size: str | None = None):
    return proj.load(str(_pdf(pdf).relative_to(ROOT)), size or None)


@app.get("/api/page.png")
def page_png(pdf: str, page: int = 0, dpi: int = 12):
    doc = pymupdf.open(_pdf(pdf))
    sheet_doc, _ = _sheet_doc(doc, sheets_of(_pdf(pdf))[page])
    pix = sheet_doc[0].get_pixmap(dpi=min(dpi, 50))
    return Response(pix.tobytes("png"), media_type="image/png")


@app.get("/api/faces")
def page_faces(pdf: str, page: int = 0, layers: str = ""):
    doc = pymupdf.open(_pdf(pdf))
    found = faces(sheet_lines(doc, sheets_of(_pdf(pdf))[page], layers or None)[0], PICK_MIN_AREA)
    # a region between two nested size lines has the inner region as a hole, so each click hits one region
    found.sort(key=lambda f: -f.area)
    return [{"d": _path_d(f), "at": list(f.representative_point().coords[0])} for f in found[:2000]]


def _abs(project):
    return dict(project, pdf=str(_pdf(project["pdf"])))


def _part(region, w, h):
    """A part of a piece for the page: its outline and a label saying where it is and how big."""
    c = region.centroid
    across = "left" if c.x < w / 3 else "right" if c.x > 2 * w / 3 else ""
    along = "top" if c.y < h / 3 else "bottom" if c.y > 2 * h / 3 else ""
    where = " ".join(x for x in (along, across) if x) or "middle"
    x0, y0, x1, y1 = region.bounds
    return {"d": _path_d(region), "label": f"part at the {where}, {(x1 - x0) / 10:.0f} × {(y1 - y0) / 10:.0f} cm"}


@app.post("/api/pieces")
def get_pieces(project: dict):
    out = []
    full = _abs(project)
    for i, p in enumerate(proj.pieces(full)):
        aligned = _align(p.outline, p.grain_deg)
        minx, miny, maxx, maxy = aligned.bounds
        aligned = affinity.translate(aligned, -minx, -miny)
        # parts are drawn over the picture so they can be clicked off or back on
        place = lambda g: affinity.translate(_align(g, p.grain_deg), -minx, -miny)
        parts = [_part(place(r), maxx - minx, maxy - miny) for r in p.regions]
        if p.half is not None:  # a part of a piece cut on the fold is on both halves
            for part, r in zip(parts, p.regions):
                part["d"] += _path_d(place(_reflect(r, *p.fold_edge)))
                part["label"] = re.sub(r" (left|right),", ",", part["label"]) + ", on both halves"
        biggest = max(range(len(parts)), key=lambda k: p.regions[k].area) if parts else None
        if parts:
            parts[biggest]["label"] = "main part"
        says = list(dict.fromkeys(t for t in p.region_labels if re.search(r"\bcut\b", t, re.I)))
        out.append({
            "index": i, "name": p.name, "copies": p.copies, "include": p.include, "fabric": p.fabric,
            "cross_grain": p.cross_grain, "mirror": p.mirror, "cut_on_fold": p.cut_on_fold,
            "match_y": p.match_y, "grain_deg": p.grain_deg, "on_fold": p.half is not None, "page": p.page,
            "lengthen": p.lengthen, "lengthen_at": p.lengthen_at, "source": p.source,
            "d": _path_d(aligned), "w": maxx - minx, "h": maxy - miny, "parts": parts, "says": says,
            "trim": getattr(p, "trimmed", []),
        })
    return {"pieces": out, "joins": full["joins"]}


def _laid_out(pieces, settings):
    try:
        layouts = make_layouts(pieces, settings)
    except ValueError as e:
        raise HTTPException(400, str(e))
    stripes = Stripes(settings.stripe_repeat, settings.stripe_phase) if settings.stripe_repeat else None
    last.update(layouts=layouts, stripes=stripes)
    return [{
        "fabric": l.fabric, "svg": layout_svg(l, stripes), "length": l.length, "width_used": l.width_used,
        "fold_width": l.fold_width, "utilisation": l.utilisation, "compactness": l.compactness, "notes": l.notes,
        "flat_width": l.flat_width,
    } for l in layouts]


@app.post("/api/overlay")
def overlay(project: dict):
    """The pieces found, drawn where they are on the pattern's sheets (page mm), for checking by eye."""
    out = []
    for i, p in enumerate(proj.pieces(_abs(project))):
        if p.source is None or p.page is None:
            continue  # added rectangles and joined pieces have no place on a sheet
        drawn = p.half if p.half is not None else p.outline
        out.append({"index": i, "name": p.name, "sheet": p.page, "d": _path_d(drawn),
                    "full": _path_d(p.outline) if p.half is not None else None,
                    "marks": "".join("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in m.coords) for m in p.marks.geoms)})
    return out


@app.post("/api/layout")
def layout(project: dict):
    proj.save(project)
    return _laid_out(proj.pieces(_abs(project)), proj.fabric_settings(project))


@app.get("/api/combined")
def combined():
    return proj.combined()


@app.post("/api/combined/add")
def combined_add(project: dict):
    """Save the pattern's review choices and add it to the combined layout."""
    proj.save(project)
    entries = proj.combined()
    entry = {"pdf": project["pdf"], "size": project["size"]}
    if entry not in entries:
        proj.save_combined(entries + [entry])
    return proj.combined()


@app.post("/api/combined/remove")
def combined_remove(i: int):
    entries = proj.combined()
    proj.save_combined(entries[:i] + entries[i + 1:])
    return proj.combined()


@app.post("/api/combined/layout")
def combined_layout(fabric: dict):
    """All combined patterns' pieces on one fabric, with these fabric settings."""
    return _laid_out(proj.combined_pieces(ROOT), proj.fabric_settings({"fabric": fabric}))


@app.get("/api/layout.svg")
def layout_file(i: int = 0, inverted: bool = False):
    if i >= len(last["layouts"]):
        raise HTTPException(404, "no layout yet")
    svg = layout_svg(last["layouts"][i], last["stripes"], inverted=inverted, calibration=False)
    return Response(svg, media_type="image/svg+xml")


@app.get("/api/layout.pdf")
def layout_pdf():
    if not last["layouts"]:
        raise HTTPException(404, "no layout yet")
    path = ROOT / "output" / "layout.pdf"
    path.parent.mkdir(exist_ok=True)
    write_pdf(path, last["layouts"], last["stripes"])
    return FileResponse(path, filename="layout.pdf")


def main():
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
