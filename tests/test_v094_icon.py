"""V0.9.4 — the Garage16 icon: an angular "16" (no right angles), shipped to every place it shows."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _angles(points):
    import math

    out = []
    n = len(points)
    for i in range(n):
        (ax, ay), (bx, by), (cx, cy) = points[i - 1], points[i], points[(i + 1) % n]
        v1, v2 = (ax - bx, ay - by), (cx - bx, cy - by)
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (math.hypot(*v1) * math.hypot(*v2))
        out.append(round(math.degrees(math.acos(max(-1.0, min(1.0, cos))))))
    return out


def test_the_svg_has_no_right_angle():
    svg = (ROOT / "web/favicon.svg").read_text(encoding="utf-8")
    assert svg == (ROOT / "client/assets/garage16.svg").read_text(encoding="utf-8")
    for d in re.findall(r'd="([^"]+)"', svg):
        for sub in d.split("Z"):
            pts = [tuple(map(float, p.split(","))) for p in re.findall(r"-?\d+\.?\d*,-?\d+\.?\d*", sub)]
            if pts:
                assert 90 not in _angles(pts), sub


def test_icon_files_exist_and_are_wired():
    import struct

    data = (ROOT / "packaging/garage16.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    sizes = {(data[6 + 16 * i] or 256) for i in range(count)}   # width byte, 0 = 256
    assert (reserved, kind) == (0, 1) and {16, 32, 48, 256} <= sizes
    assert (ROOT / "client/assets/garage16.ico").is_file() and (ROOT / "client/assets/garage16.png").is_file()
    assert 'icon=str(project_root / "packaging" / "garage16.ico")' in (ROOT / "packaging/garage16.spec").read_text(encoding="utf-8")
    assert "SetupIconFile=garage16.ico" in (ROOT / "packaging/garage16.iss").read_text(encoding="utf-8")
    index = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert 'href="favicon.svg"' in index and 'href="favicon.ico"' in index


def test_the_client_finds_its_icon_and_never_fails_without_it(monkeypatch):
    from client import icon

    assert icon.asset("garage16.ico") is not None

    class Root:
        def iconbitmap(self, **kw):
            raise RuntimeError("no display")

        def iconphoto(self, *a):
            raise RuntimeError("no display")

    assert icon.apply_to_tk(Root()) is False
    icon.set_windows_app_id()   # no-op off Windows, never raises
