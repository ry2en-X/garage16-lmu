// analysis/touch.js — touch gestures for charts and the track map (V0.9.1).
//
//   tap             place the shared cursor there (on lifting the finger, so a scroll that
//                   starts on a chart does not move the cursor first)
//   drag sideways   scrub the cursor along the lap
//   drag up/down    NOT ours: the page scrolls as usual
//   double tap      reset the zoom (like a double-click with the mouse)
//   hold (0.5 s)    pin the cursor there; dragging on after the hold keeps moving it
//   two fingers     pinch = zoom around the pinch centre, moving both = pan
//
// The decision logic is a small state machine over plain {x, y} points (no DOM), so
// it is tested in Node with fake timers; bindTouchGestures() only wires it to a canvas.
// Vertical drags are left to the browser by not calling preventDefault on them — which
// also needs `touch-action: pan-y` on the canvas (styles.css) so horizontal drags and
// pinches reach us instead of being taken for page scrolls.

const dist = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);
const mid = (p, q) => ({ x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 });
const NOTHING = Object.freeze({ prevent: false });
// Wrapped, not `{ set: setTimeout }`: browsers require setTimeout to be called on the global
// object and throw "TypeError: Illegal invocation" when it is called as a method of another
// object (Node does not check, which is why only a real browser caught this).
const DEFAULT_TIMERS = Object.freeze({ set: (fn, ms) => setTimeout(fn, ms), clear: (id) => clearTimeout(id) });
// How long after a touch the browser's emulated mouse click is ignored (see bindTouchGestures).
export const SYNTHETIC_CLICK_MS = 800;
const PREVENT = Object.freeze({ prevent: true });

/**
 * handlers: { tap(x, y), doubleTap(), scrub(x, y), longPress(x, y), pinch({ factor, centerX }), pan(dxPixels) }
 *   doubleTap is optional; without it a second tap is just another tap.
 *   pinch.factor < 1 zooms in (fingers moved apart), > 1 zooms out (same convention as
 *   viewState.zoom); pan is the horizontal movement of the pinch centre in pixels.
 * options: { longPressMs = 500, threshold = 8, holdToScrub = false, doubleTapMs = 300, timers, now }
 *   holdToScrub: a plain drag always scrolls; scrubbing only starts after the hold
 *   (for the track map, where a vertical stretch of track is a legitimate drag).
 * Every method returns { prevent } — whether the event's default (scrolling) must be cancelled.
 */
export function createGestureTracker(handlers, { longPressMs = 500, threshold = 8, holdToScrub = false, timers = DEFAULT_TIMERS, doubleTapMs = 300, now = () => Date.now() } = {}) {
  let mode = "idle"; // idle | single | scrub | scroll | held | pinch
  let origin = null;
  let timer = null;
  let prevDistance = 0;
  let prevMid = null;
  let lastTap = null; // { at, x, y } of the previous tap, for double taps

  const stopTimer = () => {
    if (timer !== null) {
      timers.clear(timer);
      timer = null;
    }
  };
  const beginPinch = (a, b) => {
    stopTimer();
    mode = "pinch";
    prevDistance = dist(a, b);
    prevMid = mid(a, b);
  };

  return {
    get mode() {
      return mode;
    },
    start(points) {
      stopTimer();
      if (points.length >= 2) {
        beginPinch(points[0], points[1]);
        return PREVENT;
      }
      if (points.length === 1) {
        mode = "single";
        origin = points[0];
        timer = timers.set(() => {
          timer = null;
          if (mode === "single") {
            mode = "held";
            handlers.longPress(origin.x, origin.y);
          }
        }, longPressMs);
      }
      return NOTHING;
    },
    move(points) {
      if (points.length >= 2) {
        if (mode !== "pinch") {
          beginPinch(points[0], points[1]);
          return PREVENT;
        }
        const d = dist(points[0], points[1]);
        const m = mid(points[0], points[1]);
        if (prevDistance > 0 && d > 0) handlers.pinch({ factor: prevDistance / d, centerX: m.x });
        if (m.x !== prevMid.x) handlers.pan(m.x - prevMid.x);
        prevDistance = d;
        prevMid = m;
        return PREVENT;
      }
      if (points.length !== 1) return NOTHING;
      const p = points[0];
      if (mode === "single") {
        if (Math.hypot(p.x - origin.x, p.y - origin.y) < threshold) return NOTHING;
        stopTimer(); // moved: it is not a hold any more
        const horizontal = Math.abs(p.x - origin.x) >= Math.abs(p.y - origin.y);
        if (holdToScrub || !horizontal) {
          mode = "scroll";
          return NOTHING;
        }
        mode = "scrub";
      }
      if (mode === "scrub" || mode === "held") {
        handlers.scrub(p.x, p.y);
        return PREVENT;
      }
      return NOTHING;
    },
    end(points) {
      stopTimer();
      // A finger that went down and came up without moving or holding is a tap.
      if (!points.length && mode === "single") {
        const at = now();
        const second = lastTap && at - lastTap.at <= doubleTapMs && dist(lastTap, origin) < threshold * 4;
        if (second && handlers.doubleTap) {
          lastTap = null;
          handlers.doubleTap();
        } else {
          lastTap = { at, x: origin.x, y: origin.y };
          handlers.tap(origin.x, origin.y);
        }
      }
      if (!points.length) mode = "idle";
      else if (mode === "pinch") mode = "scroll"; // one finger left after a pinch: do not scrub by accident
      return NOTHING;
    },
    cancel() {
      stopTimer();
      mode = "idle";
    },
  };
}

export function bindTouchGestures(element, handlers, options) {
  const tracker = createGestureTracker(handlers, options);
  const points = (event) => Array.from(event.touches || []).map((t) => ({ x: t.clientX, y: t.clientY }));
  const wire = (type, fn) => element.addEventListener(type, (event) => {
    const result = fn(points(event));
    if (result.prevent && event.cancelable !== false) event.preventDefault();
  }, { passive: false });
  let lastTouch = -Infinity;
  const stamp = () => { lastTouch = Date.now(); };
  wire("touchstart", (p) => { stamp(); return tracker.start(p); });
  wire("touchmove", (p) => tracker.move(p));
  wire("touchend", (p) => { stamp(); return tracker.end(p); });
  wire("touchcancel", () => { tracker.cancel(); return NOTHING; });
  // After a tap the browser also fires an emulated mouse click (and a dblclick after two taps).
  // The gestures above already handled the touch, and the chart's click would PIN the cursor
  // that the tap only meant to place — so a click right after a touch is swallowed here, in the
  // capture phase, before the chart's own click handler sees it. Real mouse clicks are untouched.
  for (const type of ["click", "dblclick"]) {
    element.addEventListener(type, (event) => {
      if (Date.now() - lastTouch < SYNTHETIC_CLICK_MS) {
        event.stopImmediatePropagation();
        event.preventDefault();
      }
    }, true);
  }
  return tracker;
}
