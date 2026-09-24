(() => {
  "use strict";

  const state = {
    payload: null,
    cueIndex: 0,
    selection: null,
    dragAnchor: null,
    dragging: false,
    player: null,
    stopAtSeconds: null,
    statusTimer: null,
    currentMediaId: null,
    token: null,
  };

  const byId = (id) => document.getElementById(id);

  function sliceText(text, start, end) {
    return Array.from(text).slice(start, end).join("");
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-Flashcards-Token", state.token);
    const response = await fetch(path, { ...options, headers });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  }

  function project() {
    return state.payload.project;
  }

  function cue() {
    return project().utterances[state.cueIndex];
  }

  function showStatus(message, isError = false) {
    const status = byId("status");
    status.textContent = message;
    status.className = `status visible${isError ? " error" : ""}`;
    window.clearTimeout(state.statusTimer);
    state.statusTimer = window.setTimeout(() => {
      status.className = "status";
    }, isError ? 6000 : 3200);
  }

  function formatTime(milliseconds) {
    const totalSeconds = milliseconds / 1000;
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = Math.floor(totalSeconds % 60);
    const millis = Math.floor(milliseconds % 1000);
    return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  }

  function setSelection(start, end) {
    const count = cue().tokens.length;
    const boundedStart = Math.max(0, Math.min(start, count - 1));
    const boundedEnd = Math.max(boundedStart + 1, Math.min(end, count));
    state.selection = { start: boundedStart, end: boundedEnd };
    renderSelection();
  }

  function clearSelection() {
    state.selection = null;
    renderSelection();
  }

  function selectedSpan() {
    if (!state.selection) return null;
    const tokens = cue().tokens;
    return {
      start: tokens[state.selection.start].span.start_char,
      end: tokens[state.selection.end - 1].span.end_char,
    };
  }

  function renderSelection() {
    document.querySelectorAll(".token").forEach((token, index) => {
      const selected = state.selection && index >= state.selection.start && index < state.selection.end;
      token.classList.toggle("selected", Boolean(selected));
    });
    const span = selectedSpan();
    if (!span) {
      byId("clozePreview").textContent = "Select one or more tokens.";
    } else {
      const text = cue().text;
      byId("clozePreview").textContent = `${sliceText(text, 0, span.start)}{{c1::${sliceText(text, span.start, span.end)}}}${sliceText(text, span.end)}`;
    }
    updateBoundaryButtons();
  }

  function updateBoundaryButtons() {
    const selection = state.selection;
    const count = cue().tokens.length;
    byId("extendLeft").disabled = !selection || selection.start === 0;
    byId("shrinkLeft").disabled = !selection || selection.end - selection.start <= 1;
    byId("shrinkRight").disabled = !selection || selection.end - selection.start <= 1;
    byId("extendRight").disabled = !selection || selection.end === count;
    byId("clearSelection").disabled = !selection;
  }

  function renderTokens() {
    const mount = byId("transcript");
    mount.replaceChildren();
    const current = cue();
    const textLength = Array.from(current.text).length;
    let cursor = 0;
    current.tokens.forEach((token, index) => {
      if (token.span.start_char > cursor) {
        mount.append(document.createTextNode(sliceText(current.text, cursor, token.span.start_char)));
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "token";
      button.textContent = token.text;
      button.dataset.index = String(index);
      button.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        state.dragging = true;
        state.dragAnchor = event.shiftKey && state.selection ? state.selection.start : index;
        setSelection(Math.min(state.dragAnchor, index), Math.max(state.dragAnchor, index) + 1);
      });
      button.addEventListener("pointerenter", () => {
        if (!state.dragging) return;
        setSelection(Math.min(state.dragAnchor, index), Math.max(state.dragAnchor, index) + 1);
      });
      mount.append(button);
      cursor = token.span.end_char;
    });
    if (cursor < textLength) mount.append(document.createTextNode(sliceText(current.text, cursor)));
    renderSelection();
  }

  function cueCards() {
    return project().cards.filter((card) => card.utterance_id === cue().id);
  }

  function unitForCard(card) {
    return project().learning_units.find(
      (unit) => unit.utterance_id === card.utterance_id
        && unit.span.start_char === card.target_span.start_char
        && unit.span.end_char === card.target_span.end_char,
    );
  }

  function loadCard(card) {
    const tokens = cue().tokens;
    const start = tokens.findIndex((token) => token.span.start_char === card.target_span.start_char);
    const endIndex = tokens.findIndex((token) => token.span.end_char === card.target_span.end_char);
    if (start >= 0 && endIndex >= start) setSelection(start, endIndex + 1);
    byId("targetGloss").value = card.target_gloss || "";
    byId("sentenceTranslation").value = card.sentence_translation || "";
    byId("analysis").value = card.analysis || "";
    byId("tags").value = card.tags.join(", ");
    byId("unitKind").value = unitForCard(card)?.kind || "unknown";
    showStatus("Loaded card into the editor.");
  }

  async function deleteCard(card) {
    if (!window.confirm(`Delete the card for "${sliceText(card.text, card.target_span.start_char, card.target_span.end_char)}"?`)) return;
    try {
      state.payload = await request(`/api/cards/${card.id}`, { method: "DELETE" });
      renderCueCards();
      updateCardCount();
      showStatus("Card deleted.");
    } catch (error) {
      showStatus(error.message, true);
    }
  }

  function renderCueCards() {
    const mount = byId("cueCards");
    mount.replaceChildren();
    const cards = cueCards();
    if (!cards.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No cards saved from this cue.";
      mount.append(empty);
      return;
    }
    cards.forEach((card) => {
      const row = document.createElement("div");
      row.className = "saved-card";
      const main = document.createElement("div");
      main.className = "saved-card-main";
      const target = document.createElement("span");
      target.className = "saved-target";
      target.textContent = sliceText(card.text, card.target_span.start_char, card.target_span.end_char);
      const gloss = document.createElement("span");
      gloss.className = "saved-gloss";
      gloss.textContent = card.target_gloss || "No gloss";
      main.append(target, gloss);
      const actions = document.createElement("div");
      actions.className = "saved-actions";
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "button quiet";
      edit.textContent = "Edit";
      edit.addEventListener("click", () => loadCard(card));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button quiet danger-text";
      remove.textContent = "Delete";
      remove.addEventListener("click", () => deleteCard(card));
      actions.append(edit, remove);
      row.append(main, actions);
      mount.append(row);
    });
  }

  function resetForm() {
    byId("targetGloss").value = "";
    byId("sentenceTranslation").value = cue().translation || "";
    byId("analysis").value = "";
    byId("tags").value = "";
    byId("unitKind").value = "unknown";
  }

  function selectCue(index) {
    state.cueIndex = Math.max(0, Math.min(index, project().utterances.length - 1));
    state.selection = null;
    const current = cue();
    mountPlayer(current.media_id);
    byId("cueNumber").textContent = `Cue ${state.cueIndex + 1} / ${project().utterances.length}`;
    byId("cueTime").textContent = `${formatTime(current.start_ms)} - ${formatTime(current.end_ms)}`;
    byId("previousCue").disabled = state.cueIndex === 0;
    byId("nextCue").disabled = state.cueIndex === project().utterances.length - 1;
    renderTokens();
    resetForm();
    renderCueCards();
  }

  function playCurrentCue() {
    const current = cue();
    state.player.currentTime = current.start_ms / 1000;
    state.stopAtSeconds = current.end_ms / 1000;
    state.player.play().catch((error) => showStatus(error.message, true));
  }

  async function saveCard(event) {
    event.preventDefault();
    if (!state.selection) {
      showStatus("Select one or more transcript tokens first.", true);
      return;
    }
    const tags = byId("tags").value.split(",").map((tag) => tag.trim()).filter(Boolean);
    const payload = {
      utterance_id: cue().id,
      token_start: state.selection.start,
      token_end: state.selection.end,
      target_gloss: byId("targetGloss").value,
      sentence_translation: byId("sentenceTranslation").value,
      analysis: byId("analysis").value,
      kind: byId("unitKind").value,
      tags,
    };
    try {
      const result = await request("/api/cards", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.payload = {
        project: result.project,
        export_path: result.export_path,
        media_urls: result.media_urls,
      };
      renderCueCards();
      updateCardCount();
      showStatus("Card saved to the project manifest.");
    } catch (error) {
      showStatus(error.message, true);
    }
  }

  async function exportDeck() {
    const button = byId("exportButton");
    button.disabled = true;
    button.textContent = "Exporting...";
    try {
      const result = await request("/api/export", { method: "POST" });
      showStatus(`Exported ${result.card_count} cards to ${result.output_path}`);
    } catch (error) {
      showStatus(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "Export Anki deck";
    }
  }

  function findNextCue() {
    const query = byId("cueSearch").value.trim().toLocaleLowerCase();
    if (!query) return;
    const cues = project().utterances;
    for (let offset = 1; offset <= cues.length; offset += 1) {
      const index = (state.cueIndex + offset) % cues.length;
      if (cues[index].text.toLocaleLowerCase().includes(query)) {
        selectCue(index);
        return;
      }
    }
    showStatus(`No cue contains "${query}".`, true);
  }

  function updateCardCount() {
    const count = project().cards.length;
    byId("cardCount").textContent = `${count} card${count === 1 ? "" : "s"}`;
    byId("exportButton").disabled = count === 0;
  }

  function mountPlayer(mediaId) {
    if (state.player && state.currentMediaId === mediaId) return;
    if (state.player) {
      state.player.pause();
      state.player.removeAttribute("src");
      state.player.load();
    }
    state.stopAtSeconds = null;
    const source = project().media.find((item) => item.id === mediaId);
    if (!source) throw new Error(`Missing media source ${mediaId}.`);
    const extension = source.path.split(".").pop().toLocaleLowerCase();
    const isVideo = ["mp4", "mkv", "webm", "mov", "m4v"].includes(extension);
    const player = document.createElement(isVideo ? "video" : "audio");
    player.controls = true;
    player.preload = "metadata";
    const mediaUrl = new URL(state.payload.media_urls[source.id], window.location.origin);
    mediaUrl.searchParams.set("token", state.token);
    player.src = mediaUrl;
    state.player = player;
    state.currentMediaId = mediaId;
    player.addEventListener("timeupdate", () => {
      if (
        state.player === player
        && state.stopAtSeconds !== null
        && player.currentTime >= state.stopAtSeconds
      ) {
        player.pause();
        state.stopAtSeconds = null;
      }
    });
    byId("mediaMount").replaceChildren(player);
  }

  function bindEvents() {
    window.addEventListener("pointerup", () => {
      state.dragging = false;
      state.dragAnchor = null;
    });
    byId("previousCue").addEventListener("click", () => selectCue(state.cueIndex - 1));
    byId("nextCue").addEventListener("click", () => selectCue(state.cueIndex + 1));
    byId("playCue").addEventListener("click", playCurrentCue);
    byId("cardForm").addEventListener("submit", saveCard);
    byId("exportButton").addEventListener("click", exportDeck);
    byId("searchButton").addEventListener("click", findNextCue);
    byId("cueSearch").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        findNextCue();
      }
    });
    byId("clearSelection").addEventListener("click", clearSelection);
    byId("extendLeft").addEventListener("click", () => setSelection(state.selection.start - 1, state.selection.end));
    byId("extendRight").addEventListener("click", () => setSelection(state.selection.start, state.selection.end + 1));
    byId("shrinkLeft").addEventListener("click", () => setSelection(state.selection.start + 1, state.selection.end));
    byId("shrinkRight").addEventListener("click", () => setSelection(state.selection.start, state.selection.end - 1));
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        byId("cardForm").requestSubmit();
      }
    });
  }

  async function start() {
    bindEvents();
    try {
      const parameters = new URLSearchParams(window.location.search);
      const suppliedToken = parameters.get("token");
      if (suppliedToken) window.sessionStorage.setItem("flashcards-review-token", suppliedToken);
      state.token = suppliedToken || window.sessionStorage.getItem("flashcards-review-token");
      if (!state.token) throw new Error("Missing reviewer session token. Restart flashcards-review.");
      window.history.replaceState(null, "", window.location.pathname);
      state.payload = await request("/api/project");
      if (!project().utterances.length) throw new Error("This project has no transcript cues.");
      byId("projectTitle").textContent = project().title;
      document.documentElement.lang = project().language;
      if (["ar", "fa", "he", "ur"].includes(project().language.split("-")[0])) {
        byId("transcript").dir = "rtl";
      }
      updateCardCount();
      selectCue(0);
    } catch (error) {
      showStatus(error.message, true);
    }
  }

  start();
})();
