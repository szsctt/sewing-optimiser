# sewing-optimiser

Lays out sewing pattern pieces on fabric to use as little fabric as possible, and projects the layout onto the fabric at 1:1.

## App

    pixi run app

Then open http://localhost:8000.

1. **Pattern.** Choose a PDF from `examples/` and its size layer ("every line" for files with one size per file). Tick *Pick pieces by hand* for files the reader cannot split into pieces; then click the regions that make up each piece.
2. **Pieces.** Untick option pieces you are not making (short or long sleeves), and check names, copies, fabric (pieces with different fabric names get separate layouts), grainline, cross-grain, mirrored pairs and cut-on-fold. Click a piece picture to set its stripe match line.
3. **Fabric.** Width, gap between pieces, seam allowance to add, one-way fabric, stripe repeat. *Use leftover fabric from a photo*: photograph the fabric from above with an A4 sheet on it, click the sheet's corners, trace the fabric edge, and mark the grain direction.
4. **Lay out.** Shows the layout and fabric used; download a 1:1 PDF, or open the projector view.
5. **Projector.** Calibrate once per projector set-up: drag four handles onto the corners of a known rectangle (a cutting mat), then check the 10 cm square. Arrow keys move the layout to project it in sections. Calibration is kept in the browser.

Choices are saved per pattern and size in `projects/`.

## Command line

    pixi run layout "examples/Cuff leggings/bt12-BW-projector-pattern.pdf"                       # list layers
    pixi run layout "examples/Cuff leggings/bt12-BW-projector-pattern.pdf" --size 2-3t --width 900 --out output/cuff

Options: `--gap`, `--one-way`, `--unfold`, `--seam-allowance`, `--stripes MM`, `--skip TEXT`, `--cross-grain TEXT`, `--tries`.

## How it works

- **Pieces.** Strokes on the size layer are joined into closed regions; regions that share an edge (mirrored halves, pieces split by internal lines) are merged. Names, "cut N", grainline and fold labels come from the text in and around each piece. Short strokes touching an outline are kept as notches.
- **Tiled files.** A4/Letter pages are joined into one sheet by matching paths that run off one page and continue on another; blank tiles are placed from the page grid.
- **Cut on fold.** Half pieces are mirrored across their fold edge. The layout also tries cutting them on a folded strip along the left selvedge (with mirrored pairs cut through both layers) and keeps whichever uses less fabric.
- **Nesting.** Pieces are turned so the grainline runs along the length (0°/180°, plus 90°/270° for cross-grain pieces) and placed largest first at the lowest free position, found on a 2 mm grid for all positions at once. Several piece orders are tried. The final layout is checked on the exact shapes for the gap and the fabric edge.
- **Stripes.** Pieces with a match line are placed only where that line falls on a stripe.

## Current limits

- Makers that draw the edges shared by every size on a separate base layer (Make by TFS Fog Tee, Paper Theory Pocket Skirt A4) do not give closed outlines yet.
- Sizes marked only by line colour on one sheet (Merino leggings, Pocket Skirt A0) need picking by hand.
- Tiles that repeat no paths across page edges (the nappy cover files) are read page by page.
