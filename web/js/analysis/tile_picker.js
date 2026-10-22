// analysis/tile_picker.js — choosing a tile and putting it on the grid (V0.9.5).
//
// Two ways, both from the same catalog (tile_catalog.js):
//
//   A) The side panel (edit mode): Wofür? → Art? → the tiles. A click on a tile opens the
//      preview: the REAL tile with the laps that are open, the ready-made sizes to choose from,
//      and then either drag it onto the grid (a ghost shows the cells it will take) or let it
//      be placed at the first free spot. A tile can also be dragged straight from the list by
//      its grip ⠿, at its standard size.
//
//   B) Draw a box on empty cells of the grid (grid.js → onDraw). The same catalog opens as an
//      overlay, but only with the tiles that fit into that box; pick one, see the preview at
//      exactly that size, put it there.
//
// Nothing here changes the layout itself: it calls onAdd(spec) with { type, channel?, x?, y?,
// w, h } and the page applies it (layout.js addWidget).

import { el } from "../format.js";
import { contentHeight, rowsToPx } from "./grid.js";
import { COLS, GAP_PX, sizeName } from "./layout.js";
import { presetsFor, tileTree } from "./tile_catalog.js";
import { T } from "./strings.js";

const DRAG_THRESHOLD_PX = 6;

// A small picture of a size: the box on a 12-column strip.
function sizeIcon(w, h) {
  const scale = 4;
  return el("span", { class: "tsize__icon", style: `width:${COLS * scale}px;height:${Math.min(h, 16) * scale}px;` },
    el("span", { class: "tsize__box", style: `width:${w * scale}px;height:${Math.min(h, 16) * scale}px;` }));
}

// ---------------------------------------------------------------- dragging a tile onto the grid

// Starts dragging `tile` (w x h cells) from `source`; the grid shows where it would land.
// onDrop(box) is called when it is let go over the grid; onEnd() always.
export function startTileDrag(event, { tile, w, h, getGrid, onDrop, onEnd }) {
  const source = event.currentTarget || event.target;
  if (source && source.setPointerCapture && event.pointerId !== undefined) {
    try { source.setPointerCapture(event.pointerId); } catch { /* synthetic events */ }
  }
  const proxy = el("div", { class: "tdrag", "aria-hidden": "true" }, `${tile.title} · ${w} × ${h}`);
  document.body.appendChild(proxy);
  let box = null;
  const move = (e) => {
    proxy.style.left = `${e.clientX + 12}px`;
    proxy.style.top = `${e.clientY + 12}px`;
    const grid = getGrid();
    if (!grid) return;
    box = grid.boxAt(e.clientX, e.clientY, w, h);
    if (box) grid.showGhost(box, grid.stateOf(box), `${tile.title} · ${w} × ${h}`);
    else grid.hideGhost();
  };
  const end = (commit) => {
    source.removeEventListener("pointermove", move);
    source.removeEventListener("pointerup", up);
    source.removeEventListener("pointercancel", cancel);
    window.removeEventListener("keydown", key, true);
    proxy.remove();
    const grid = getGrid();
    if (grid) grid.hideGhost();
    if (commit && box) onDrop(box);
    if (onEnd) onEnd();
  };
  const up = () => end(true);
  const cancel = () => end(false);
  const key = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      end(false);
    }
  };
  source.addEventListener("pointermove", move);
  source.addEventListener("pointerup", up);
  source.addEventListener("pointercancel", cancel);
  window.addEventListener("keydown", key, true);
  move(event);
}

// ---------------------------------------------------------------- the tree (Wofür → Art → Kachel)

