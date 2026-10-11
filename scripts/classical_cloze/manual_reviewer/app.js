let current = null;
let busy = false;

const el = id => document.getElementById(id);

async function api(path, body) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: {"Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

function setBusy(value, message = "") {
  busy = value;
  document.querySelectorAll("button").forEach(button => button.disabled = value);
  el("status").textContent = message;
}

function renderCurrent(data) {
  current = data.note || null;
  const p = data.progress;
  el("progress").textContent = `${p.pending} pending · ${p.completed} completed · ${p.skipped} skipped`;
  if (data.done) {
    el("front").textContent = "Campaign complete";
    el("back").textContent = "No pending notes remain.";
    return;
  }
  el("language").textContent = current.language;
  el("back").innerHTML = current.back;
  el("target").value = "";
  el("front").innerHTML = "";
  const parts = current.front.split(/([^\p{L}\p{M}’']+)/u);
  parts.forEach(part => {
    if (/^[\p{L}\p{M}’']+$/u.test(part)) {
      const button = document.createElement("button");
      button.className = "token";
      button.textContent = part;
      button.onclick = () => {
        document.querySelectorAll(".token").forEach(token => token.classList.remove("selected"));
        button.classList.add("selected");
        el("target").value = part;
      };
      el("front").appendChild(button);
    } else {
      el("front").appendChild(document.createTextNode(part));
    }
  });
  el("candidates").className = "candidates empty";
  el("candidates").textContent = "Select a target and search.";
}

function clozeHtml(value) {
  const escaped = value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  return escaped.replace(/\{\{c1::(.*?)\}\}/g, '<span class="cloze">$1</span>');
}

function renderCandidates(candidates, suggestions = []) {
  const container = el("candidates");
  container.innerHTML = "";
  container.className = "candidates";
  if (!candidates.length) {
    container.className = "candidates empty";
    if (!suggestions.length) {
      container.textContent = "No corpus examples or related lemmas found.";
      return;
    }
    const message = document.createElement("p");
    message.textContent = "No exact examples. Try a related GLAUx lemma:";
    container.appendChild(message);
    const choices = document.createElement("div");
    choices.className = "suggestions";
    for (const lemma of suggestions) {
      const button = document.createElement("button");
      button.className = "suggestion";
      button.textContent = lemma;
      button.onclick = () => {
        el("target").value = lemma;
        el("search").click();
      };
      choices.appendChild(button);
    }
    container.appendChild(choices);
    return;
  }
  for (const candidate of candidates) {
    const fragment = el("candidate-template").content.cloneNode(true);
    fragment.querySelector(".sentence").innerHTML = clozeHtml(candidate.cloze);
    const interpretation = candidate.search_note ? ` · ${candidate.search_note}` : "";
    fragment.querySelector(".source").textContent = `${candidate.author}, ${candidate.work}, ${candidate.citation} · ${candidate.corpus}${interpretation}`;
    const translation = fragment.querySelector("textarea");
    translation.value = candidate.translation || current.back.replace(/<[^>]+>/g, " ").trim();
    fragment.querySelector(".approve").onclick = async () => {
      if (busy || !confirm("Create and immediately activate this replacement card?")) return;
      try {
        setBusy(true, "Generating audio and creating card…");
        const result = await api("/api/approve", {
          note_id: current.id,
          candidate_id: candidate.id,
          translation: translation.value
        });
        renderCurrent(result.current);
        el("status").textContent = `Created Anki note ${result.replacement_note_id}`;
      } catch (error) {
        el("status").textContent = error.message;
      } finally {
        setBusy(false, el("status").textContent);
      }
    };
    container.appendChild(fragment);
  }
}

el("search").onclick = async () => {
  if (!current || busy) return;
  try {
    setBusy(true, "Searching corpus…");
    const data = await api("/api/search", {note_id: current.id, target: el("target").value});
    renderCandidates(data.candidates, data.suggestions);
    el("status").textContent = data.candidates.length
      ? `${data.candidates.length} examples`
      : `${data.suggestions.length} related lemmas`;
  } catch (error) {
    el("status").textContent = error.message;
  } finally {
    setBusy(false, el("status").textContent);
  }
};

async function skip(reason) {
  if (!current || busy) return;
  try {
    setBusy(true, "Saving…");
    renderCurrent(await api("/api/skip", {note_id: current.id, reason}));
    el("status").textContent = "";
  } catch (error) {
    el("status").textContent = error.message;
  } finally {
    setBusy(false, el("status").textContent);
  }
}

el("skip").onclick = () => skip("review_later");
el("unusable").onclick = () => skip("unusable");
el("target").addEventListener("keydown", event => {
  if (event.key === "Enter") el("search").click();
});

api("/api/current").then(renderCurrent).catch(error => el("status").textContent = error.message);
