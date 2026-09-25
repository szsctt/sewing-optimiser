# sewing-optimiser

Lays out sewing pattern pieces on fabric to use as little fabric as possible, and projects the layout onto the fabric at 1:1.

## App

    pixi run app

Then open http://localhost:8000.

1. **Pattern.** Choose a PDF from `examples/` and its size: a PDF layer, or for files without layers a line colour and dash style named from the pattern's legend ("every line" for files with one size per file). Tick *Pick pieces by hand* for files the reader cannot split into pieces; then click the regions that make up each piece.
2. **Pieces.** Untick option pieces you are not making (short or long sleeves), and check names, copies, fabric (pieces with different fabric names get separate layouts), grainline, cross-grain, mirrored pairs and cut-on-fold. Click a piece picture to set its stripe match line, or its lengthen/shorten line and amount (for example to match an inseam). Pieces given only by size in the instructions (waistbands, cuffs) can be added as rectangles.
3. **Fabric.** Width, what to aim for (shortest length, narrowest width, or most compact), gap between pieces, seam allowance to add, one-way fabric, stripe repeat. *Use leftover fabric from a photo*: photograph the fabric from above with an A4 sheet on it, click the sheet's corners, trace the fabric edge, and mark the grain direction.
4. **Lay out.** Shows the layout and fabric used; download a 1:1 PDF, or open the projector view.
5. **Projector.** Calibrate once per projector set-up: drag four handles onto the corners of a known rectangle (a cutting mat), then check the 10 cm square. Arrow keys move the layout to project it in sections. Calibration is kept in the browser.

Choices are saved per pattern and size in `projects/`.

## Command line

    pixi run layout "examples/Cuff leggings/bt12-BW-projector-pattern.pdf"                       # list layers
    pixi run layout "examples/Cuff leggings/bt12-BW-projector-pattern.pdf" --size 2-3t --width 900 --out output/cuff

Options: `--aim length|width|compact` (shortest fabric length, the default; narrowest width used; or smallest rectangle around the pieces), `--gap`, `--one-way`, `--unfold`, `--seam-allowance`, `--stripes MM`, `--skip TEXT`, `--cross-grain TEXT`, `--rect NAME:WxL:N`, `--lengthen TEXT:MM@AT`, `--tries`.

A home-made pattern, for example:

    pixi run layout "examples/Nappy cover - longies/Large Longies.pdf" --width 1500 --seam-allowance 6 --lengthen "Extend:150@250" --rect "Waistband:432x102:1" --rect "Leg cuff:229x102:2" --out output/longies

## Tests

    pixi run test

The tests use the patterns in `examples/` (not in the repository) and are skipped without them. `tests/test_app.py` drives the web app's API the way the page does.

## How it works

- **Pieces.** Strokes on the size layer are joined into closed regions; regions that share an edge (mirrored halves, pieces split by internal lines) are merged. Names, "cut N", grainline and fold labels come from the text in and around each piece. Short strokes touching an outline are kept as notches.
- **Tiled files.** A4/Letter pages are joined into one sheet by matching paths that run off one page and continue on another; blank tiles are placed from the page grid. Consecutive pages that each carry the same coloured dotted "tape here" line are joined along it.
- **Small gaps.** Loose line ends up to 5 mm apart are joined to each other (outlines drawn as separate dashes), and ends that stop up to 2 mm short of another line are joined to it.
- **Cut on fold.** Half pieces are mirrored across their fold edge. The layout also tries cutting them on a folded strip along the left selvedge (with mirrored pairs cut through both layers) and keeps whichever uses less fabric.
- **Nesting.** Pieces are turned so the grainline runs along the length (0°/180°, plus 90°/270° for cross-grain pieces) and placed largest first at the lowest free position, found on a 2 mm grid for all positions at once. Several piece orders are tried. The final layout is checked on the exact shapes for the gap and the fabric edge.
- **Stripes.** Pieces with a match line are placed only where that line falls on a stripe.

## Current limits

- The Make by TFS Fog Tee works from its A0 files; its A4/Letter tiles are clipped at the page edges and are not joined.
- Sizes marked only by dash pattern in black on one sheet (Merino leggings, Pocket Skirt A0) need picking by hand: click every strip from the smallest size out to yours. The Pocket Skirt A4 file has size layers and reads directly.
- Home-made patterns do not say how many to cut or where the grainline runs; set copies in the review table. The longies A and B parts differ in length by 14 mm at the hem, so check the joined outline.