// Renders the catalog tree into `host`. opts: { tiles, fit, getLayout, validChannels, query,
// onSelect(tile), onGrip(event, tile) (only without fit), openState: Set of open branch ids }
function renderTree(host, opts) {
  host.innerHTML = "";
  const tree = tileTree(opts.tiles, { fit: opts.fit, layout: opts.getLayout(), validChannels: opts.validChannels, query: opts.query });
  if (!tree.length) {
    host.appendChild(el("p", { class: "subtitle tpick__empty" }, opts.fit ? T.pickerNothingFits : T.pickerNothingFound));
    return;
  }
  const searching = !!(opts.query && opts.query.trim());
  for (const purpose of tree) {
    const purposeId = `p:${purpose.id}`;
    const kinds = el("div", { class: "tpick__kinds" });
    for (const kind of purpose.kinds) {
      const kindId = `${purposeId}/k:${kind.id}`;
      const list = el("ul", { class: "tpick__tiles" }, kind.tiles.map((tile) => tileItem(tile, opts)));
      const kindNode = el("details", { class: "tpick__kind", "data-kind": kind.id }, [
        el("summary", { title: kind.hint }, [kind.label, el("span", { class: "tpick__count" }, String(kind.tiles.length))]),
        list,
      ]);
      if (searching || opts.openState.has(kindId) || purpose.kinds.length === 1) kindNode.open = true;
      kindNode.addEventListener("toggle", () => (kindNode.open ? opts.openState.add(kindId) : opts.openState.delete(kindId)));
      kinds.appendChild(kindNode);
    }
    const node = el("details", { class: "tpick__purpose", "data-purpose": purpose.id }, [
      el("summary", {}, [el("span", { class: "tpick__plabel" }, purpose.label), el("span", { class: "tpick__count" }, String(purpose.count)), el("small", { class: "tpick__phint" }, purpose.hint)]),
      kinds,
    ]);
    if (searching || opts.openState.has(purposeId) || (opts.fit && tree.length <= 3)) node.open = true;
    node.addEventListener("toggle", () => (node.open ? opts.openState.add(purposeId) : opts.openState.delete(purposeId)));
    host.appendChild(node);
  }
}

function tileItem(tile, opts) {
  const title = tile.unit ? `${tile.title} (${tile.unit})` : tile.title;
  const pick = el("button", {
    type: "button", class: "tpick__tile", "data-tile": tile.key, disabled: tile.taken ? "disabled" : null,
    title: tile.taken ? T.pickerTaken : tile.description,
  }, [el("span", { class: "tpick__tname" }, title), tile.taken ? el("small", { class: "tpick__taken" }, T.pickerTaken) : null]);
  pick.addEventListener("click", () => opts.onSelect(tile));
  const children = [pick];
  if (opts.onGrip && !tile.taken) {
    const grip = el("span", { class: "tpick__grip", title: T.pickerDragHint, "aria-hidden": "true", "data-grip": tile.key }, "⠿");
    let pending = null;
    grip.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      event.preventDefault();
      pending = { x: event.clientX, y: event.clientY };
      if (grip.setPointerCapture && event.pointerId !== undefined) {
        try { grip.setPointerCapture(event.pointerId); } catch { /* synthetic events */ }
      }
    });
    grip.addEventListener("pointermove", (event) => {
      if (!pending) return;
      if (Math.hypot(event.clientX - pending.x, event.clientY - pending.y) < DRAG_THRESHOLD_PX) return;
      pending = null;
      opts.onGrip(event, tile, grip);
    });
    grip.addEventListener("pointerup", () => { pending = null; });
    grip.addEventListener("pointercancel", () => { pending = null; });
    children.unshift(grip);
  }
  return el("li", { class: `tpick__item${tile.taken ? " tpick__item--taken" : ""}` }, children);
}

// ---------------------------------------------------------------- the preview

// The preview of `tile` at w x h: the real tile, rendered by the page (renderPreview), as wide
// as the dialog allows (charts and maps adapt to the width; the height is scaled the same).
function previewStage(tile, w, h, { renderPreview, getGrid, maxWidth }) {
  const stage = el("div", { class: "tprev__stage", "data-size": `${w}x${h}` });
  const grid = getGrid();
  const gridWidth = grid && grid.element && grid.element.getBoundingClientRect ? grid.element.getBoundingClientRect().width : 0;
  const colWidth = gridWidth > 0 ? (gridWidth - GAP_PX * (COLS - 1)) / COLS : 90;
  const realWidth = colWidth * w + GAP_PX * (w - 1);
  const width = Math.max(Math.min(realWidth, maxWidth), 220);
  const scale = Math.min(width / realWidth, 1);
  const height = Math.max(Math.round(rowsToPx(h) * scale), 90);
  stage.style.width = `${Math.round(width)}px`;
  stage.style.height = `${height}px`;
  const body = el("div", { class: "awin awin--preview awin__preview" }, el("div", { class: "subtitle" }, T.pickerLoading));
  stage.appendChild(body);
  let handle = null;
  let gone = false;
  Promise.resolve()
    .then(() => renderPreview({ type: tile.type, channel: tile.channel, mode: tile.type === "trackmap" ? "delta" : undefined, x: 0, y: 0, w, h, size: sizeName(w), id: "preview", boxHeight: Math.max(Math.round(contentHeight(h) * scale), 60) }))
    .then((result) => {
      if (gone) {
        if (result) result.destroy();
        return;
      }
      handle = result;
      body.innerHTML = "";
      body.appendChild(result.element);
    })
    .catch(() => {
      if (gone) return;
      body.innerHTML = "";
      body.appendChild(el("div", { class: "notice notice--error" }, T.pickerPreviewFailed));
    });
  return {
    element: stage,
    destroy() {
      gone = true;
      if (handle) {
        try { handle.destroy(); } catch { /* ignore */ }
      }
    },
  };
}

