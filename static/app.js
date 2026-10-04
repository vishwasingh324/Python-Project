(() => {
  const form = document.querySelector("#command-form");
  const input = document.querySelector("#command-input");
  const sendButton = document.querySelector("#send-button");
  const micButton = document.querySelector("#mic-button");
  const promptStatus = document.querySelector("#prompt-status");
  const promptDescription = document.querySelector("#prompt-description");
  const speechPreview = document.querySelector("#speech-preview");
  const orbCaption = document.querySelector(".orb-caption");
  const statePillText = document.querySelector("#state-pill-text");
  const soundToggle = document.querySelector("#sound-toggle");
  const soundToggleLabel = document.querySelector("#sound-toggle-label");
  const conversation = document.querySelector("#conversation");
  const emptyState = document.querySelector("#empty-state");
  const activityCount = document.querySelector("#activity-count");
  const navCommandCount = document.querySelector("#nav-command-count");
  const healthIndicator = document.querySelector("#health-indicator");
  const healthLabel = document.querySelector("#health-label");
  const toast = document.querySelector("#toast");

  let requestInFlight = false;
  let recognition = null;
  let listening = false;
  let commandCount = 0;
  let toastTimeout = null;
  let speechEnabled = false;
  try {
    speechEnabled = localStorage.getItem("backend.speech-enabled") === "true";
  } catch (_error) {
    // Storage can be disabled in private browsing; speech still works this session.
  }

  const copyByState = {
    ready: {
      pill: "Ready to help",
      heading: "Ready when you are",
      description: "Tap the microphone and say something like “open YouTube”.",
      orb: "TAP TO SPEAK",
    },
    listening: {
      pill: "Listening now",
      heading: "Go ahead, I’m listening",
      description: "Say your command clearly, then I’ll take it from here.",
      orb: "LISTENING",
    },
    thinking: {
      pill: "Working on it",
      heading: "One moment…",
      description: "Alexa is figuring out the best next step.",
      orb: "THINKING",
    },
    offline: {
      pill: "Backend offline",
      heading: "I can’t reach the backend",
      description: "Start the app with “python backend.py”, then try again.",
      orb: "OFFLINE",
    },
    stopped: {
      pill: "Session paused",
      heading: "Until next time",
      description: "You can send another command whenever you’re ready.",
      orb: "TAP TO SPEAK",
    },
  };

  function setStatus(state, description) {
    const copy = copyByState[state] || copyByState.ready;
    document.body.dataset.state = state;
    statePillText.textContent = copy.pill;
    promptStatus.textContent = copy.heading;
    promptDescription.textContent = description || copy.description;
    orbCaption.textContent = copy.orb;
    if (state !== "listening") speechPreview.textContent = "";

    const isMicActive = state === "listening";
    micButton.setAttribute("aria-label", isMicActive ? "Stop listening" : "Start voice command");
    micButton.title = isMicActive ? "Stop listening" : "Start listening";
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(toastTimeout);
    toastTimeout = window.setTimeout(() => toast.classList.remove("visible"), 3300);
  }

  function currentTime() {
    return new Intl.DateTimeFormat(undefined, {
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date());
  }

  function appendMessage(role, text, action = null) {
    emptyState.hidden = true;

    const message = document.createElement("article");
    message.className = `message ${role === "user" ? "message-user" : "message-assistant"}`;

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
    time.textContent = currentTime();
    meta.append(name, time);

    const paragraph = document.createElement("p");
    paragraph.className = "message-text";
    paragraph.textContent = text;
    main.append(meta, paragraph);

    if (action && typeof action.url === "string" && /^https:\/\//i.test(action.url)) {
      const link = document.createElement("a");
      link.className = "message-action";
      link.href = action.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = action.label || "Open link";
      const arrow = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      arrow.setAttribute("viewBox", "0 0 16 16");
      arrow.setAttribute("aria-hidden", "true");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M4 12 12 4M5 4h7v7");
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "currentColor");
      path.setAttribute("stroke-width", "1.5");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
      arrow.append(path);
      link.append(arrow);
      main.append(link);
    }

    message.append(avatar, main);
    conversation.append(message);
    conversation.scrollTop = conversation.scrollHeight;

    if (role === "assistant" && speechEnabled) speak(text);
  }

  function updateCommandCount() {
    const label = commandCount === 1 ? "command" : "commands";
    activityCount.textContent = String(commandCount);
    navCommandCount.textContent = String(commandCount);
    navCommandCount.setAttribute("aria-label", `${commandCount} ${label}`);
  }

  function speak(text) {
    if (!("speechSynthesis" in window) || !speechEnabled) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = navigator.language || "en-US";
    utterance.rate = 0.98;
    utterance.pitch = 1.02;
    window.speechSynthesis.speak(utterance);
  }

  function updateSoundControl() {
    soundToggle.setAttribute("aria-pressed", String(speechEnabled));
    soundToggleLabel.textContent = speechEnabled ? "Sound on" : "Sound off";
    soundToggle.title = speechEnabled ? "Turn spoken replies off" : "Turn spoken replies on";
    if (!("speechSynthesis" in window)) {
      soundToggle.disabled = true;
      soundToggle.title = "Spoken replies are not supported in this browser";
      soundToggleLabel.textContent = "No speech";
    }
  }

  async function sendCommand(command) {
    if (!command || requestInFlight) return;
    requestInFlight = true;
    sendButton.disabled = true;
    commandCount += 1;
    updateCommandCount();
    appendMessage("user", command);
    setStatus("thinking");

    if (recognition && listening) {
      try { recognition.stop(); } catch (_error) { /* Already stopped. */ }
    }

    try {
      const response = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(result.error || `Request failed (${response.status})`);
      }

      appendMessage("assistant", result.reply || "I’m ready for another command.", result.action);
      if (result.status === "stopped") {
        setStatus("stopped");
      } else {
        setStatus("ready");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not contact the backend.";
      appendMessage("assistant", "I can’t connect to the local backend right now. Check that the server is running, then try again.");
      setStatus("offline");
      showToast(message);
    } finally {
      requestInFlight = false;
      sendButton.disabled = false;
      input.focus({ preventScroll: true });
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const command = input.value.trim();
    if (!command || requestInFlight) return;
    input.value = "";
    sendCommand(command);
  });

  document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => {
      const command = button.getAttribute("data-command");
      if (!command || requestInFlight) return;
      input.value = command;
      form.requestSubmit();
    });
  });

  document.querySelector("#clear-activity").addEventListener("click", () => {
    conversation.replaceChildren(emptyState);
    emptyState.hidden = false;
    commandCount = 0;
    updateCommandCount();
    if (!requestInFlight && document.body.dataset.state !== "offline") setStatus("ready");
    showToast("Conversation cleared.");
  });

  document.querySelector("#focus-shortcut").addEventListener("click", () => input.focus());
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      input.focus();
    }
    if (event.key === "Escape" && listening && recognition) {
      recognition.stop();
    }
  });

  soundToggle.addEventListener("click", () => {
    speechEnabled = !speechEnabled;
    try { localStorage.setItem("backend.speech-enabled", String(speechEnabled)); } catch (_error) { /* Optional preference. */ }
    updateSoundControl();
    if (!speechEnabled && "speechSynthesis" in window) window.speechSynthesis.cancel();
    showToast(speechEnabled ? "Spoken replies are on." : "Spoken replies are off.");
  });
  updateSoundControl();

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      listening = true;
      setStatus("listening");
      speechPreview.textContent = "";
    };

    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const transcript = event.results[index][0].transcript;
        if (event.results[index].isFinal) finalText += transcript;
        else interimText += transcript;
      }
      if (interimText.trim()) {
        speechPreview.textContent = `“${interimText.trim()}”`;
      }
      if (finalText.trim()) {
        const command = finalText.trim();
        speechPreview.textContent = `Heard: “${command}”`;
        input.value = command;
        form.requestSubmit();
      }
    };

    recognition.onerror = (event) => {
      listening = false;
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        setStatus("ready", "Microphone access is blocked. You can still type a command.");
        showToast("Allow microphone access in your browser to use voice commands.");
      } else if (event.error === "no-speech") {
        setStatus("ready", "I didn’t hear anything. Tap the microphone and try again.");
      } else if (event.error !== "aborted") {
        setStatus("ready", "Voice input stopped. You can type a command instead.");
        showToast(`Voice input error: ${event.error}`);
      }
    };

    recognition.onend = () => {
      listening = false;
      if (!requestInFlight && document.body.dataset.state === "listening") setStatus("ready");
    };
  }

  micButton.addEventListener("click", () => {
    if (!recognition) {
      showToast("Voice input is not available in this browser. Try Chrome or Edge, or type a command.");
      return;
    }
    if (requestInFlight) return;
    if (listening) {
      recognition.stop();
      return;
    }
    speechPreview.textContent = "";
    try {
      recognition.start();
    } catch (error) {
      showToast("The microphone is busy. Wait a moment and try again.");
    }
  });

  async function checkBackend() {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) throw new Error("Backend unavailable");
      healthIndicator.classList.add("online");
      healthIndicator.classList.remove("offline");
      healthLabel.textContent = "All systems ready";
      if (document.body.dataset.state === "offline") setStatus("ready");
    } catch (_error) {
      healthIndicator.classList.add("offline");
      healthIndicator.classList.remove("online");
      healthLabel.textContent = "Backend unavailable";
      setStatus("offline");
    }
  }

  const dateLabel = document.querySelector("#today-label");
  const today = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(new Date());
  dateLabel.textContent = `SESSION ACTIVE · ${today.toUpperCase()}`;

  updateCommandCount();
  setStatus("ready");
  checkBackend();
})();
