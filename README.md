# Backend — Alexa Voice Assistant

A browser frontend plus a small Python backend that turns voice or typed commands
into safe actions: opening sites, running YouTube or web searches, and answering
simple questions. Python 3.10+ is all you need — the backend uses only the
standard library, and the frontend loads no external fonts, CDNs or scripts, so
the whole thing runs offline.

## Run it

```bash
python backend.py
```

Then open [http://localhost:8000](http://localhost:8000). To change the port, run
`python backend.py --port 8080` (or set `PORT`). For a terminal-only version, run
`python backend.py --cli`.

Two convenience scripts are included:

```bash
bash scripts/start-app.sh        # the web app, listening on 0.0.0.0 (default :8000)
bash scripts/start-vscode.sh     # VS Code in the browser with this repo open
```

Note: `0.0.0.0` is a *listen* address, not a browsable one — open `localhost`
(or the forwarded preview link, in a hosted sandbox) in your browser.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Backend liveness — used by the UI's status dot. |
| `GET` | `/api/commands` | Every command the assistant understands, grouped for the UI. |
| `POST` | `/api/command` | `{"command": "open youtube", "session": "<id>"}` → `{reply, action, status, should_continue, session}`. |
| `POST` | `/api/reset` | `{"session": "<id>"}` → forgets that conversation (used by the Clear button). |

The frontend renders its quick-launch buttons, capability cards and suggestion
chips straight from `/api/commands`, so the UI can never drift away from what the
backend actually supports. A test asserts every offered example resolves to a
real intent.

## Talking to it

The assistant holds a normal conversation, not just commands:

- **Small talk** — greetings, "how are you", "are you an AI?", "who made you",
  feelings ("I'm tired" gets a real answer), praise, apologies, jokes, facts,
  "can you sing?", "what's the meaning of life".
- **Little utilities** — "flip a coin", "roll a dice", "pick a random number",
  safe arithmetic like "what is 12 * 8" (parsed with `ast`, never `eval`).
- **Session memory** — it remembers the current chat, so follow-ups work:
  "open **it**", "search that", "play it", "**again**", "what did I ask?",
  "what did you say?", "what have we talked about?".
- **No dead ends** — an unrecognised sentence is answered in prose. Questions
  come with a ready-to-click web search; statements get a friendly reply plus an
  optional search link. Nothing says "Command not recognized" any more.

Conversations are keyed by a `session` id the frontend generates and stores in
`localStorage`, kept in memory with a two-hour idle timeout, and dropped when you
press **Clear**.

### Real language-model chat (optional)

For open-ended answers, point it at an LLM — it is entirely optional and off by
default:

```bash
export ALEXA_LLM_PROVIDER=anthropic      # or openai (auto-detected from the key)
export ANTHROPIC_API_KEY=sk-ant-...      # or OPENAI_API_KEY=sk-...
export ALEXA_LLM_MODEL=claude-3-5-haiku-latest   # optional
python backend.py
```

Free-form sentences are then answered by the model with the conversation history
as context. Commands, greetings and the time are still handled locally, so
nothing breaks when there is no key, no network, or the API call fails — it
quietly falls back to the local answers. Keys are read from the environment only;
none are ever stored in the repository.

## What you can say

- **Open a site:** "open YouTube", "open Google Classroom", "open Drive", "open Claude", "open Gemini", "open Google", "open GitHub", "open WhatsApp", "open Gmail", "open ChatGPT", "open Instagram".
- **YouTube:** "play lofi beats on YouTube" or "open YouTube and play lofi beats".
- **Web search:** "search for Python tutorials".
- **Conversation:** "hi", "how are you", "tell me a joke", "I'm tired", "flip a coin", "what is 12 * 8", "thanks", "help", "what time is it", "what's the date".
- **End the session:** "stop", "exit", "quit", "bye", "good night".

## The frontend

- **The conversation and the input box share one panel.** The log sits directly
  above the box you type in, so what you asked and what I answered stay on
  screen while you write the next line. New bubbles appear next to the input and
  the log scrolls on its own — the box never moves, and nothing is hidden in a
  section further down the page.
- **Talk or type.** The microphone uses the browser's speech recognition; the
  input box always works, and `Enter` sends. `Ctrl`/`Cmd`+`K` focuses the input,
  `Esc` cancels listening, `↑`/`↓` walk back through your command history.
  Focusing the input also scrolls the conversation into view if a short window
  had pushed it off screen.
- **Conversation log** with timestamps, the backend's `status` for each reply, a
  copy button, and a clickable **Open …** link when the backend returned an action.
- **Spoken replies** are opt-in via the *Sound* toggle in the top bar.
- **Voice check** (sidebar, or the badge in the hero panel) explains voice
  problems: secure context, browser support, whether the page is embedded in a
  frame, microphone permission, the last error, plus a **Test microphone** button
  that shows a live input-level meter and a **Copy report** button for bug reports.

## If voice doesn't work

The microphone has real requirements, and the app now reports which one is
failing instead of blaming your permissions:

1. **Chrome or Edge.** Firefox and Safari have no `SpeechRecognition` API — typing still works.
2. **A secure page** — `https://` or `http://localhost`. Plain `http://` on another host is blocked by the browser.
3. **Top-level, not embedded.** Browsers block microphone access inside
   cross-origin iframes (for example a live-preview panel) unless the embedding
   page opts in. If you see *"This embedded preview blocks the microphone"*, use
   the **Open in a new tab** button — voice works there.
4. **An internet connection / speech service reachability.** Chrome/Edge send
   audio to an online speech service, so recognition needs network access even
   though the rest of the app is offline.
   - **Brave Browser:** Brave disables Google speech recognition by default. Open
     `brave://settings/extensions` and enable **"Use Google services for speech
     recognition"**.
   - **Preview frame:** If running inside an embedded preview iframe, click
     **Open in a new tab** to bypass frame restrictions.
   - **OS Voice Typing (Offline Alternative):** You can speak directly into the
     command input using your operating system's built-in dictation without
     cloud speech:
     - **Windows:** Press `Win + H` while focused on the box.
     - **macOS:** Press `Fn` twice (or your configured Dictation key).
   - **Retry button:** The UI provides a "Retry microphone" button that cleans
     up stale connection state and automatically falls back to `en-US`.
5. **Permission granted** in the browser's address bar for the site.

## Tests

```bash
python -m unittest discover -s tests -v
```

An optional frontend smoke test (needs Node + jsdom) loads the real page in a
DOM, mocks the API and checks the UI wiring, including the embedded-preview
voice path:

```bash
cd tests && npm install jsdom && node frontend_smoke.js
```

The initial repository did not include the referenced PDF, so this UI follows the
supplied Alexa script rather than matching a PDF pixel-for-pixel. Provide the PDF
to tune the layout to that reference.
