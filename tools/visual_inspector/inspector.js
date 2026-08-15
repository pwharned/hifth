/**
 * inspector.js
 * ------------
 * Drives the Quran Alignment Inspector UI.
 *
 * Responsibilities:
 *   - Accept WAV and JSON files via file picker
 *   - Render Arabic word tokens grouped by Ayah
 *   - Drive a requestAnimationFrame loop that highlights the
 *     active word in real time based on audio.currentTime vs timestamps
 *   - Update stats bar and progress bar on every frame
 */
const Inspector = (() => {
    // ── State ─────────────────────────────────────────────────────────────
    let alignmentData  = null;   // Parsed JSON from verified file
    let audioObjectUrl = null;   // Revokeable URL for loaded WAV
    let rafHandle      = null;   // requestAnimationFrame handle
    let activeIndex    = -1;     // Index of currently highlighted word
    // ── DOM refs ──────────────────────────────────────────────────────────
    const audioEl      = document.getElementById("audioEl");
    const playBtn      = document.getElementById("playBtn");
    const playIcon     = document.getElementById("playIcon");
    const progressFill = document.getElementById("progressFill");
    const currentTime  = document.getElementById("currentTime");
    const totalTime    = document.getElementById("totalTime");
    const verseDisplay = document.getElementById("verseDisplay");
    const statusBadge  = document.getElementById("statusBadge");
    const audioBtn     = document.getElementById("audioBtn");
    const jsonBtn      = document.getElementById("jsonBtn");
    const statSurah  = document.getElementById("statSurah");
    const statWords  = document.getElementById("statWords");
    const statActive = document.getElementById("statActive");
    const statAyah   = document.getElementById("statAyah");
    const statScore  = document.getElementById("statScore");
    // Icons as SVG path strings
    const ICON_PLAY  = "M4 2l10 6-10 6V2z";
    const ICON_PAUSE = "M4 2h3v12H4V2zm5 0h3v12H9V2z";
    // ── File Loading ──────────────────────────────────────────────────────
    /**
     * Wires up file input listeners.
     * Called once on init.
     */
    function bindFileInputs() {
        document.getElementById("audioInput").addEventListener("change", e => {
            const file = e.target.files[0];
            if (!file) return;
            // Revoke any previous object URL to avoid memory leaks
            if (audioObjectUrl) URL.revokeObjectURL(audioObjectUrl);
            audioObjectUrl  = URL.createObjectURL(file);
            audioEl.src     = audioObjectUrl;
            audioBtn.textContent = file.name;
            audioBtn.classList.add("loaded");
            audioEl.addEventListener("loadedmetadata", () => {
                totalTime.textContent = formatTime(audioEl.duration * 1000);
                tryEnablePlayback();
            }, { once: true });
        });
        document.getElementById("jsonInput").addEventListener("change", e => {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = event => {
                try {
                    alignmentData = JSON.parse(event.target.result);
                    validateJsonStructure(alignmentData);
                    renderWords(alignmentData.words);
                    populateStats(alignmentData);
                    jsonBtn.textContent = file.name;
                    jsonBtn.classList.add("loaded");
                    tryEnablePlayback();
                } catch (err) {
                    alert(`JSON parse error: ${err.message}`);
                    alignmentData = null;
                }
            };
            reader.readAsText(file);
        });
    }
    /**
     * Throws if the JSON is missing required top-level keys.
     */
    function validateJsonStructure(data) {
        const required = ["surah_number", "total_words", "words"];
        for (const key of required) {
            if (!(key in data)) {
                throw new Error(`Missing required key: "${key}"`);
            }
        }
        if (!Array.isArray(data.words) || data.words.length === 0) {
            throw new Error("words array is empty or not an array.");
        }
        const first = data.words[0];
        const wordRequired = ["index", "surah", "ayah", "text", "start_ms", "end_ms", "score"];
        for (const key of wordRequired) {
            if (!(key in first)) {
                throw new Error(`Word token missing required key: "${key}"`);
            }
        }
    }
    /**
     * Enables playback controls only when both files are loaded.
     */
    function tryEnablePlayback() {
        if (alignmentData && audioEl.src && audioEl.readyState >= 1) {
            playBtn.disabled = false;
            setStatus("ready");
        }
    }
    // ── Rendering ─────────────────────────────────────────────────────────
    /**
     * Renders the full word list grouped by Ayah number.
     * Each word becomes a <span> with data attributes for timestamps.
     */
    function renderWords(words) {
        verseDisplay.innerHTML = "";
        let currentAyah = -1;
        words.forEach(word => {
            // Insert Ayah number marker at each Ayah boundary
            if (word.ayah !== currentAyah) {
                currentAyah = word.ayah;
                const marker = document.createElement("span");
                marker.className   = "ayah-number";
                marker.textContent = currentAyah;
                verseDisplay.appendChild(marker);
            }
            const span = document.createElement("span");
            span.className      = "word-token";
            span.id             = `w${word.index}`;
            span.textContent    = word.text;
            span.dataset.index  = word.index;
            span.dataset.startMs = word.start_ms;
            span.dataset.endMs   = word.end_ms;
            span.dataset.score   = word.score;
            span.dataset.ayah    = word.ayah;
            span.dataset.tooltip = `${word.start_ms}ms → ${word.end_ms}ms | score: ${word.score}`;
            // Clicking a word seeks audio to that word's timestamp
            span.addEventListener("click", () => {
                if (audioEl.src) {
                    audioEl.currentTime = word.start_ms / 1000;
                }
            });
            verseDisplay.appendChild(span);
        });
    }
    /**
     * Populates the static stats (Surah number, total words).
     */
    function populateStats(data) {
        statSurah.textContent = data.surah_number;
        statWords.textContent = data.total_words;
    }
    // ── Playback ──────────────────────────────────────────────────────────
    function togglePlay() {
        if (audioEl.paused) {
            audioEl.play();
            playIcon.setAttribute("d", ICON_PAUSE);
            setStatus("playing");
            startRaf();
        } else {
            audioEl.pause();
            playIcon.setAttribute("d", ICON_PLAY);
            setStatus("ready");
            stopRaf();
        }
    }
    function seek(event) {
        if (!audioEl.duration) return;
        const track  = document.getElementById("progressTrack");
        const rect   = track.getBoundingClientRect();
        const ratio  = (event.clientX - rect.left) / rect.width;
        audioEl.currentTime = ratio * audioEl.duration;
    }
    audioEl.addEventListener("ended", () => {
        playIcon.setAttribute("d", ICON_PLAY);
        setStatus("ready");
        stopRaf();
        clearHighlights();
    });
    // ── Animation Loop ────────────────────────────────────────────────────
    /**
     * Called on every animation frame while audio is playing.
     * Determines which word is active and drives all UI updates.
     */
    function rafLoop() {
        const currentMs = audioEl.currentTime * 1000;
        updateProgress(currentMs);
        updateActiveWord(currentMs);
        rafHandle = requestAnimationFrame(rafLoop);
    }
    function startRaf() {
        if (rafHandle) cancelAnimationFrame(rafHandle);
        rafHandle = requestAnimationFrame(rafLoop);
    }
    function stopRaf() {
        if (rafHandle) {
            cancelAnimationFrame(rafHandle);
            rafHandle = null;
        }
    }
    // ── Progress Bar ──────────────────────────────────────────────────────
    function updateProgress(currentMs) {
        const duration = audioEl.duration * 1000;
        if (!duration) return;
        const pct = (currentMs / duration) * 100;
        progressFill.style.width = `${pct.toFixed(2)}%`;
        currentTime.textContent  = formatTime(currentMs);
    }
    // ── Word Highlighting ─────────────────────────────────────────────────
    /**
     * Finds the active word for the current timestamp and updates the DOM.
     * Uses a linear scan - acceptable for single-Surah sizes (max ~6k words).
     * Applies "past" class to all words before the active word.
     */
    function updateActiveWord(currentMs) {
        if (!alignmentData) return;
        const words     = alignmentData.words;
        let   newIndex  = -1;
        // Find active word: start_ms <= currentMs < end_ms
        for (let i = 0; i < words.length; i++) {
            if (currentMs >= words[i].start_ms && currentMs < words[i].end_ms) {
                newIndex = i;
                break;
            }
        }
        // No change needed
        if (newIndex === activeIndex) return;
        // Remove state from previous active word
        if (activeIndex >= 0) {
            const prev = document.getElementById(`w${activeIndex}`);
            if (prev) {
                prev.classList.remove("active");
                prev.classList.add("past");
            }
        }
        // Apply active state to new word
        if (newIndex >= 0) {
            const curr = document.getElementById(`w${newIndex}`);
            if (curr) {
                curr.classList.add("active");
                curr.classList.remove("past");
                curr.scrollIntoView({ behavior: "smooth", block: "nearest" });
                // Update stats bar
                const word = words[newIndex];
                statActive.textContent = `${word.text} (${newIndex})`;
                statAyah.textContent   = word.ayah;
                statScore.textContent  = word.score.toFixed(3);
            }
        }
        activeIndex = newIndex;
    }
    function clearHighlights() {
        if (!alignmentData) return;
        alignmentData.words.forEach(w => {
            const el = document.getElementById(`w${w.index}`);
            if (el) el.classList.remove("active", "past");
        });
        activeIndex = -1;
        statActive.textContent = "—";
        statAyah.textContent   = "—";
        statScore.textContent  = "—";
    }
    // ── Helpers ───────────────────────────────────────────────────────────
    /**
     * Formats milliseconds to M:SS.d string.
     * e.g. 65400ms → "1:05.4"
     */
    function formatTime(ms) {
        const totalSec = ms / 1000;
        const mins     = Math.floor(totalSec / 60);
        const secs     = Math.floor(totalSec % 60);
        const tenth    = Math.floor((totalSec % 1) * 10);
        return `${mins}:${String(secs).padStart(2, "0")}.${tenth}`;
    }
    function setStatus(state) {
        statusBadge.className   = `status-badge ${state}`;
        statusBadge.textContent = state.charAt(0).toUpperCase() + state.slice(1);
    }
    // ── Init ──────────────────────────────────────────────────────────────
    function init() {
        bindFileInputs();
    }
    init();
    // Public API
    return { togglePlay, seek };
})();
