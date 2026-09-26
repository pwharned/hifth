package com.github.pwharned.flashcards.reviewer.frontend

import org.scalajs.dom

object ReviewerStyles:
  private val ElementId = "media-reviewer-styles"

  def install(): Unit =
    if dom.document.getElementById(ElementId) == null then
      val element = dom.document.createElement("style").asInstanceOf[dom.HTMLStyleElement]
      element.id = ElementId
      element.textContent = css
      dom.document.head.appendChild(element)

  private val css = """
:root {
  color-scheme: dark;
  --mr-bg: #111513;
  --mr-panel: #191f1b;
  --mr-panel-2: #202721;
  --mr-line: #384139;
  --mr-text: #f2eee4;
  --mr-muted: #a7aa9f;
  --mr-accent: #e2a84a;
  --mr-accent-ink: #241a09;
  --mr-error: #ee8a78;
  --mr-success: #89c99a;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  min-width: 280px;
  min-height: 100vh;
  background:
    radial-gradient(circle at 85% 0%, rgba(226, 168, 74, .09), transparent 34rem),
    var(--mr-bg);
  color: var(--mr-text);
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
}
button, input, select, textarea { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: default; opacity: .42; }

.mr-app { min-height: 100vh; }
.mr-header {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 2rem;
  padding: 1.5rem clamp(1rem, 4vw, 3.5rem);
  border-bottom: 1px solid var(--mr-line);
}
.mr-kicker, .fc-card-preview__label {
  color: var(--mr-accent);
  font-size: .68rem;
  font-weight: 750;
  letter-spacing: .14em;
  text-transform: uppercase;
}
.mr-title {
  margin: .3rem 0 0;
  font-family: Georgia, 'Times New Roman', serif;
  font-size: clamp(1.8rem, 4vw, 3rem);
  font-weight: 500;
  line-height: 1.05;
}
.mr-header-meta { display: flex; align-items: center; gap: .65rem; color: var(--mr-muted); font-size: .78rem; }
.mr-connection { padding: .35rem .55rem; border: 1px solid var(--mr-line); border-radius: 999px; }
.mr-connection--connected { color: var(--mr-success); border-color: #426d4c; }
.mr-connection--reconnecting { color: var(--mr-error); border-color: #73483f; }

.mr-workspace {
  width: min(1380px, 100%);
  margin: 0 auto;
  padding: clamp(1rem, 3vw, 2.4rem);
  display: grid;
  grid-template-columns: minmax(250px, .72fr) minmax(480px, 1.5fr);
  gap: 1.4rem;
  align-items: start;
}
.mr-panel { border: 1px solid var(--mr-line); background: rgba(25, 31, 27, .96); }
.mr-media-panel { position: sticky; top: 1rem; padding: 1rem; }
.mr-editor-panel { padding: clamp(1rem, 3vw, 2rem); }
.mr-audio { display: block; width: 100%; height: 46px; filter: sepia(.12) saturate(.85); }
.mr-audio-error { margin-top: .65rem; color: var(--mr-error); font-size: .78rem; }
.mr-cue-nav { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: .6rem; margin-top: 1rem; }
.mr-cue-nav .mr-button:last-child { justify-self: end; }
.mr-cue-position { text-align: center; font-size: .78rem; font-variant-numeric: tabular-nums; }
.mr-cue-position span { display: block; }
.mr-cue-time { margin-top: .2rem; color: var(--mr-muted); }

.mr-button {
  min-height: 2.4rem;
  padding: .55rem .8rem;
  border: 1px solid var(--mr-line);
  border-radius: 3px;
  background: var(--mr-panel-2);
  color: var(--mr-text);
}
.mr-button:not(:disabled):hover { border-color: #73796e; }
.mr-play { width: 100%; margin-top: .75rem; }
.mr-primary { border-color: var(--mr-accent); background: var(--mr-accent); color: var(--mr-accent-ink); font-weight: 800; }

.mr-section-heading { display: flex; align-items: end; justify-content: space-between; gap: 1rem; }
.mr-section-heading h2 { margin: .3rem 0 0; font-family: Georgia, serif; font-size: 1.45rem; font-weight: 500; }
.mr-instruction { color: var(--mr-muted); font-size: .78rem; text-align: right; }
.fc-selection-editor {
  width: 100%;
  min-height: 9rem;
  margin: 1.15rem 0 .65rem;
  padding: clamp(1rem, 3vw, 1.6rem);
  border: 1px solid var(--mr-line);
  outline: none;
  background: #121613;
  font-family: Georgia, 'Times New Roman', serif;
  font-size: clamp(1.55rem, 3.2vw, 2.35rem);
  line-height: 1.75;
  white-space: pre-wrap;
  user-select: text;
  cursor: text;
  resize: vertical;
}
.fc-selection-editor:focus { border-color: #77766a; }
.fc-selection-editor::selection { background: var(--mr-accent); color: var(--mr-accent-ink); }
.mr-selection-editor--locked { pointer-events: none; user-select: none; cursor: default; opacity: .58; }
.mr-selected { min-height: 1.3rem; color: var(--mr-muted); font-size: .78rem; }
.mr-selected strong { color: var(--mr-accent); font-weight: 650; }

.mr-deck-control { margin-top: 1rem; padding: .8rem; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: .55rem .75rem; border: 1px solid var(--mr-line); background: #151a16; }
.mr-deck-select { width: 100%; min-height: 2.4rem; padding: .45rem .65rem; border: 1px solid var(--mr-line); border-radius: 3px; outline: none; background: #121613; color: var(--mr-text); }
.mr-deck-select:focus { border-color: var(--mr-accent); }
.mr-deck-refresh { white-space: nowrap; }
.mr-deck-status { grid-column: 1 / -1; min-height: 1rem; color: var(--mr-muted); font-size: .72rem; }
.mr-deck-status--error { color: var(--mr-error); }
.mr-preparation-status { min-height: 2.4rem; margin: 1rem 0 .7rem; display: flex; align-items: center; justify-content: space-between; gap: .75rem; color: var(--mr-muted); font-size: .78rem; }
.mr-preparation-status--loading { color: var(--mr-accent); }
.mr-preparation-status--error { color: var(--mr-error); }
.mr-retry { min-height: 2rem; flex: none; padding-block: .35rem; }
.mr-prepared { display: grid; gap: .85rem; }
.mr-generated-audio-block { padding: .75rem .8rem; border: 1px solid var(--mr-line); background: #151a16; }
.mr-audio-label { display: block; margin-bottom: .45rem; color: var(--mr-muted); font-size: .75rem; font-weight: 650; }
.mr-generated-audio { display: block; width: 100%; height: 42px; }
.mr-fields { display: grid; grid-template-columns: minmax(0, .85fr) minmax(0, 1.3fr); gap: .85rem; }
.mr-field { display: grid; gap: .4rem; color: var(--mr-muted); font-size: .75rem; font-weight: 650; }
.mr-field input, .mr-field textarea {
  width: 100%;
  padding: .72rem .8rem;
  border: 1px solid var(--mr-line);
  border-radius: 3px;
  outline: none;
  background: #121613;
  color: var(--mr-text);
  resize: vertical;
}
.mr-field input:focus, .mr-field textarea:focus { border-color: var(--mr-accent); box-shadow: 0 0 0 2px rgba(226, 168, 74, .1); }

.fc-card-preview { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; margin: 1.25rem 0; border: 1px solid var(--mr-line); background: var(--mr-line); }
.fc-card-preview__side { min-width: 0; padding: .9rem 1rem; background: #151a16; }
.fc-card-preview__label { display: block; margin-bottom: .45rem; color: var(--mr-muted); }
.fc-card-preview__value { overflow-wrap: anywhere; line-height: 1.5; white-space: pre-wrap; }
.mr-create-row { margin-top: .9rem; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: stretch; gap: .75rem; }
.mr-create-row .mr-primary { min-width: 8.5rem; }
.mr-outcome { min-height: 2.4rem; padding: .6rem .75rem; display: flex; align-items: center; border: 1px solid var(--mr-line); border-left-width: 3px; background: #151a16; color: var(--mr-muted); font-size: .78rem; line-height: 1.4; }
.mr-outcome--success { border-color: #426d4c; color: var(--mr-success); }
.mr-outcome--duplicate { border-color: #7b612e; color: var(--mr-accent); }
.mr-outcome--error { border-color: #73483f; color: var(--mr-error); }
.mr-outcome--unknown { border-color: #8a6530; color: #efbd69; }

.mr-state { min-height: 100vh; display: grid; place-items: center; padding: 2rem; text-align: center; }
.mr-state-card { width: min(32rem, 100%); padding: 2rem; border: 1px solid var(--mr-line); background: var(--mr-panel); }
.mr-state-card h1 { margin: 0 0 .6rem; font-family: Georgia, serif; font-weight: 500; }
.mr-state-card p { margin: 0 0 1rem; color: var(--mr-muted); line-height: 1.5; }
.mr-state-card--error { border-color: #73483f; }

.mr-toast {
  position: fixed;
  z-index: 20;
  left: 50%;
  bottom: 1.25rem;
  transform: translateX(-50%);
  width: max-content;
  max-width: calc(100vw - 2rem);
  padding: .7rem .95rem;
  border: 1px solid var(--mr-line);
  background: #262d27;
  box-shadow: 0 12px 40px rgba(0, 0, 0, .35);
  font-size: .8rem;
}
.mr-toast--success { color: var(--mr-success); border-color: #426d4c; }
.mr-toast--duplicate { color: var(--mr-accent); border-color: #7b612e; }
.mr-toast--error { color: var(--mr-error); border-color: #73483f; }
.mr-toast--hidden { display: none; }

@media (max-width: 820px) {
  .mr-workspace { grid-template-columns: 1fr; }
  .mr-media-panel { position: static; }
}
@media (max-width: 560px) {
  .mr-header { align-items: start; flex-direction: column; gap: 1rem; }
  .mr-section-heading { align-items: start; flex-direction: column; }
  .mr-instruction { text-align: left; }
  .mr-fields, .fc-card-preview { grid-template-columns: 1fr; }
  .mr-deck-control, .mr-create-row { grid-template-columns: 1fr; }
  .mr-deck-refresh, .mr-create-row .mr-primary { width: 100%; }
  .mr-preparation-status { align-items: stretch; flex-direction: column; }
  .mr-retry { width: 100%; }
  .mr-cue-nav { grid-template-columns: auto 1fr auto; }
  .mr-button { padding-inline: .65rem; }
}
"""
