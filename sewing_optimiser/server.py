"""Local web app: review pieces, set up fabric, lay out and project.

Run with `pixi run app`, then open http://localhost:8000.
"""

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
from .pdf_import import MM_PER_PT, faces, list_sizes, sheet_lines, sheets

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


def _sheet_doc(doc, sheet):
    """A one-page PDF with the sheet's tiles placed at their offsets."""
    rects = [pymupdf.Rect(doc[n].rect) + (dx / MM_PER_PT, dy / MM_PER_PT) * 2 for n, dx, dy in sheet]
    bound = rects[0]
    for r in rects[1:]:
        bound |= r
    out = pymupdf.open()
    page = out.new_page(width=bound.width, height=bound.height)
    for (n, _, _), r in zip(sheet, rects):
        page.show_pdf_page(r - (bound.x0, bound.y0) * 2, doc, n)
    return out, bound


@app.get("/api/info")
def info(pdf: str):
    doc = pymupdf.open(_pdf(pdf))
    out = []
    for sheet in sheets(doc):
        _, bound = _sheet_doc(doc, sheet)
        out.append({"x": bound.x0 * MM_PER_PT, "y": bound.y0 * MM_PER_PT,
                    "w": bound.width * MM_PER_PT, "h": bound.height * MM_PER_PT, "pages": [n for n, _, _ in sheet]})
    return {"sizes": list_sizes(_pdf(pdf)), "pages": out}


@app.get("/api/project")
def get_project(pdf: str, size: str | None = None):
    return proj.load(str(_pdf(pdf).relative_to(ROOT)), size or None)


@app.get("/api/page.png")
def page_png(pdf: str, page: int = 0, dpi: int = 12):
    doc = pymupdf.open(_pdf(pdf))
    sheet_doc, _ = _sheet_doc(doc, sheets(doc)[page])
    pix = sheet_doc[0].get_pixmap(dpi=min(dpi, 50))
    return Response(pix.tobytes("png"), media_type="image/png")


@app.get("/api/faces")
def page_faces(pdf: str, page: int = 0, layers: str = ""):
    doc = pymupdf.open(_pdf(pdf))
    found = faces(sheet_lines(doc, sheets(doc)[page], layers or None)[0])
    found.sort(key=lambda f: f.area)  # small first, so they are drawn on top
    return [{"d": _path_d(f), "coords": [list(c) for c in f.exterior.coords]} for f in found[:2000]]


def _abs(project):
    return dict(project, pdf=str(_pdf(project["pdf"])))


@app.post("/api/pieces")
def get_pieces(project: dict):
    out = []
    for i, p in enumerate(proj.pieces(_abs(project))):
        aligned = _align(p.outline, p.grain_deg)
        minx, miny, maxx, maxy = aligned.bounds
        aligned = affinity.translate(aligned, -minx, -miny)
        out.append({
            "index": i, "name": p.name, "copies": p.copies, "include": p.include, "fabric": p.fabric,
            "cross_grain": p.cross_grain, "mirror": p.mirror, "cut_on_fold": p.cut_on_fold,
            "match_y": p.match_y, "grain_deg": p.grain_deg, "on_fold": p.half is not None, "page": p.page,
            "d": _path_d(aligned), "w": maxx - minx, "h": maxy - miny,
        })
    return out


@app.post("/api/layout")
def layout(project: dict):
    proj.save(project)
    settings = proj.fabric_settings(project)
    try:
        layouts = make_layouts(proj.pieces(_abs(project)), settings)
    except ValueError as e:
        raise HTTPException(400, str(e))
    stripes = Stripes(settings.stripe_repeat, settings.stripe_phase) if settings.stripe_repeat else None
    last.update(layouts=layouts, stripes=stripes)
    return [{
        "fabric": l.fabric, "svg": layout_svg(l, stripes), "length": l.length, "width_used": l.width_used,
        "fold_width": l.fold_width, "utilisation": l.utilisation, "notes": l.notes,
    } for l in layouts]


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