/**
 * The dialog: either the overlay chooser for a drawn box (fit) followed by the preview, or the
 * preview of one tile picked in the side panel.
 *
 *   tiles, getLayout, validChannels, renderPreview, getGrid
 *   tile?          open straight at this tile's preview
 *   fit?           {x, y, w, h} — the drawn box
 *   onAdd(spec)    put a tile on the grid
 *   onClose()
 */
export function openTileDialog(opts) {
  const { fit } = opts;
  const backdrop = el("div", { class: "tprev-backdrop", "data-tile-dialog": fit ? "fit" : "preview" });
  const dialog = el("div", { class: "tprev card", role: "dialog", "aria-modal": "true", "aria-label": fit ? T.pickerChooseFor : T.pickerPreview });
  backdrop.appendChild(dialog);
  let current = null; // the preview stage
  const openState = new Set();
  let query = "";

  const close = () => {
    if (current) current.destroy();
    window.removeEventListener("keydown", onKey, true);
    backdrop.remove();
    if (opts.onClose) opts.onClose();
  };
  const onKey = (event) => {
    if (event.key === "Escape" && !backdrop.classList.contains("tprev-backdrop--dragging")) {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  };
  backdrop.addEventListener("pointerdown", (event) => { if (event.target === backdrop) close(); });
  window.addEventListener("keydown", onKey, true);

  const header = (title, back) => el("div", { class: "tprev__head" }, [
    back ? el("button", { type: "button", class: "btn btn--ghost tprev__back", onclick: back }, `← ${T.pickerBack}`) : null,
    el("h2", {}, title),
    el("button", { type: "button", class: "btn btn--ghost tprev__close", "aria-label": T.close, title: T.close, onclick: close }, "✕"),
  ]);

  // --- step 1 (drawn box only): which tile goes here?
  function showChooser() {
    if (current) current.destroy();
    current = null;
    dialog.innerHTML = "";
    const treeHost = el("div", { class: "tpick__tree tprev__tree" });
    const search = el("input", { type: "search", class: "tpick__search", placeholder: T.pickerSearch, "aria-label": T.pickerSearch, value: query });
    const draw = () => renderTree(treeHost, { tiles: opts.tiles, fit, getLayout: opts.getLayout, validChannels: opts.validChannels, query, openState, onSelect: showPreview });
    search.addEventListener("input", () => { query = search.value; draw(); });
    dialog.append(
      header(T.pickerChooseFor),
      el("p", { class: "subtitle tprev__fit" }, [sizeIcon(fit.w, fit.h), ` ${T.pickerFitInfo.replace("{0}", `${fit.w} × ${fit.h}`)}`]),
      search,
      treeHost,
    );
    draw();
    search.focus && search.focus();
  }

  // --- step 2: the preview, the size, and where it goes
  function showPreview(tile) {
    if (current) current.destroy();
    current = null;
    dialog.innerHTML = "";
    const presets = fit ? [[fit.w, fit.h]] : presetsFor(tile);
    let [w, h] = fit ? [fit.w, fit.h] : (presets.find(([pw, ph]) => pw === tile.def[0] && ph === tile.def[1]) || presets[0]);
    const stageSlot = el("div", { class: "tprev__stage-slot" });
    const maxWidth = Math.min((typeof window !== "undefined" && window.innerWidth) || 900, 960) - 64;
    const drawStage = () => {
      if (current) current.destroy();
      stageSlot.innerHTML = "";
      current = previewStage(tile, w, h, { renderPreview: opts.renderPreview, getGrid: opts.getGrid, maxWidth });
      stageSlot.appendChild(current.element);
    };
    const sizeButtons = el("div", { class: "tsize", role: "radiogroup", "aria-label": T.size }, presets.map(([pw, ph]) => {
      const button = el("button", {
        type: "button", class: "tsize__option", role: "radio", "data-size": `${pw}x${ph}`,
        "aria-checked": pw === w && ph === h ? "true" : "false",
      }, [sizeIcon(pw, ph), el("span", {}, `${pw} × ${ph}`)]);
      button.addEventListener("click", () => {
        w = pw;
        h = ph;
        sizeButtons.querySelectorAll(".tsize__option").forEach((b) => b.setAttribute("aria-checked", b === button ? "true" : "false"));
        drawStage();
      });
      return button;
    }));
    const add = (box) => {
      const spec = { type: tile.type, w: box.w, h: box.h, x: box.x, y: box.y };
      if (tile.channel) spec.channel = tile.channel;
      close();
      opts.onAdd(spec);
    };
    const actions = [];
    if (fit) {
      actions.push(el("button", { type: "button", class: "btn tprev__place", onclick: () => add(fit) }, T.pickerPlaceHere));
    } else {
      const grid = opts.getGrid();
      if (grid && !grid.isStacked()) {
        const dragChip = el("button", { type: "button", class: "btn tprev__drag", title: T.pickerDragHint }, `⠿ ${T.pickerDrag}`);
        dragChip.addEventListener("pointerdown", (event) => {
          if (event.pointerType === "mouse" && event.button !== 0) return;
          event.preventDefault();
          backdrop.classList.add("tprev-backdrop--dragging");
          startTileDrag(event, {
            tile, w, h, getGrid: opts.getGrid,
            onDrop: (box) => add(box),
            onEnd: () => backdrop.classList.remove("tprev-backdrop--dragging"),
          });
        });
        actions.push(dragChip);
      }
      actions.push(el("button", { type: "button", class: "btn btn--ghost tprev__auto", onclick: () => {
        const spec = { type: tile.type, w, h };
        if (tile.channel) spec.channel = tile.channel;
        close();
        opts.onAdd(spec);
      } }, T.pickerAuto));
    }
    actions.push(el("button", { type: "button", class: "btn btn--ghost tprev__cancel", onclick: close }, T.cancel));
    dialog.append(
      header(tile.unit ? `${tile.title} (${tile.unit})` : tile.title, fit ? showChooser : null),
      el("p", { class: "subtitle tprev__desc" }, tile.description),
      el("div", { class: "tprev__sizes" }, [el("strong", {}, fit ? `${T.pickerDrawnSize}: ` : `${T.size}: `), fit ? el("span", {}, `${w} × ${h}`) : sizeButtons]),
      stageSlot,
      el("div", { class: "inline-form tprev__actions" }, actions),
      fit ? null : el("p", { class: "subtitle tprev__hint" }, T.pickerDropHint),
    );
    drawStage();
  }

  document.body.appendChild(backdrop);
  if (fit) showChooser();
  else showPreview(opts.tile);
  return { close, element: backdrop };
}

/**
 * The side panel (edit mode). Lives across layout changes: update() refreshes which tiles are
 * already on the grid without closing what the user had opened.
 *
 *   tiles, getLayout, validChannels, renderPreview, getGrid, onAdd(spec)
 * -> { element, update(), destroy() }
 */
export function createTilePicker(opts) {
  const openState = new Set();
  let query = "";
  let dialog = null;
  const treeHost = el("div", { class: "tpick__tree" });
  const search = el("input", { type: "search", class: "tpick__search", placeholder: T.pickerSearch, "aria-label": T.pickerSearch });
  const openPreview = (tile) => {
    if (dialog) dialog.close();
    dialog = openTileDialog({ ...opts, tile, onClose: () => { dialog = null; } });
  };
  const onGrip = (event, tile) => {
    const grid = opts.getGrid();
    if (!grid || grid.isStacked()) return;
    startTileDrag(event, {
      tile, w: tile.def[0], h: tile.def[1], getGrid: opts.getGrid,
      onDrop: (box) => {
        const spec = { type: tile.type, ...box };
        if (tile.channel) spec.channel = tile.channel;
        opts.onAdd(spec);
      },
    });
  };
  const draw = () => renderTree(treeHost, { tiles: opts.tiles, fit: null, getLayout: opts.getLayout, validChannels: opts.validChannels, query, openState, onSelect: openPreview, onGrip });
  search.addEventListener("input", () => { query = search.value; draw(); });
  const element = el("aside", { class: "card tpick", "data-picker": "1", "aria-label": T.pickerTitle }, [
    el("h2", {}, T.pickerTitle),
    el("p", { class: "subtitle tpick__intro" }, T.pickerIntro),
    search,
    el("div", { class: "tpick__crumbs subtitle" }, T.pickerPath),
    treeHost,
  ]);
  draw();
  return {
    element,
    update: draw,
    // option B: a box was drawn on the grid
    chooseFor(fit) {
      if (dialog) dialog.close();
      dialog = openTileDialog({ ...opts, fit, onClose: () => { dialog = null; const grid = opts.getGrid(); if (grid) grid.hideGhost(); } });
      return dialog;
    },
    destroy() {
      if (dialog) dialog.close();
      element.remove();
    },
  };
}
