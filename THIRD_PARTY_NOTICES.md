# Third-party notices

Garage16 contains no source code copied from the projects below. They are listed
because their work informed parts of V0.9.0, and so that the origin of each idea or
fact is traceable.

## ApexDash — idea and feature list (MIT)

<https://github.com/AlexiMarin/ApexDash> — Copyright (c) 2026 ApexDash Contributors.

The V0.9.0 lap analysis (track map, telemetry charts, lap comparison with delta, car
physics view, circuit library) follows that project's feature list. Garage16's
implementation is its own: different data source (live shared memory uploaded to the
server instead of LMU's `.duckdb` files), different stack (plain JavaScript/Canvas and
FastAPI), different layout and style. No ApexDash code, assets or track data are
included. Its licence (MIT) is reproduced here out of courtesy:

```
MIT License

Copyright (c) 2026 ApexDash Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## LMU Pitwall and pyLMUSharedMemory — shared-memory field layout (MIT)

- LMU Pitwall, <https://github.com/Swizzjack/lmu-pitwall> — Copyright (c) 2025 Swizzjack (MIT).
- pyLMUSharedMemory, <https://github.com/TinyPedal/pyLMUSharedMemory> — MIT according to the
  repository's metadata; its licence file could not be retrieved when this was written, so
  its copyright line is not reproduced here.
- goLMUSharedMemory, <https://github.com/stephenhoran/goLMUSharedMemory> — MIT (already the
  reference for the pre-0.9 struct definitions, see `client/lmu/structs.py`).

Used only for **facts**: the names, order and sizes of the fields LMU 1.3+ placed inside the
reserved bytes of its telemetry structs (driver aids, wheel extras), which the projects state
they took from LMU's own `Support\SharedMemoryInterface\SharedMemoryInterface.hpp`. That
header is Studio 397's and is not redistributed here. The two projects were compared with each
other, and the resulting offsets are pinned by `tests/test_lmu_structs.py`; they still have to
be confirmed against a real session (`docs/LMU_VERIFICATION_PROTOCOL.md`, items (h)-(k)).

The Pitwall project's notes were also the source of the finding that LMU reports brake disc
temperatures in Kelvin although its header comment says Celsius (`server/telemetry_channels.py`
detects either).

## Bundled in the "Client + analysis app" edition only (V0.9.2)

The analysis-window edition of the desktop client (`Garage16-Client-Analysis-Setup.exe`)
bundles these packages unchanged, as installed by `pip install -r
client/requirements-analysis.txt`. The plain client, the server and the web app contain none
of them. Their licence texts are inside the installed packages (`*.dist-info`) and on their
project pages.

| Package | Licence | What for |
|---|---|---|
| [pywebview](https://github.com/r0x0r/pywebview) | BSD 3-Clause | the analysis window |
| [pythonnet](https://github.com/pythonnet/pythonnet) and clr_loader | MIT | lets pywebview use the Windows WebView2 control |
| bottle, proxy_tools | MIT | pywebview dependencies |
| Microsoft Edge WebView2 SDK assemblies (shipped inside pywebview: `Microsoft.Web.WebView2.*.dll`, `WebView2Loader.dll`) | Microsoft's WebView2 SDK licence (redistributable) | the WebView2 control itself |

The WebView2 **runtime** is not bundled: it is part of Windows 11 and updated Windows 10
(or Microsoft's free download).
