"""Command line: lay out one size of a pattern on fabric of a given width."""

import argparse
from pathlib import Path

from .nest import nest
from .output import write_pdf, write_svg
from .pdf_import import extract_pieces, list_layers


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--size", help="PDF layer holding the chosen size; omit to list layers")
    ap.add_argument("--width", type=float, help="fabric width in mm, selvedge to selvedge")
    ap.add_argument("--gap", type=float, default=3.0, help="minimum gap between pieces, mm")
    ap.add_argument("--one-way", action="store_true", help="napped or one-way fabric: no 180° turns")
    ap.add_argument("--skip", action="append", default=[], metavar="TEXT",
                    help="leave out pieces whose name contains TEXT (e.g. 'short sleeve'); repeatable")
    ap.add_argument("--out", default="layout", help="output path without extension")
    args = ap.parse_args()

    if not args.size:
        print("\n".join(list_layers(args.pdf)))
        return
    if not args.width:
        ap.error("--width is required with --size")

    pieces = extract_pieces(args.pdf, args.size)
    skipped = [p for p in pieces if any(t.lower() in p.name.lower() for t in args.skip)]
    pieces = [p for p in pieces if p not in skipped]
    for p in skipped:
        print(f"{p.name}: skipped")
    for p in pieces:
        grain = "not found, assumed vertical" if p.grain_deg is None else f"{p.grain_deg:.0f}°"
        fold = ", unfolded from half on fold" if p.unfolded else ""
        print(f"{p.name}: cut {p.copies}, grainline {grain}{fold}")

    placements, length, used_width, utilisation = nest(pieces, args.width, args.gap, allow_180=not args.one_way)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_svg(out.with_suffix(".svg"), placements, args.width, length)
    write_pdf(out.with_suffix(".pdf"), placements, args.width, length)
    print(f"Length used: {length:.0f} mm, width used: {used_width:.0f} mm, utilisation {utilisation:.0%}")
    print(f"Wrote {out.with_suffix('.svg')} and {out.with_suffix('.pdf')}")


if __name__ == "__main__":
    main()
