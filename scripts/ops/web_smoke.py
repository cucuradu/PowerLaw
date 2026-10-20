"""Start the dashboard, open every page in headless Chromium and time it (cold, then warm).

Usage: python -m scripts.ops.web_smoke [--port 8599]
Needs Playwright: `uv pip install playwright` and `python -m playwright install chromium`.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
PAGES = ["", "Law_Registry", "News_Feed", "Graph_Explorer", "Temporal_Table", "GNN_Modeling_Lab", "Ingestion_Console"]


def wait_healthy(url: str, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if urllib.request.urlopen(f"{url}/_stcore/health", timeout=2).read() == b"ok":
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit("Streamlit did not become healthy")


def load(page, url: str) -> dict:
    """Seconds until the page title renders and Streamlit has stopped running the script."""
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    started = time.perf_counter()
    page.goto(url, wait_until="domcontentloaded", timeout=180_000)
    page.locator("h1").first.wait_for(state="visible", timeout=180_000)
    visible = time.perf_counter() - started
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=180_000)
    exceptions = page.locator("[data-testid='stException']").count()
    return {"visible_s": round(visible, 2), "done_s": round(time.perf_counter() - started, 2),
            "exceptions": exceptions, "console_errors": len(errors)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8599)
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "dashboard.py", "--server.headless", "true",
                               "--server.port", str(args.port)], cwd=REPO,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_healthy(url)
        results = {}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for name in PAGES:
                page = browser.new_page()
                results[name or "Home"] = {"cold": load(page, f"{url}/{name}"), "warm": load(page, f"{url}/{name}")}
                page.close()
            browser.close()
        print(json.dumps(results, indent=1))
    finally:
        server.terminate()
        server.wait(timeout=30)


if __name__ == "__main__":
    main()
