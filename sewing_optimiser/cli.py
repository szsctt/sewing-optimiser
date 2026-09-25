"""Command line: lay out one size of a pattern on fabric of a given width."""

import argparse
from pathlib import Path

from .layout import FabricSettings, make_layouts
from .nest import Stripes
from .output import write_pdf, write_svg
from .pdf_import import extract_pieces, list_sizes


def _matches(piece, texts):
    return any(t.lower() in piece.name.lower() for t in texts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--size", help="size: a PDF layer or a line style (style:...); omit to list them (files without either use every line)")
    ap.add_argument("--width", type=float, help="fabric width in mm, selvedge to selvedge")
    ap.add_argument("--gap", type=float, default=3.0, help="minimum gap between pieces, mm")
    ap.add_argument("--one-way", action="store_true", help="napped or one-way fabric: no 180° turns")
    ap.add_argument("--unfold", action="store_true", help="unfold cut-on-fold pieces instead of cutting them on a folded strip")
    ap.add_argument("--seam-allowance", type=float, default=0.0, help="mm to add around every piece")
    ap.add_argument("--stripes", type=float, metavar="MM", help="stripe repeat along the length, mm")
    ap.add_argument("--skip", action="append", default=[], metavar="TEXT",
                    help="leave out pieces whose name contains TEXT (e.g. 'short sleeve'); repeatable")
    ap.add_argument("--cross-grain", action="append", default=[], metavar="TEXT",
                    help="pieces whose name contains TEXT may also turn 90°; repeatable")
    ap.add_argument("--tries", type=int, default=8, help="piece orders to try; more is slower and may be shorter")
    ap.add_argument("--out", default="layout", help="output path without extension")
    args = ap.parse_args()

    sizes = list_sizes(args.pdf)
    if not args.size and sizes:
        print("\n".join(f"{value}    {label}" if value != label else value for value, label in sizes))
        return
    if not args.width:
        ap.error("--width is required with --size")

    pieces = extract_pieces(args.pdf, args.size or None)
    for p in pieces:
        p.include = not _matches(p, args.skip)
        p.cross_grain = _matches(p, args.cross_grain)
        p.cut_on_fold = not args.unfold
        p.match_y = 0.0 if args.stripes else None  # match at the top edge until set in the review step
        grain = "not found, assumed vertical" if p.grain_deg is None else f"{p.grain_deg:.0f}°"
        fold = ", on fold" if p.half is not None else ""
        print(f"{p.name}: {'cut ' + str(p.copies) if p.include else 'skipped'}, grainline {grain}{fold}")

    settings = FabricSettings(args.width, gap=args.gap, one_way=args.one_way, seam_allowance=args.seam_allowance,
                              stripe_repeat=args.stripes, tries=args.tries)
    layouts = make_layouts(pieces, settings)
    if not layouts:
        ap.exit(1, "No pieces found on this layer. Try another layer, or pick pieces by hand in the app.\n")
    stripes = Stripes(args.stripes) if args.stripes else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for layout in layouts:
        svg = out.with_name(f"{out.name}-{layout.fabric}.svg")
        write_svg(svg, layout, stripes)
        fold = f", fold {layout.fold_width:.0f} mm over" if layout.fold_width else ""
        print(f"{layout.fabric}: length used {layout.length:.0f} mm, width used {layout.width_used:.0f} mm{fold}, "
              f"utilisation {layout.utilisation:.0%}")
        for note in layout.notes:
            print(note)
    write_pdf(out.with_suffix(".pdf"), layouts, stripes)
    print(f"Wrote {out.with_suffix('.pdf')} and one SVG per fabric")


if __name__ == "__main__":
    main()
