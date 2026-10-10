// pages/analysis_compare.js — the "Runde vergleichen" dialog (V0.9.1).
//
//   Vergleichen mit:  ○ Garage16 Best   ○ Team Best   ○ Meine Beste   ○ Lap ID [____]
//
// The server decides what each choice means (server/analysis_reference.py); this file
// only asks, and shows the answer — a missing reference is a plain message, never a
// substitute lap. Lap IDs are checked on the server too (permissions, file, same
// track): the dialog never opens an analysis for a lap that failed that check, and
// it never shows a raw error, only the sentences below.

import { api, ApiError } from "../api.js";
import { el } from "../format.js";
import { T } from "../analysis/strings.js";

const KINDS = ["wr", "team_best", "my_best", "lap_id"];
const MAX_LAP_ID = 2147483647;

// Server reason code -> the sentence shown. Team Best always says "Kein Team Best verfügbar."
export function referenceMessage(kind, reason) {
  if (kind === "wr" && (reason === "no_lap" || !reason)) return T.noWr;
  if (kind === "team_best" && reason === "no_team") return T.noReference.no_team;
  if (kind === "team_best" && (reason === "no_team_lap" || reason === "no_lap")) return T.noReference.no_team_lap;
  return T.noReference[reason] || T.noReference.no_lap;
}

// An error from the lap lookup -> one safe sentence.
export function lapLookupMessage(err) {
  const status = err instanceof ApiError || (err && typeof err.status === "number") ? err.status : null;
  if (status === 404) return T.lapNotFound;
  if (status === 422 || status === 400) return T.lapNotAnalysable;
  if (status === 429) return T.tooManyRequests;
  if (status === 401 || status === 403) return T.signInAgain;
  return T.genericError;
}

// Whole numbers only, 1..2^31-1; null for anything else.
export function parseLapId(text) {
  const trimmed = String(text ?? "").trim();
  if (!/^\d{1,10}$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return value >= 1 && value <= MAX_LAP_ID ? value : null;
}

/**
 * lapA      header of the lap being analysed ({ id, track_name, ... })
 * onCompare (lapBId, note?) => void   called once a valid comparison lap is known
 * -> { element, close() }
 */
export function openCompareDialog({ lapA, onCompare, parent = document.body }) {
  const previouslyFocused = document.activeElement;
  let busy = false;
  let pendingNext = null; // a reference that is "the next-fastest lap" and needs a second, explicit click

  const message = el("div", { class: "compare-dialog__message", role: "status", "aria-live": "polite" });
  const radios = KINDS.map((kind) => {
    const input = el("input", { type: "radio", name: "compare_kind", value: kind, id: `compare_${kind}` });
    return { kind, input, row: el("label", { class: "compare-dialog__option", for: `compare_${kind}` }, [
      input, " ", el("strong", {}, T.kinds[kind].label), el("div", { class: "compare-dialog__hint" }, T.kinds[kind].hint),
    ]) };
  });
  radios[0].input.checked = true;

  const lapIdInput = el("input", { type: "text", inputmode: "numeric", id: "compare_lap_id_value", name: "lap_id", autocomplete: "off", maxlength: "10", placeholder: "z. B. 1234" });
  const lapIdRow = el("div", { class: "compare-dialog__lapid" }, [el("label", { for: "compare_lap_id_value" }, T.lapIdLabel), " ", lapIdInput]);
  lapIdRow.hidden = true;

  const go = el("button", { type: "button", class: "btn compare-dialog__go" }, T.compareGo);
  const cancel = el("button", { type: "button", class: "btn btn--ghost compare-dialog__cancel" }, T.cancel);
  const box = el("div", { class: "compare-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "compare_title" }, [
    el("h2", { id: "compare_title" }, T.compareTitle),
    el("div", { class: "compare-dialog__options" }, [...radios.map((r) => r.row), lapIdRow]),
    message,
    el("div", { class: "inline-form compare-dialog__actions" }, [go, cancel]),
  ]);
  const overlay = el("div", { class: "compare-overlay", "data-compare-dialog": "1" }, box);

  const selectedKind = () => (radios.find((r) => r.input.checked) || radios[0]).kind;
  const say = (text, error = true) => {
    message.textContent = text;
    message.className = `compare-dialog__message${text ? (error ? " compare-dialog__message--error" : " compare-dialog__message--info") : ""}`;
  };
  const setBusy = (on) => {
    busy = on;
    go.disabled = on;
    for (const r of radios) r.input.disabled = on;
    lapIdInput.disabled = on;
    if (on) say(T.checking, false);
  };

  function close() {
    document.removeEventListener("keydown", onKey, true);
    overlay.remove();
    if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
  }
  function finish(lapId, note) {
    close();
    onCompare(lapId, note);
  }
  function onKey(event) {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  }

  function reset() {
    pendingNext = null;
    go.textContent = T.compareGo;
    say("");
    lapIdRow.hidden = selectedKind() !== "lap_id";
  }
  for (const r of radios) r.input.addEventListener("change", () => {
    reset();
    if (r.kind === "lap_id") lapIdInput.focus();
  });

  async function submit() {
    if (busy) return;
    if (pendingNext) return finish(pendingNext.lapId, pendingNext.note);
    const kind = selectedKind();
    say("");

    if (kind === "lap_id") {
      const id = parseLapId(lapIdInput.value);
      if (id === null) return say(T.lapIdInvalid);
      if (id === lapA.id) return say(T.lapSame);
      setBusy(true);
      try {
        const info = await api.analysisLap(id);
        if (info && info.lap && info.lap.track_name !== lapA.track_name) {
          setBusy(false);
          return say(T.lapOtherTrack);
        }
        setBusy(false);
        return finish(id);
      } catch (err) {
        setBusy(false);
        return say(lapLookupMessage(err));
      }
    }

    setBusy(true);
    try {
      const ref = await api.analysisReference({ kind, lapId: lapA.id });
      setBusy(false);
      if (!ref.found || !ref.lap) return say(referenceMessage(kind, ref.reason));
      if (ref.already_best) {
        pendingNext = { lapId: ref.lap.id, note: `already_best_${kind}` };
        go.textContent = T.useNext;
        return say(T.alreadyBest[kind], false);
      }
      return finish(ref.lap.id);
    } catch (err) {
      setBusy(false);
      say(lapLookupMessage(err));
    }
  }

  go.addEventListener("click", submit);
  lapIdInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    }
  });
  cancel.addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  document.addEventListener("keydown", onKey, true);

  parent.appendChild(overlay);
  radios[0].input.focus();
  return { element: overlay, close };
}
