"""
templates.py
------------
Anki card model: CSS + Front/Back HTML templates shared across every note.
Per-note data (WordsJson) drives an inline JS cloze-slider so the mask level
is adjustable live on a single card, rather than Anki's native
answer-hiding cloze deletion. See scripts/anki_export/README.md.
"""

CSS = """
.qh-card {
  font-family: system-ui, sans-serif;
  background: #14161f;
  color: #e8eaf0;
  padding: 14px;
  border-radius: 10px;
}
.qh-header {
  font-size: 13px;
  color: #8b90a0;
  margin-bottom: 10px;
  text-align: center;
}
.qh-controls {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  margin-bottom: 14px;
}
.cloze-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #8b90a0;
}
.cloze-slider {
  width: 180px;
}
.cloze-pct {
  min-width: 34px;
  font-weight: 600;
  color: #4f8ef7;
}
.verse, .verse-full {
  direction: rtl;
  text-align: justify;
  line-height: 2.6;
  font-family: 'Traditional Arabic', 'Scheherazade New', 'Amiri', serif;
  font-size: 26px;
}
.word {
  display: inline-block;
  padding: 2px 4px;
  border-radius: 5px;
}
.word.masked {
  color: #4f8ef7;
  background: #4f8ef720;
}
.ayah-marker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 1px solid #6b7280;
  color: #6b7280;
  font-size: 11px;
  font-family: system-ui, sans-serif;
  margin: 0 4px;
  vertical-align: middle;
}
hr.qh-sep {
  border: none;
  border-top: 1px solid #2e3140;
  margin: 16px 0;
}
"""

# Inline JS reads the per-note WordsJson data island via a *scoped* lookup
# (document.currentScript.closest(...)) so the same template can appear
# twice on the Answer side ({{FrontSide}} + native rendering) without id
# collisions.
FRONT_TEMPLATE = """
<div class="qh-card" dir="rtl">
  <div class="qh-header" dir="ltr">Quarter-Hizb {{QHId}} &middot; {{SurahLabel}}</div>

  <div class="qh-controls" dir="ltr">
    {{#AudioTag}}<div>{{AudioTag}}</div>{{/AudioTag}}
    <div class="cloze-row">
      <span>Cloze</span>
      <input type="range" class="cloze-slider" min="0" max="6" step="1" value="2">
      <span class="cloze-pct">25%</span>
    </div>
  </div>

  <div class="verse"></div>

  <script type="application/json" class="qh-data">{{WordsJson}}</script>
  <script>
  (function () {
    var thisScript = document.currentScript;
    var root = thisScript.closest('.qh-card');
    var dataEl = root.querySelector('script.qh-data');
    var words = JSON.parse(dataEl.textContent);
    var steps = [0, 10, 25, 50, 75, 90, 95];

    var segTotals = {};
    words.forEach(function (w) {
      segTotals[w.s] = (segTotals[w.s] || 0) + 1;
    });

    var verseEl = root.querySelector('.verse');
    var spans = [];
    var lastAyah = null, lastSeg = null;
    words.forEach(function (w) {
      if (lastAyah !== null && (w.a !== lastAyah || w.s !== lastSeg)) {
        var marker = document.createElement('span');
        marker.className = 'ayah-marker';
        marker.textContent = lastAyah;
        verseEl.appendChild(marker);
      }
      var span = document.createElement('span');
      span.className = 'word';
      span.dataset.r = w.r;
      span.dataset.seg = w.s;
      span.dataset.text = w.t;
      span.textContent = w.t;
      verseEl.appendChild(span);
      spans.push(span);
      lastAyah = w.a;
      lastSeg = w.s;
    });
    if (lastAyah !== null) {
      var marker = document.createElement('span');
      marker.className = 'ayah-marker';
      marker.textContent = lastAyah;
      verseEl.appendChild(marker);
    }

    function placeholder(text) {
      var n = Math.max(1, Math.floor(text.length / 2));
      return '_'.repeat(n);
    }

    function applyLevel(stepIdx) {
      var pct = steps[stepIdx];
      root.querySelector('.cloze-pct').textContent = pct + '%';
      spans.forEach(function (span) {
        var total = segTotals[span.dataset.seg];
        var threshold = Math.round(total * pct / 100);
        var masked = parseInt(span.dataset.r, 10) < threshold;
        span.textContent = masked ? placeholder(span.dataset.text) : span.dataset.text;
        span.classList.toggle('masked', masked);
      });
    }

    var slider = root.querySelector('.cloze-slider');
    slider.addEventListener('input', function () {
      applyLevel(parseInt(slider.value, 10));
    });
    applyLevel(parseInt(slider.value, 10));
  })();
  </script>
</div>
"""

BACK_TEMPLATE = """
{{FrontSide}}
<hr class="qh-sep">
<div class="verse-full" dir="rtl">{{FullTextHtml}}</div>
"""
