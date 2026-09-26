"""Fabric needed for each saved pattern at common fabric widths, as a page to check on a phone.

Run with `pixi run widths` (writes output/fabric-needed.html).
"""

import json
import sys
from datetime import date
from pathlib import Path

from shapely import affinity

from . import project as proj
from .layout import make_layouts
from .pdf_import import list_sizes

ROOT = Path(__file__).parent.parent
WIDTHS = [900, 1100, 1150, 1200, 1350, 1400, 1450, 1500, 1600]  # mm, common bolt widths
THUMB = 360  # px, width of the layout pictures (a phone screen)


def _thumbnail(layout):
    """A small SVG of the layout: the fabric and simplified piece outlines."""
    w, h = layout.flat_width or 1, layout.length or 1
    k = THUMB / max(w, 1)
    paths = []
    for p in layout.placements:
        g = affinity.scale(p.outline.simplify(3), k, k, origin=(0, 0))
        paths.append("M" + "L".join(f"{x:.0f},{y:.0f}" for x, y in g.exterior.coords) + "Z")
    return {"w": round(w * k), "h": round(h * k), "d": "".join(paths)}


def _name(entry):
    folder = Path(entry["pdf"]).relative_to("examples").parts[0]  # the pattern's own folder
    sizes = dict(list_sizes(ROOT / entry["pdf"]))
    size = (sizes.get(entry["size"], entry["size"] or "") or "").split(" (")[0].replace("Size_", "Size ")
    return f"{folder}, {size}".strip(", ")


def _kind(size):
    """How a saved size was chosen; layers and size labels read more reliably than line styles."""
    return 0 if size and ":" not in size else 1 if size and size.startswith("label:") else 2


def _saved():
    """One saved choice per pattern folder and size name, preferring the most reliable way of reading it."""
    best = {}
    for path in sorted(proj.PROJECTS.glob("*.json")):
        p = json.loads(path.read_text())
        if not isinstance(p, dict) or not (ROOT / p["pdf"]).exists():
            continue
        sizes = [v for v, _ in list_sizes(ROOT / p["pdf"])]
        if (p["size"] is None and sizes) or (p["size"] is not None and p["size"] not in sizes):
            continue  # saved for a size this file no longer offers
        name = _name(p)
        if name not in best or _kind(p["size"]) < _kind(best[name]["size"]):
            best[name] = p
    # a pattern with size layers or labels saved does not also need a line-style guess
    folder = lambda p: Path(p["pdf"]).relative_to("examples").parts[0]
    sure = {folder(p) for p in best.values() if _kind(p["size"]) < 2}
    return {n: p for n, p in best.items() if _kind(p["size"]) < 2 or folder(p) not in sure}


def main():
    rows = []
    for name, p in _saved().items():
        pieces = proj.pieces(dict(p, pdf=str(ROOT / p["pdf"])))
        settings = proj.fabric_settings(p)
        settings.shape, settings.tries, settings.aim = None, 3, "length"  # the shortest length to buy
        row = {"name": name, "pieces": [f"{x.name} ×{x.copies}" for x in pieces if x.include], "widths": {}}
        shortest = None
        for width in WIDTHS:
            settings.width = width
            try:
                layouts = make_layouts(pieces, settings)
            except ValueError:
                continue
            found = [{"fabric": l.fabric, "length": round(l.length), "thumb": _thumbnail(l)} for l in layouts]
            total = lambda f: sum(x["length"] for x in f)
            # a layout that fits narrower fabric also fits this width, so never report more
            if shortest is None or total(found) < total(shortest):
                shortest = found
            row["widths"][width] = shortest
        rows.append(row)
        print(name, {w: [f["length"] for f in v] for w, v in row["widths"].items()}, file=sys.stderr, flush=True)
    data = json.dumps({"widths": WIDTHS, "patterns": rows, "made": date.today().strftime("%-d %B %Y")})
    template = (Path(__file__).parent / "static" / "widths.html").read_text()
    out = ROOT / "output" / "fabric-needed.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(template.replace("/*DATA*/null", data))
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
