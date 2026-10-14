"""Regenerate the landing page screenshots in src/undertow/web/static/demo/.

Drives a real headless Chrome through its DevTools protocol against a running dev server, so
the images always match the current design. Needs Google Chrome and the `websocket-client`
package (not a project dependency: `uv run --with websocket-client python scripts/...`).

    uv run uvicorn undertow.web.app:app --port 8011 &
    uv run --with websocket-client python scripts/capture_demo_screenshots.py

Captures 390x844 at 2x as WebP. The "sample data" banner is hidden in the images; the landing
page says in words that they are sample screens. The draft board shot plays the first rounds by
hand (accepting each recommendation), so it needs `?pace=0` support in the draft room.
"""

import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "undertow" / "web"
OUT = WEB / "static" / "demo"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8011"
PORT = 9333
HIDE_BANNER = (
    "var s=document.createElement('style');"
    "s.textContent='.sample-banner{display:none!important}';document.head.appendChild(s);"
)


class Chrome:
    def __init__(self, width: int = 390, height: int = 844, scale: int = 2) -> None:
        self.profile = tempfile.mkdtemp(prefix="undertow-cdp-")  # fresh: no stale cache
        self.proc = subprocess.Popen(
            [
                "google-chrome",
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                f"--remote-debugging-port={PORT}",
                "--remote-allow-origins=*",
                f"--user-data-dir={self.profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        page = None
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        if page is None:
            raise RuntimeError("could not start Chrome")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        self.n = 0
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=scale,
            mobile=True,
        )

    def send(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        time.sleep(1.5)

    def js(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def wait(self, expr: str, timeout: float = 40) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expr):
                return
            time.sleep(0.3)
        raise TimeoutError(expr)

    def snap(self, name: str) -> None:
        time.sleep(0.8)
        r = self.send("Page.captureScreenshot", format="webp", quality=88)
        (OUT / f"{name}.webp").write_bytes(base64.b64decode(r["data"]))
        print("saved", name)

    def close(self) -> None:
        try:
            self.ws.close()
        finally:
            self.proc.terminate()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    c = Chrome()
    try:
        # Draft room: on the clock with a recommendation, then (after a few rounds) the board.
        c.goto(f"{BASE}/preview/draft?pace=0&clock=90&autostart=1")
        c.js(HIDE_BANNER)
        c.wait("document.getElementById('clock').classList.contains('me')")
        c.js("window.scrollTo(0,0)")
        c.snap("draft-recommendation")
        while True:  # accept each recommendation until the fourth round
            c.wait("document.getElementById('clock').classList.contains('me')")
            filled = c.js("document.querySelectorAll('#board td[class*=pos-]').length")
            if filled >= 36:
                break
            c.js("document.querySelector('#rec .btn').click()")
            c.js("document.getElementById('sheet-ok').click()")
            time.sleep(0.4)
        time.sleep(1.5)
        c.js("window.scrollTo(0, document.getElementById('tabs').offsetTop - 70)")
        c.snap("draft-board")

        c.goto(f"{BASE}/preview/matchup")
        c.js(HIDE_BANNER)
        c.snap("matchup")

        c.goto(f"{BASE}/preview/team")
        c.js(HIDE_BANNER)
        c.js("document.querySelector('.thead .help').classList.add('open')")  # show the ? tip
        c.snap("team")

        c.goto(f"{BASE}/preview/players")
        c.js(HIDE_BANNER)
        c.snap("players")

        c.goto(f"{BASE}/preview/league")
        c.js(HIDE_BANNER)
        c.snap("league")
    finally:
        c.close()


if __name__ == "__main__":
    main()
