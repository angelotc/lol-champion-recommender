"""Screenshot the panel headless (Microsoft Edge) with a given state, for checking views.

  uv run scripts/render_panel.py out.png                       demo picks view
  uv run scripts/render_panel.py out.png --demo status
  uv run scripts/render_panel.py out.png --state state.json --click ".rec" --height 1080
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent / "panel"
EDGE_PATHS = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
]


def render(out: Path, state: dict | None = None, demo: str | None = None, click: list[str] | None = None,
           width: int = 300, height: int = 900, scale: float = 1.0, mode: str = "") -> Path:
    edge = next((p for p in EDGE_PATHS if p.exists()), None)
    if not edge:
        raise SystemExit("Microsoft Edge not found")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "panel"
        shutil.copytree(PANEL, root)
        script = [f"setScale({scale})", f"setMode({json.dumps(mode)})"]
        if state is not None:
            script.append(f"updateState({json.dumps(state)})")
        for sel in click or []:
            script.append(f"(document.querySelector({json.dumps(sel)})||{{click(){{}}}}).click()")
        inject = (f"<style>html,body{{width:{width}px;height:{height}px}}</style>"
                  f"<script>window.addEventListener('load',function(){{setTimeout(function(){{{';'.join(script)}}},{700 if demo else 0})}})</script></body>")
        index = root / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace("</body>", inject), encoding="utf-8")
        url = index.as_uri() + (f"?demo={demo}" if demo else "")
        out = out.resolve()
        subprocess.run(
            [str(edge), "--headless", "--disable-gpu", "--hide-scrollbars", f"--screenshot={out}",
             f"--window-size={width},{height}", "--virtual-time-budget=4000", url],
            capture_output=True, timeout=60,
        )
    if not out.exists():
        raise SystemExit("screenshot failed")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--state", type=Path, help="JSON payload as sent by the app")
    ap.add_argument("--demo", help="demo view instead of a state (picks, status, ...)")
    ap.add_argument("--click", action="append", help="CSS selector to click after rendering")
    ap.add_argument("--width", type=int, default=300)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--mode", default="")
    a = ap.parse_args()
    state = json.loads(a.state.read_text(encoding="utf-8")) if a.state else None
    print(render(a.out, state, a.demo if not state else None, a.click, a.width, a.height, a.scale, a.mode))


if __name__ == "__main__":
    main()
