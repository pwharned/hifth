const ANKI_URL = "http://localhost:8765";
const GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single";
const GOOGLE_TTS_URL = "https://translate.googleapis.com/translate_tts";

async function fetchWithTimeout(url, options = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

async function translateText(text, sourceLanguage, targetLanguage) {
  if (typeof text !== "string" || !text.trim()) {
    throw new Error("Translation text is empty");
  }

  const url = new URL(GOOGLE_TRANSLATE_URL);
  url.search = new URLSearchParams({
    client: "gtx",
    sl: sourceLanguage || "auto",
    tl: targetLanguage || "en",
    dt: "t",
    q: text
  }).toString();

  const response = await fetchWithTimeout(url.toString());
  if (!response.ok) {
    throw new Error(`Google translation failed (${response.status})`);
  }

  const payload = await response.json();
  const segments = Array.isArray(payload?.[0]) ? payload[0] : [];
  const result = segments.map(segment => segment?.[0] || "").join("").trim();
  if (!result) {
    throw new Error("Google returned an empty translation");
  }
  return { result, detectedLang: payload?.[2] || sourceLanguage || "auto" };
}

async function translateRequest(message) {
  if (
    message.mode === "contextual-target" &&
    typeof message.context === "string" &&
    Number.isInteger(message.startUtf16) &&
    Number.isInteger(message.endUtf16) &&
    message.context.slice(message.startUtf16, message.endUtf16) === message.text
  ) {
    const marked =
      message.context.slice(0, message.startUtf16) +
      `⟦${message.text}⟧` +
      message.context.slice(message.endUtf16);
    const contextual = await translateText(marked, message.langSrc, message.langTgt);
    const match = contextual.result.match(/⟦([\s\S]*?)⟧/);
    if (match?.[1]?.trim()) {
      return { ...contextual, result: match[1].trim() };
    }
  }
  return translateText(message.text, message.langSrc, message.langTgt);
}

function splitForTts(sentence, maxLength = 180) {
  const chunks = [];
  let remaining = sentence.trim();
  while (remaining.length > maxLength) {
    let splitAt = -1;
    for (let index = maxLength; index > Math.floor(maxLength / 2); index -= 1) {
      if (/\s/.test(remaining[index])) {
        splitAt = index;
        break;
      }
    }
    if (splitAt < 0) splitAt = maxLength;
    if (/^[\uDC00-\uDFFF]$/.test(remaining[splitAt])) splitAt -= 1;
    chunks.push(remaining.slice(0, splitAt).trim());
    remaining = remaining.slice(splitAt).trim();
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}

async function fetchWholeSentenceAudio(sentence, language) {
  if (typeof sentence !== "string" || !sentence.trim()) {
    throw new Error("Speech sentence is empty");
  }
  if (!language || language === "auto") {
    throw new Error("Cannot generate speech for an unknown language");
  }

  const audioParts = [];
  for (const chunk of splitForTts(sentence)) {
    const url = new URL(GOOGLE_TTS_URL);
    url.search = new URLSearchParams({
      ie: "UTF-8",
      client: "tw-ob",
      tl: language,
      q: chunk
    }).toString();
    const response = await fetchWithTimeout(url.toString());
    if (!response.ok) {
      throw new Error(`Google speech request failed (${response.status})`);
    }
    audioParts.push(new Uint8Array(await response.arrayBuffer()));
  }

  const length = audioParts.reduce((total, part) => total + part.length, 0);
  const audio = new Uint8Array(length);
  let offset = 0;
  for (const part of audioParts) {
    audio.set(part, offset);
    offset += part.length;
  }
  return audio;
}

function bytesToBase64(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    const end = Math.min(offset + 0x8000, bytes.length);
    for (let index = offset; index < end; index += 1) {
      binary += String.fromCharCode(bytes[index]);
    }
  }
  return btoa(binary);
}

async function audioFilename(sentence, language) {
  const encoded = new TextEncoder().encode(`${language}\u0000${sentence}`);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  const hash = Array.from(new Uint8Array(digest), byte =>
    byte.toString(16).padStart(2, "0")
  ).join("").slice(0, 20);
  const safeLanguage = language.replace(/[^a-z0-9-]/gi, "_");
  return `clausula_${safeLanguage}_${hash}.mp3`;
}

async function postToAnki(payload) {
  let response;
  try {
    response = await fetchWithTimeout(ANKI_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  } catch (error) {
    throw new Error(`AnkiConnect network error: ${errorMessage(error)}`);
  }
  if (!response.ok) {
    throw new Error(`AnkiConnect HTTP error (${response.status})`);
  }
  try {
    return await response.json();
  } catch (_error) {
    throw new Error("AnkiConnect returned invalid JSON");
  }
}

async function createAndStoreAudio(sentence, language) {
  const audio = await fetchWholeSentenceAudio(sentence, language);
  const filename = await audioFilename(sentence, language);
  const response = await postToAnki({
    action: "storeMediaFile",
    version: 6,
    params: { filename, data: bytesToBase64(audio) }
  });
  if (response?.error) {
    throw new Error(`AnkiConnect could not store audio: ${response.error}`);
  }
  if (!response?.result) {
    throw new Error("AnkiConnect returned no stored audio filename");
  }
  return response.result;
}

function errorMessage(error) {
  if (error?.name === "AbortError") return "request timed out";
  return error?.message || String(error);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  let operation;
  if (message?.type === "TRANSLATE_REQUEST") {
    operation = translateRequest(message)
      .then(value => ({ success: true, ...value }));
  } else if (message?.type === "AUDIO_FILENAME_REQUEST") {
    operation = audioFilename(message.sentence, message.lang)
      .then(filename => ({ success: true, filename }));
  } else if (message?.type === "AUDIO_REQUEST") {
    operation = createAndStoreAudio(message.sentence, message.lang)
      .then(filename => ({ success: true, filename }));
  } else if (message?.type === "ANKI_REQUEST") {
    operation = postToAnki(message.payload)
      .then(data => ({ success: true, data }));
  } else {
    return false;
  }

  operation
    .then(sendResponse)
    .catch(error => sendResponse({ success: false, error: errorMessage(error) }));
  return true;
});
