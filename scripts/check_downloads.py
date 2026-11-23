#!/usr/bin/env python3
"""Check that every download on the case-study page works, byte-for-byte.

Two paths are tested:

1. Lone file: only site/index.html is copied into an empty temp folder and opened via file:// in headless
   Chrome (CDP over the `websockets` package). Every download link is clicked; the page has no data/ folder,
   so the files must come from the data embedded in the page. Each downloaded file must equal the file
   in site/ byte-for-byte.
2. Hosted: site/ is served with a local http.server and every download href is fetched; each response must
   be 200 and equal the file in site/.

Needs Google Chrome (default /usr/bin/google-chrome) and the `websockets` package. Not part of pytest because
it needs a browser; tests/test_build_site.py checks the embedded payloads without one.

    .venv/bin/python scripts/check_downloads.py [--site site] [--chrome /usr/bin/google-chrome]
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def download_links(html: str) -> dict[str, str]:
    """data-dl filename -> href, first occurrence of each."""
    out: dict[str, str] = {}
    for tag in re.findall(r"<a\b[^>]*\bdata-dl=\"[^\"]+\"[^>]*>", html):
        name = re.search(r'data-dl="([^"]+)"', tag).group(1)
        href = re.search(r'href="([^"]+)"', tag).group(1)
        out.setdefault(name, href)
    return out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def click_downloads(chrome: str, page: Path, names: list[str], dl_dir: Path) -> dict[str, bytes | None]:
    import websockets  # imported here so the module loads without it

    port = free_port()
    profile = tempfile.mkdtemp(prefix="cs-chrome-")
    proc = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", f"--remote-debugging-port={port}",
                             f"--user-data-dir={profile}", "--no-first-run", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        targets = None
        for _ in range(100):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))  # noqa: S310
                break
            except OSError:
                time.sleep(0.1)
        if targets is None:
            raise SystemExit("check_downloads: Chrome did not start")
        version = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version"))  # noqa: S310
        page_ws = next(t for t in targets if t["type"] == "page")["webSocketDebuggerUrl"]
        msg_id = 0

        async def call(ws, method, **params):
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == msg_id:
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg.get("result", {})

        results: dict[str, bytes | None] = {}
        # Keep the browser session open while clicking: the download behaviour belongs to it.
        async with websockets.connect(version["webSocketDebuggerUrl"], max_size=2**28) as browser, \
                websockets.connect(page_ws, max_size=2**28) as ws:
            await call(browser, "Browser.setDownloadBehavior", behavior="allow", downloadPath=str(dl_dir))
            await call(ws, "Page.enable")
            await call(ws, "Page.navigate", url=page.as_uri())
            await asyncio.sleep(1.5)
            probe = await call(ws, "Runtime.evaluate", returnByValue=True, expression=(
                "JSON.stringify(" + json.dumps(names) + ".map(function(n){var r=window.__caseStudyDownload&&"
                "window.__caseStudyDownload(n);return [n, r ? r[0].length : null]}))"))
            print("in-page builders (name, characters):", probe.get("result", {}).get("value"))
            for name in names:
                target = dl_dir / name
                expr = f"document.querySelector('a[data-dl=\"{name}\"]').click()"
                await call(ws, "Runtime.evaluate", expression=expr)
                for _ in range(100):
                    if target.is_file() and not (dl_dir / (name + ".crdownload")).exists():
                        break
                    await asyncio.sleep(0.1)
                await asyncio.sleep(0.2)
                results[name] = target.read_bytes() if target.is_file() else None
        return results
    finally:
        proc.terminate()
        proc.wait()
        shutil.rmtree(profile, ignore_errors=True)


def serve_and_fetch(site: Path, hrefs: dict[str, str]) -> dict[str, tuple[int, bytes]]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep the report readable
            pass

    handler = functools.partial(Quiet, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    out = {}
    try:
        for name, href in hrefs.items():
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/{href}") as resp:  # noqa: S310
                out[name] = (resp.status, resp.read())
    finally:
        server.shutdown()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--site", type=Path, default=ROOT / "site")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    site = args.site.resolve()
    hrefs = download_links((site / "index.html").read_text(encoding="utf-8"))
    ok = True
    with tempfile.TemporaryDirectory(prefix="cs-lone-") as tmp:
        lone = Path(tmp) / "page"
        lone.mkdir()
        shutil.copyfile(site / "index.html", lone / "index.html")  # nothing else: no data/, no llms.txt
        dl_dir = Path(tmp) / "downloads"
        dl_dir.mkdir()
        got = asyncio.run(click_downloads(args.chrome, lone / "index.html", list(hrefs), dl_dir))
    print("lone index.html via file:// (no data/ folder next to it):")
    for name, href in hrefs.items():
        want = (site / href).read_bytes()
        data = got.get(name)
        same = data == want
        ok &= same
        print(f"  {'OK  ' if same else 'FAIL'} {name:18} {len(data) if data is not None else 'missing':>8} bytes"
              f"  vs site/{href} {len(want)} bytes{'  identical' if same else ''}")
    print("site/ served over http:")
    for name, (status, body) in serve_and_fetch(site, hrefs).items():
        same = status == 200 and body == (site / hrefs[name]).read_bytes()
        ok &= same
        print(f"  {'OK  ' if same else 'FAIL'} {hrefs[name]:22} HTTP {status} {len(body):>8} bytes")
    print("check_downloads: " + ("all downloads match byte-for-byte" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
