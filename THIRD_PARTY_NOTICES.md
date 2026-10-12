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
- pyLMUSharedMemory, <https://github.com/s-victor/pyLMUSharedMemory> (also mirrored under
  TinyPedal) — MIT, Copyright (c) 2021 Tony Whitley, Copyright (c) 2025 Xiang. Licence file
  checked for V0.9.3 at commit `6cef58e22bf025268acee93e9e5ea4f569939c1b` (2026-09-22).
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


## V0.9.3 — facts taken, candidates reviewed

Again **no third-party source code, assets or data were copied into Garage16**. What V0.9.3
took from other projects are facts about LMU's interface; the full review (what was examined,
what was rejected and why) is in `docs/OPEN_SOURCE_REVIEW_V0_9_3.md`.

Facts, all from pyLMUSharedMemory (MIT, see above; the project states they come from LMU's own
`SharedMemoryInterface.hpp`), each checked against a second source where one existed:

| Fact | Used in | Cross-check |
|---|---|---|
| names/types/order of the 13 bytes LMU took from `ScoringInfoV01`'s reserve (session time, time of day, fixed setup, grip level, cloud coverage, track-limits steps per penalty/point) | `client/lmu/structs.py` | sizes add up to the former 200-byte reserve (`tests/test_v093_client.py`) |
| `mVehicleModel` = "brand & model name" (TelemInfoV01, 30 bytes) | `server/car_identity.py` | field already pinned at offset 796 by `tests/test_lmu_structs.py` |
| `mSurfaceType`: 0 dry, 1 wet, 2 grass, 3 dirt, 4 gravel, 5 kerb, 6 special | `server/track_limits.py` | rFactor 2 internals header (same enum) |
| `mCountLapFlag`: 0 neither lap nor time, 1 lap but not time, 2 lap and time | `client/telemetry/parser.py`, `server/track_limits.py` | rFactor 2 internals header; real Garage16 session (CHANGELOG V0.6.1: normal laps show 2) |
| `mTrackLimitsSteps` = track-limits points × steps per point | `server/track_limits.py` | — (reported as unreliable and ignored if values are out of range) |

No new runtime dependency was added to the server, the clients or the web app.

## V0.9.3.1 — facts from LMU's documentation and from TinyPedal (read only)

Still **no third-party source code, assets or data files were copied into Garage16**. The
correction release relies on these facts; where they come from a GPL project, only the fact is
used (no code, no file, no structure), and every one of them is treated as *unconfirmed* until
Garage16's own LMU recordings confirm it (docs/LMU_VERIFICATION_PROTOCOL.md, (r)–(v)):

| Fact | Source | Used in | How it is used |
|---|---|---|---|
| LMU detects track limits by the average track width ("two wheels inside the white lines or four wheels on the kerbs"), scores an offence 2–5 s after rejoining by the time gained, and outside races invalidates a lap when an offence earns warning points | Le Mans Ultimate guide, "Understanding track limits" (Studio 397, public documentation) | `server/track_limits.py` (tiers, attribution window), `client/telemetry/lap_verdict.py` | heuristics only send a lap to review; 6 s late window after the line |
| warning points = `mTrackLimitsSteps / mTrackLimitsStepsPerPoint`; points per penalty = steps per penalty / steps per point | TinyPedal `tinypedal/adapter/lmu_reader.py` (GPL-3.0, revision 4e2de40, read only) | `server/track_limits.py` `points_of` | points shown in the findings, no decision depends on it |
| `mLastLapTime <= 0` marks an invalid last lap | same TinyPedal file (read only) | `server/track_limits.py` (`game_last_lap_time_withdrawn`) | review only, never invalidates by default |
| LMU cuts `mVehicleModel` (char[30]) after 29 characters; three cut names and their full names: Aston Martin Vantage AMR LMGT3, Chevrolet Corvette Z06 LMGT3.R, Lamborghini Huracan LMGT3 Evo2 | same TinyPedal file, `LMU_CAR_MODEL_CORRECTION` (read only) — car names are facts, not code; only the three name pairs are used | `server/car_identity.py` `MODEL_NAME_FIXES` | cut names repaired; any other name of the cut length is marked "…" |

TinyPedal was not added as a dependency and none of its code was used; GPL-3.0 code would only
be considered after a compatibility check, which was not needed here.


## V0.9.4 — LapTime Analyzer and Stint Analyzer

**No new dependency and no third-party code** in the server, the clients or the web app. The
new parts are Garage16's own code:

| Part | Basis | Used in |
|---|---|---|
| Theil–Sen slope (median of all pairwise slopes) for lap-time trend, tyre degradation and forecast | H. Theil (1950) and P. K. Sen (1968) — a textbook statistical method, implemented from its definition | `web/js/stints/stats.js` `theilSen` |
| Median, median absolute deviation, Pearson correlation, least squares with two inputs (normal equations) | textbook statistics, implemented from the definitions | `web/js/stints/stats.js` |
| Lateral offset of one driven line from another (nearest point within a window, sign from the corner direction) | own geometry | `server/telemetry_analysis.py` `line_offset`, `inside_sign` |
| Zoomable track map with pinch zoom, SVG charts of the Stint Analyzer | own code (no d3, no chart library) | `web/js/analysis/linemap.js`, `web/js/stints/chart.js` |
| Shared-memory fields `mNumPitstops`, `mPlace`, `mFlag`, `mInPits`, `mTimeGapCarAhead/Behind`, `mFront/RearTireCompoundIndex/Name`, `mFuelCapacity`, `mDentSeverity`, `mOverheating`, `mDetached`, `mSessionTimeRemaining`, `mMaxLaps` | already in Garage16's structure definitions since V0.9.3 (field layout from LMU's `SharedMemoryInterface` header, cross-checked with pyLMUSharedMemory, MIT — see above); their meaning in practice is still to be confirmed (docs/LMU_VERIFICATION_PROTOCOL.md (w)) | `client/telemetry/channels.py`, `client/telemetry/parser.py` |

Considered and not used: d3-zoom (ISC) for the map — the needed pan/zoom is a few lines of own
code, an extra library would only add weight.
