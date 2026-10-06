/* ============================================================================
   Alexa Assistant — frontend logic
   Talks to the local Python backend:
     GET  /api/health    -> backend liveness
     GET  /api/commands  -> what the assistant understands (drives this UI)
     POST /api/command   -> { command } -> { reply, action, status, should_continue }
   Voice uses the browser's SpeechRecognition (Chrome/Edge) and reports exactly
   why it fails instead of blaming the user's permissions blindly.
   ========================================================================== */
(() => {
  "use strict";

  /* ───────────────────────────── element handles ───────────────────────────── */
  const $ = (id) => document.getElementById(id);

  const els = {
    navCount: $("nav-count"),
    railLaunch: $("rail-launch"),
    healthDot: $("health-dot"),
    healthLabel: $("health-label"),
    healthDetail: $("health-detail"),
    voiceCheck: $("voice-check"),
    statePillText: $("state-pill-text"),
    soundToggle: $("sound-toggle"),
    soundLabel: $("sound-label"),
    voiceBadge: $("voice-badge"),
    micButton: $("mic-button"),
    orbCaption: $("orb-caption"),
    statusTitle: $("status-title"),
    statusText: $("status-text"),
    statusHeard: $("status-heard"),
    statusFix: $("status-fix"),
    statusFixText: $("status-fix-text"),
    openTab: $("open-tab"),
    form: $("command-form"),
    input: $("command-input"),
    sendButton: $("send-button"),
    suggestionRow: $("suggestion-row"),
    capGrid: $("cap-grid"),
    countBadge: $("count-badge"),
    clearLog: $("clear-log"),
    conversation: $("conversation"),
    emptyState: $("empty-state"),
    footStatus: $("foot-status"),
    dialog: $("diagnostics"),
    diagClose: $("diag-close"),
    checkList: $("check-list"),
    testMic: $("test-mic"),
    copyReport: $("copy-report"),
    openTab2: $("open-tab-2"),
    micTest: $("mic-test"),
    micTestMeter: $("mic-test-meter"),
    micTestResult: $("mic-test-result"),
    toast: $("toast"),
  };

  /* ───────────────────────────── app state ───────────────────────────── */
  function loadSessionId() {
    try {
      const existing = localStorage.getItem("backend.session");
      if (existing) return existing;
    } catch (_error) { /* storage may be unavailable */ }
    const fresh = (window.crypto && window.crypto.randomUUID)
      ? window.crypto.randomUUID()
      : `s-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    try { localStorage.setItem("backend.session", fresh); } catch (_error) { /* optional */ }
    return fresh;
  }

  const state = {
    session: loadSessionId(),
    status: "ready",
    listening: false,
    busy: false,
    speechEnabled: false,
    commandCount: 0,
    history: [],
    historyIndex: -1,
    capabilities: null,
  };

  const voice = {
    ctor: null,
    recognition: null,
    hasApi: false,
    embedded: false,
    secure: false,
    permission: "unknown",
    lastError: null,
  };

  const stateCopy = {
    ready: ["Ready to help", "Ready when you are", "Talk to me like a person — “hi”, “tell me a joke”, “I\u2019m tired” — or ask me to open something.", "TAP TO SPEAK"],
    listening: ["Listening now", "Go ahead, I'm listening", "Say your command clearly — I'll send it as soon as you stop.", "LISTENING"],
    thinking: ["Working on it", "One moment…", "Asking the backend what to do.", "THINKING"],
    offline: ["Backend offline", "I can't reach the backend", "The Python server isn't responding. Start it with “python backend.py”, then try again.", "OFFLINE"],
    stopped: ["Session paused", "Until next time", "Send “hello” whenever you want to start again.", "TAP TO SPEAK"],
    error: ["Voice problem", "Voice input hit a snag", "Check the voice panel for the exact reason — typing always works.", "VOICE ERROR"],
    chat: ["Chatting", "Listening to you", "Ask me anything — I\u2019ll reply in prose and offer a search when I don\u2019t know.", "TAP TO SPEAK"],
  };

  /* ───────────────────────────── small helpers ───────────────────────────── */
  function setStatus(status, overrides) {
    const copy = stateCopy[status] || stateCopy.ready;
    state.status = status;
    document.body.dataset.state = status;
    els.statePillText.textContent = copy[0];
    els.statusTitle.textContent = (overrides && overrides.title) || copy[1];
    els.statusText.textContent = (overrides && overrides.text) || copy[2];
    els.orbCaption.textContent = copy[3];
    if (status !== "listening") els.statusHeard.hidden = true;
  }

  function showHeard(text) {
    els.statusHeard.textContent = text;
    els.statusHeard.hidden = !text;
  }

  function showFix(message) {
    if (!message) {
      els.statusFix.hidden = true;
      return;
    }
    els.statusFixText.textContent = message;
    els.statusFix.hidden = false;
  }

  let toastTimer = null;
  function toast(message) {
    els.toast.textContent = message;
    els.toast.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => els.toast.classList.remove("visible"), 4200);
  }

  function timeNow() {
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date());
  }

  function svgEl(paths) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 20 20");
    svg.setAttribute("aria-hidden", "true");
    paths.forEach((d) => {
      const p = document.createElementNS(ns, "path");
      p.setAttribute("d", d);
      svg.append(p);
    });
    return svg;
  }

  /* ───────────────────────────── conversation ───────────────────────────── */
  function appendMessage(role, text, options = {}) {
    els.emptyState.hidden = true;
    const article = document.createElement("article");
    article.className = `message message-${role === "user" ? "user" : "assistant"}${options.error ? " error" : ""}`;

    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = role === "user" ? "Y" : "A";

    const main = document.createElement("div");
    main.className = "message-main";

    const meta = document.createElement("div");
    meta.className = "message-meta";
    const name = document.createElement("span");
    name.className = "message-name";
    name.textContent = role === "user" ? "You" : "Alexa";
    const time = document.createElement("time");
    time.className = "message-time";
    time.textContent = timeNow();
    meta.append(name, time);

    const body = document.createElement("p");
    body.className = "message-text";
    body.textContent = text;
    main.append(meta, body);

    if (options.status) {
      const tags = document.createElement("div");
      tags.className = "message-tags";
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = options.status;
      tags.append(tag);
      if (options.action) {
        const actionTag = document.createElement("span");
        actionTag.className = "tag";
        actionTag.textContent = "link ready";
        tags.append(actionTag);
      }
      main.append(tags);
    }

    if (options.action && typeof options.action.url === "string" && /^https:\/\//i.test(options.action.url)) {
      const link = document.createElement("a");
      link.className = "message-action";
      link.href = options.action.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.append(document.createTextNode(options.action.label || "Open link"));
      link.append(svgEl(["M4 12 12 4M5 4h7v7"]));
      main.append(link);
    }

    if (role !== "user") {
      const tools = document.createElement("div");
      tools.className = "message-tools";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "mini-button";
      copy.textContent = "Copy";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast("Reply copied.");
        } catch (_error) {
          toast("Copying isn't available in this browser.");
        }
      });
      tools.append(copy);
      main.append(tools);
    }

    article.append(avatar, main);
    els.conversation.append(article);
    els.conversation.scrollTop = els.conversation.scrollHeight;
    removeTyping();

    if (role !== "user" && state.speechEnabled) speak(text);
    return article;
  }

  let typingEl = null;
  function showTyping() {
    removeTyping();
    typingEl = document.createElement("article");
    typingEl.className = "message message-assistant";
    typingEl.innerHTML =
      '<span class="message-avatar" aria-hidden="true">A</span>' +
      '<div class="message-main"><div class="message-meta"><span class="message-name">Alexa</span></div>' +
      '<p class="message-text"><span class="typing"><i></i><i></i><i></i></span></p></div>';
    els.conversation.append(typingEl);
    els.conversation.scrollTop = els.conversation.scrollHeight;
  }

  function removeTyping() {
    if (typingEl) {
      typingEl.remove();
      typingEl = null;
    }
  }

  const statusLabels = {
    action: "opened a link",
    smalltalk: "chat",
    chat: "chat",
    chat_llm: "chat · model",
    search_suggested: "suggested a search",
    recall: "remembered",
    followup: "follow-up",
    time: "answered",
    help: "help",
    unsupported: "not supported here",
    unknown: "didn't understand",
    empty: "empty",
    stopped: "session ended",
  };

  function updateCount() {
    els.countBadge.textContent = String(state.commandCount);
    els.navCount.textContent = String(state.commandCount);
  }

  /* ───────────────────────────── spoken replies ───────────────────────────── */
  function speak(text) {
    if (!("speechSynthesis" in window) || !state.speechEnabled) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = document.documentElement.lang || navigator.language || "en-US";
    utterance.rate = 0.98;
    utterance.pitch = 1.02;
    window.speechSynthesis.speak(utterance);
  }

  function updateSoundControl() {
    const supported = "speechSynthesis" in window;
    els.soundToggle.disabled = !supported;
    els.soundToggle.setAttribute("aria-pressed", String(state.speechEnabled && supported));
    els.soundLabel.textContent = !supported ? "No speech" : state.speechEnabled ? "Sound on" : "Sound off";
  }

  /* ───────────────────────────── backend calls ───────────────────────────── */
  async function sendCommand(command) {
    if (!command || state.busy) return;
    state.busy = true;
    els.sendButton.disabled = true;
    state.commandCount += 1;
    state.history.push(command);
    state.historyIndex = state.history.length;
    updateCount();
    appendMessage("user", command);
    setStatus("thinking");
    showFix(null);

    if (state.listening && voice.recognition) {
      try { voice.recognition.stop(); } catch (_error) { /* already stopped */ }
    }

    showTyping();
    try {
      const response = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command, session: state.session }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
      removeTyping();
      appendMessage("assistant", result.reply || "I'm ready for another command.", {
        action: result.action,
        status: statusLabels[result.status] || result.status,
      });
      markHealthy();
      setStatus(result.status === "stopped" ? "stopped" : "ready");
    } catch (error) {
      removeTyping();
      const message = error instanceof Error ? error.message : "Could not contact the backend.";
      appendMessage("assistant", "I can't reach the local backend right now. Make sure the server is running, then try again.", { error: true, status: "offline" });
      setStatus("offline");
      markOffline(message);
      toast(message);
    } finally {
      state.busy = false;
      els.sendButton.disabled = false;
      els.input.focus({ preventScroll: true });
    }
  }

  async function loadCapabilities() {
    els.capGrid.replaceChildren(...Array.from({ length: 3 }, () => {
      const skeleton = document.createElement("div");
      skeleton.className = "cap-skeleton";
      return skeleton;
    }));

    try {
      const response = await fetch("/api/commands", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      state.capabilities = data;
      renderCapabilities(data);
    } catch (_error) {
      els.capGrid.replaceChildren();
      const note = document.createElement("p");
      note.className = "panel-sub";
      note.textContent = "Couldn't load /api/commands from the backend. Is the server running?";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "ghost-button";
      retry.textContent = "Retry";
      retry.addEventListener("click", loadCapabilities);
      els.capGrid.append(note, retry);
      els.railLaunch.replaceChildren();
    }
  }

  function commandButton(label, command, className) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.dataset.command = command;
    button.append(document.createTextNode(label));
    return button;
  }

  function renderCapabilities(data) {
    const categories = Array.isArray(data.categories) ? data.categories : [];

    // Rail quick launch: the first few site shortcuts.
    const railItems = (categories.find((c) => c.id === "launch")?.items || []).slice(0, 6);
    els.railLaunch.replaceChildren(
      ...railItems.map((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "launch-item";
        button.dataset.command = item.command;
        const icon = document.createElement("span");
        icon.className = "launch-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = item.label.slice(0, 1).toUpperCase();
        const text = document.createElement("span");
        text.textContent = item.label;
        const arrow = svgEl(["M5 10h10m-4-4 4 4-4 4"]);
        arrow.setAttribute("class", "launch-arrow");
        button.append(icon, text, arrow);
        return button;
      })
    );

    // Capability cards.
    els.capGrid.replaceChildren(
      ...categories.map((category) => {
        const card = document.createElement("div");
        card.className = "cap-card";
        const title = document.createElement("h3");
        title.textContent = category.label;
        const hint = document.createElement("p");
        hint.className = "cap-card-hint";
        hint.textContent = category.hint || "";
        const items = document.createElement("div");
        items.className = "cap-items";
        (category.items || []).forEach((item) => {
          const button = commandButton("", item.command, "cap-item");
          button.replaceChildren();
          const dot = document.createElement("span");
          dot.className = "cap-item-dot";
          dot.setAttribute("aria-hidden", "true");
          const label = document.createElement("span");
          label.textContent = item.label;
          button.append(dot, label);
          button.title = item.command;
          items.append(button);
        });
        card.append(title, hint, items);
        return card;
      })
    );

    // Suggestion chips under the input: one playful example per category.
    const picks = [];
    ["play", "search", "chat"].forEach((id) => {
      const category = categories.find((c) => c.id === id);
      if (category && category.items && category.items[0]) picks.push(category.items[0]);
    });
    els.suggestionRow.replaceChildren(
      ...picks.map((item) => {
        const button = commandButton("", item.command, "suggestion-chip");
        button.replaceChildren();
        const label = document.createElement("span");
        label.textContent = item.label;
        const arrow = document.createElement("span");
        arrow.className = "chip-arrow";
        arrow.textContent = "↗";
        button.append(label, arrow);
        return button;
      })
    );
  }

  /* ───────────────────────────── health ───────────────────────────── */
  function markHealthy() {
    els.healthDot.classList.add("online");
    els.healthDot.classList.remove("offline");
    els.healthLabel.textContent = "Backend connected";
    els.healthDetail.textContent = location.host || "local server";
    els.footStatus.textContent = `backend: ok at ${location.origin}`;
  }

  function markOffline(detail) {
    els.healthDot.classList.add("offline");
    els.healthDot.classList.remove("online");
    els.healthLabel.textContent = "Backend unavailable";
    els.healthDetail.textContent = detail || "Start it with: python backend.py";
    els.footStatus.textContent = "backend: offline";
  }

  async function checkHealth() {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      markHealthy();
      if (state.status === "offline") setStatus("ready");
    } catch (_error) {
      markOffline();
      if (state.status !== "offline") setStatus("offline");
    }
  }

  /* ───────────────────────────── voice ───────────────────────────── */
  function detectVoice() {
    voice.embedded = window.self !== window.top;
    // Browsers allow the microphone on https:// and on localhost. Not every
    // browser exposes isSecureContext, so the URL is checked as well.
    const localHost = ["localhost", "127.0.0.1", "[::1]", ""].includes(location.hostname);
    const httpsPage = location.protocol === "https:";
    voice.secure = window.isSecureContext === true || httpsPage || localHost;
    voice.ctor = window.SpeechRecognition || window.webkitSpeechRecognition || null;
    voice.hasApi = Boolean(voice.ctor);
  }

  function voiceBadge() {
    if (!voice.secure) {
      els.voiceBadge.className = "chip-badge bad";
      els.voiceBadge.textContent = "Voice: needs HTTPS";
    } else if (!voice.hasApi) {
      els.voiceBadge.className = "chip-badge bad";
      els.voiceBadge.textContent = "Voice: unsupported browser";
    } else if (voice.permission === "denied") {
      els.voiceBadge.className = "chip-badge bad";
      els.voiceBadge.textContent = "Voice: mic blocked";
    } else if (voice.embedded) {
      els.voiceBadge.className = "chip-badge warn";
      els.voiceBadge.textContent = "Voice: may be blocked here";
    } else {
      els.voiceBadge.className = "chip-badge ok";
      els.voiceBadge.textContent = "Voice: ready";
    }
  }

  function withTimeout(promise, ms) {
    return Promise.race([
      promise,
      new Promise((_resolve, reject) => window.setTimeout(() => reject(new Error("permission query timed out")), ms)),
    ]);
  }

  async function refreshPermission() {
    try {
      if (navigator.permissions && navigator.permissions.query) {
        // Some browsers accept the name but never settle the query, so cap it.
        const status = await withTimeout(navigator.permissions.query({ name: "microphone" }), 1500);
        voice.permission = status.state; // granted | denied | prompt
        status.addEventListener?.("change", () => {
          voice.permission = status.state;
          voiceBadge();
          renderChecks();
        });
      }
    } catch (_error) {
      voice.permission = "unknown"; // Firefox/Safari don't expose this
    }
    voiceBadge();
  }

  function explainVoiceError(code) {
    switch (code) {
      case "not-allowed":
      case "service-not-allowed":
        if (voice.embedded) {
          return {
            title: "This embedded preview blocks the microphone",
            text: "Browsers refuse microphone access inside frames unless the embedding page allows it. Open the app in its own tab — voice works there.",
            fix: "Voice is blocked inside this embedded preview. Open the app in its own browser tab to talk to it.",
          };
        }
        return {
          title: "Microphone permission is blocked",
          text: "Click the lock (or tune) icon in the address bar, allow the microphone for this site, then tap the mic again.",
          fix: "Microphone permission is denied. Allow it in the address bar and retry.",
        };
      case "audio-capture":
        return { title: "No microphone available", text: "No input device was found, or another app is holding the microphone.", fix: null };
      case "network":
        return {
          title: "The speech service is unreachable",
          text: "Chrome/Edge send audio to an online speech service, so voice needs an internet connection. Typing works fully offline.",
          fix: null,
        };
      case "no-speech":
        return { title: "I didn't hear anything", text: "Tap the microphone and speak a little closer to it.", fix: null };
      case "language-not-supported":
        return { title: "That language isn't supported", text: "Try another browser language, or type the command.", fix: null };
      case "aborted":
        return null;
      default:
        return { title: "Voice input stopped", text: `The browser reported “${code}”. Typing always works.`, fix: null };
    }
  }

  function handleVoiceError(code) {
    voice.lastError = code;
    const explained = explainVoiceError(code);
    if (!explained) {
      setStatus("ready");
      return;
    }
    setStatus("error", { title: explained.title, text: explained.text });
    showFix(explained.fix);
    voiceBadge();
    if (els.dialog.open) renderChecks();
  }

  function buildRecognition() {
    if (!voice.hasApi) return;
    const recognition = new voice.ctor();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      state.listening = true;
      els.micButton.setAttribute("aria-pressed", "true");
      els.micButton.setAttribute("aria-label", "Stop listening");
      setStatus("listening");
      showFix(null);
      showHeard("");
    };

    recognition.onresult = (event) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) final += transcript;
        else interim += transcript;
      }
      if (interim.trim()) showHeard(`“${interim.trim()}”`);
      if (final.trim()) {
        const command = final.trim();
        showHeard(`Heard: “${command}”`);
        els.input.value = command;
        state.listening = false;
        els.micButton.setAttribute("aria-pressed", "false");
        els.sendButton.click();
      }
    };

    recognition.onerror = (event) => {
      state.listening = false;
      els.micButton.setAttribute("aria-pressed", "false");
      els.micButton.setAttribute("aria-label", "Start voice command");
      handleVoiceError(event.error);
    };

    recognition.onend = () => {
      state.listening = false;
      els.micButton.setAttribute("aria-pressed", "false");
      els.micButton.setAttribute("aria-label", "Start voice command");
      if (!state.busy && state.status === "listening") setStatus("ready");
    };

    voice.recognition = recognition;
  }

  function startListening() {
    if (!voice.hasApi) {
      const message = voice.secure
        ? "This browser has no speech recognition. Chrome or Edge supports it — typing works everywhere."
        : "Voice needs a secure page (https:// or localhost). Typing works here.";
      setStatus("error", { title: "Voice isn't available here", text: message });
      toast(message);
      openDiagnostics();
      return;
    }
    els.input.value = "";
    try {
      voice.recognition.start();
    } catch (_error) {
      toast("The microphone is busy — wait a moment and try again.");
    }
  }

  function stopListening() {
    if (voice.recognition && state.listening) {
      try { voice.recognition.stop(); } catch (_error) { /* already stopping */ }
    }
  }

  /* ───────────────────────────── diagnostics dialog ───────────────────────────── */
  function checkRow(level, title, detail) {
    const li = document.createElement("li");
    const icon = svgEl(level === "ok" ? ["M4 10.5 8 14.5 16 6"] : level === "warn" ? ["M10 4v8", "M10 15.5v.5"] : ["m5 5 10 10", "M15 5 5 15"]);
    icon.setAttribute("class", `check-icon ${level}`);
    const body = document.createElement("div");
    body.className = "check-body";
    const strong = document.createElement("strong");
    strong.textContent = title;
    const span = document.createElement("span");
    span.textContent = detail;
    body.append(strong, span);
    li.append(icon, body);
    return li;
  }

  function renderChecks() {
    const rows = [];

    rows.push(
      els.healthDot.classList.contains("online")
        ? checkRow("ok", "Backend reachable", `${location.origin}/api/health answered.`)
        : checkRow("bad", "Backend not reachable", "Start it with “python backend.py”, then reload this page.")
    );

    rows.push(
      voice.secure
        ? checkRow("ok", "Secure context", `Speech features are permitted at ${location.origin}.`)
        : checkRow("bad", "Not a secure context", "Browsers only allow the microphone on https:// or http://localhost.")
    );

    rows.push(
      voice.hasApi
        ? checkRow("ok", "Speech recognition available", "window.SpeechRecognition is present.")
        : checkRow("bad", "Speech recognition missing", "Firefox/Safari have no SpeechRecognition. Use Chrome or Edge, or type commands.")
    );

    rows.push(
      voice.embedded
        ? checkRow("warn", "Running inside a frame", "This page is embedded (a preview iframe). Browsers block the microphone in frames unless the embedder allows it — “Open in a new tab” fixes it.")
        : checkRow("ok", "Running top-level", "Not embedded, so the microphone prompt can appear normally.")
    );

    const permissionText = {
      granted: "The browser reports the microphone as granted for this site.",
      denied: "Access was denied. Reset it in the address bar's site settings and retry.",
      prompt: "The browser will ask for permission the first time you tap the microphone.",
      unknown: "This browser doesn't expose microphone permission state.",
    }[voice.permission] || "";

    rows.push(
      voice.permission === "granted"
        ? checkRow("ok", "Microphone permission: granted", permissionText)
        : voice.permission === "denied"
          ? checkRow("bad", "Microphone permission: denied", permissionText)
          : checkRow("warn", `Microphone permission: ${voice.permission}`, permissionText)
    );

    rows.push(
      voice.lastError
        ? checkRow("bad", "Last voice error", `${voice.lastError} — ${(explainVoiceError(voice.lastError) || {}).text || "see the status message."}`)
        : checkRow("ok", "No voice errors this session", "Nothing has failed yet.")
    );

    els.checkList.replaceChildren(...rows);
  }

  function openDiagnostics() {
    refreshPermission().then(renderChecks);
    renderChecks();
    try {
      if (typeof els.dialog.showModal === "function") {
        if (!els.dialog.open) els.dialog.showModal();
      } else {
        els.dialog.setAttribute("open", "");
      }
    } catch (_error) {
      els.dialog.setAttribute("open", ""); // environments without <dialog> support
    }
  }

  function closeDiagnostics() {
    try {
      if (typeof els.dialog.close === "function" && els.dialog.open) els.dialog.close();
      else els.dialog.removeAttribute("open");
    } catch (_error) {
      els.dialog.removeAttribute("open");
    }
  }

  function openInNewTab() {
    window.open(location.href, "_blank", "noopener");
  }

  function reportText() {
    return [
      "Alexa Assistant — voice report",
      `when: ${new Date().toISOString()}`,
      `origin: ${location.origin}`,
      `embedded in frame: ${voice.embedded}`,
      `secure context: ${voice.secure}`,
      `SpeechRecognition: ${voice.hasApi}`,
      `mic permission: ${voice.permission}`,
      `last voice error: ${voice.lastError || "none"}`,
      `backend online: ${els.healthDot.classList.contains("online")}`,
      `browser: ${navigator.userAgent}`,
    ].join("\n");
  }

  let micTestRunning = false;
  async function testMicrophone() {
    if (micTestRunning) return;
    els.micTest.hidden = false;
    const bars = Array.from(els.micTestMeter.children);
    bars.forEach((bar) => { bar.style.height = "4px"; });

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      els.micTestResult.textContent = "This browser doesn't expose getUserMedia, so the microphone can't be tested directly.";
      return;
    }

    micTestRunning = true;
    els.micTestResult.textContent = "Requesting microphone access…";
    let stream = null;
    let audioContext = null;
    let frame = 0;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const deadline = Date.now() + 5000;
      els.micTestResult.textContent = "Listening for 5 seconds — say something…";

      const draw = () => {
        analyser.getByteFrequencyData(data);
        const chunk = Math.floor(data.length / bars.length);
        bars.forEach((bar, index) => {
          let sum = 0;
          for (let i = index * chunk; i < (index + 1) * chunk; i += 1) sum += data[i];
          const level = Math.min(100, Math.round((sum / chunk / 255) * 260));
          bar.style.height = `${Math.max(4, level)}%`;
        });
        if (Date.now() < deadline) frame = window.requestAnimationFrame(draw);
        else finish("The microphone is working — the bars moved with your voice.");
      };
      draw();
    } catch (error) {
      const name = error && error.name ? error.name : "Error";
      const detail = {
        NotAllowedError: voice.embedded
          ? "Blocked (this page is embedded — open it in a new tab)."
          : "Blocked — allow the microphone in your browser's address bar.",
        NotFoundError: "No microphone device was found.",
        NotReadableError: "The microphone is busy — close other apps using it.",
        OverconstrainedError: "No microphone matched the requested settings.",
        SecurityError: "Insecure context — use https:// or localhost.",
      }[name] || `The browser reported ${name}.`;
      els.micTestResult.textContent = `Microphone test failed: ${detail}`;
      voice.lastError = name === "NotAllowedError" ? "not-allowed" : voice.lastError;
      micTestRunning = false;
      renderChecks();
    }

    function finish(message) {
      window.cancelAnimationFrame(frame);
      if (stream) stream.getTracks().forEach((track) => track.stop());
      if (audioContext && audioContext.close) audioContext.close();
      els.micTestResult.textContent = message;
      micTestRunning = false;
    }
  }

  /* ───────────────────────────── wiring ───────────────────────────── */
  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const command = els.input.value.trim();
    if (!command || state.busy) return;
    els.input.value = "";
    sendCommand(command);
  });

  // One delegated handler covers every dynamically rendered command button.
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-command]");
    if (!trigger || state.busy) return;
    const command = trigger.getAttribute("data-command");
    if (!command) return;
    els.input.value = command;
    els.form.requestSubmit();
  });

  els.micButton.addEventListener("click", () => {
    if (state.busy) return;
    if (state.listening) stopListening();
    else startListening();
  });

  els.clearLog.addEventListener("click", () => {
    // Forget the conversation server-side too, so follow-ups like "open it"
    // can't refer to links that are no longer on screen.
    fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: state.session }),
    }).catch(() => {});
    els.conversation.replaceChildren(els.emptyState);
    els.emptyState.hidden = false;
    state.commandCount = 0;
    updateCount();
    setStatus("ready");
    showFix(null);
    toast("Conversation cleared.");
  });

  els.soundToggle.addEventListener("click", () => {
    state.speechEnabled = !state.speechEnabled;
    try { localStorage.setItem("backend.speech-enabled", String(state.speechEnabled)); } catch (_error) { /* optional */ }
    updateSoundControl();
    if (!state.speechEnabled && "speechSynthesis" in window) window.speechSynthesis.cancel();
    toast(state.speechEnabled ? "Spoken replies are on." : "Spoken replies are off.");
  });

  els.voiceCheck.addEventListener("click", openDiagnostics);
  els.diagClose.addEventListener("click", closeDiagnostics);
  els.testMic.addEventListener("click", testMicrophone);
  els.openTab.addEventListener("click", openInNewTab);
  els.openTab2.addEventListener("click", openInNewTab);
  els.copyReport.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(reportText());
      toast("Voice report copied to the clipboard.");
    } catch (_error) {
      toast("Copying isn't available — see the checks above.");
    }
  });
  els.dialog.addEventListener("click", (event) => {
    if (event.target === els.dialog) closeDiagnostics(); // click the backdrop
  });

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      els.input.focus();
      els.input.select();
    }
    if (event.key === "Escape" && state.listening) stopListening();
    if (event.key === "ArrowUp" && document.activeElement === els.input && state.history.length) {
      event.preventDefault();
      state.historyIndex = Math.max(0, state.historyIndex - 1);
      els.input.value = state.history[state.historyIndex] || "";
    }
    if (event.key === "ArrowDown" && document.activeElement === els.input && state.history.length) {
      event.preventDefault();
      state.historyIndex = Math.min(state.history.length, state.historyIndex + 1);
      els.input.value = state.history[state.historyIndex] || "";
    }
  });

  /* ───────────────────────────── boot ───────────────────────────── */
  try {
    state.speechEnabled = localStorage.getItem("backend.speech-enabled") === "true";
  } catch (_error) {
    state.speechEnabled = false;
  }

  detectVoice();
  buildRecognition();
  voiceBadge(); // show the voice state immediately, before any permission query
  updateSoundControl();
  updateCount();
  setStatus("ready");
  refreshPermission();
  loadCapabilities();
  checkHealth();
  window.setInterval(checkHealth, 15000);
})();
