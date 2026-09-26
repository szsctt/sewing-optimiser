"""Record demo/demo.mp4 and demo/demo.gif: the app used on the sample pattern, in Google Chrome.

Frames come from Chrome's screencast and are joined with ffmpeg.

Run with `pixi run demo`. The app runs on its own port with a throwaway projects folder,
so saved choices are left alone.
"""

import base64
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from sewing_optimiser import project, server

DEMO = Path(__file__).parent
PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
PATTERN = "demo/sample-pattern.pdf"


def caption(page, text):
    """Show a caption bar at the bottom of the page."""
    page.evaluate("""text => {
        let bar = document.getElementById('demo-caption');
        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'demo-caption';
            bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:9999;padding:14px 20px;' +
                'background:rgba(20,20,20,0.85);color:#fff;font:20px -apple-system,system-ui,sans-serif';
            document.body.appendChild(bar);
        }
        bar.textContent = text;
    }""", text)
    page.wait_for_timeout(1800)


def run(page):
    page.goto(URL)
    page.wait_for_timeout(800)
    caption(page, "Choose a pattern PDF and the layer with your size")
    page.select_option("#file", PATTERN)
    page.wait_for_function("document.querySelector('#size').options.length > 1")
    page.select_option("#size", "Size M")
    page.click("#load")
    page.wait_for_selector("#pieces tr")
    caption(page, "Names, copies, grainlines and cut-on-fold pieces are read from the pattern")
    page.locator("#pieces-section").scroll_into_view_if_needed()
    page.wait_for_timeout(1500)

    caption(page, "Open a piece to choose between cutting options, such as a cropped length")
    front = page.evaluate("pieces.findIndex(p => p.name === 'FRONT')")
    page.click(f'svg.thumb[data-i="{front}"]')
    page.wait_for_timeout(1500)
    page.hover('#ed-view path[data-part]:not([fill="transparent"]), #ed-view path[data-part]')
    page.wait_for_timeout(800)
    small = page.evaluate("pieces[%d].parts.findIndex(p => p.label !== 'main part')" % front)
    page.click(f'#ed-parts input[data-part="{small}"]')
    page.wait_for_timeout(2500)
    page.click("#ed-close")

    caption(page, "Enter the fabric width and what to aim for, then lay out")
    page.locator("#fabric-section").scroll_into_view_if_needed()
    page.fill("#width", "1100")
    page.select_option("#aim", "length")
    page.wait_for_timeout(800)
    page.click("#run")
    page.wait_for_selector("#layouts svg", timeout=120_000)
    page.locator("#result-section").scroll_into_view_if_needed()
    caption(page, "Grainlines run along the fabric, and mirrored pairs and fold pieces are handled")
    page.wait_for_timeout(2000)

    caption(page, "Or aim for the most compact rectangle")
    page.locator("#fabric-section").scroll_into_view_if_needed()
    page.select_option("#aim", "compact")
    page.click("#run")
    page.wait_for_function("!document.querySelector('#run-status').textContent", timeout=120_000)
    page.locator("#result-section").scroll_into_view_if_needed()
    page.wait_for_timeout(2500)

    page.goto(f"{URL}/projector?i=0")
    page.wait_for_timeout(800)
    caption(page, "Projector: drag the corners onto a cutting mat to calibrate")
    for (x, y), (tx, ty) in [((256, 160), (300, 190)), ((1024, 640), (990, 610))]:
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(tx, ty, steps=15)
        page.mouse.up()
        page.wait_for_timeout(400)
    page.select_option("#mode", "layout")
    caption(page, "Then project the layout at 1:1; arrow keys move it in sections")
    for _ in range(3):
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(700)
    page.wait_for_timeout(1500)


def main():
    projects = Path(tempfile.mkdtemp())
    project.PROJECTS = projects  # keep the demo's choices out of projects/
    server.EXAMPLES = DEMO  # offer only the sample pattern
    config = uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    time.sleep(2)

    frames = Path(tempfile.mkdtemp())
    shots = []  # (time, file) of each frame Chrome sends while the page changes
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        cdp = page.context.new_cdp_session(page)

        def frame(event):
            path = frames / f"{len(shots):05d}.jpg"
            path.write_bytes(base64.b64decode(event["data"]))
            shots.append((event["metadata"]["timestamp"], path))
            cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

        cdp.on("Page.screencastFrame", frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 85, "maxWidth": 1280, "maxHeight": 800})
        run(page)
        cdp.send("Page.stopScreencast")
        browser.close()
    # each frame stays on screen until the next one arrives
    listing = frames / "frames.txt"
    lines = []
    for (t, path), (t_next, _) in zip(shots, shots[1:] + [(shots[-1][0] + 1.5, None)]):
        lines += [f"file '{path}'", f"duration {max(t_next - t, 0.02):.3f}"]
    listing.write_text("\n".join(lines + [f"file '{shots[-1][1]}'"]) + "\n")
    source = ["-f", "concat", "-safe", "0", "-i", listing]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *source, "-vf", "fps=25,scale=1280:-2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28", DEMO / "demo.mp4"], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *source, "-vf",
                    "fps=6,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse",
                    DEMO / "demo.gif"], check=True)
    shutil.rmtree(frames)
    shutil.rmtree(projects)
    print("wrote demo/demo.mp4 and demo/demo.gif")


if __name__ == "__main__":
    main()
