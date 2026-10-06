#!/usr/bin/env python3
"""Local web backend for the Alexa-style voice assistant.

The command interpreter is adapted from the supplied script. The browser handles
microphone access and speech output, while this module serves the UI and turns
commands into safe, explicit actions for the frontend to open.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlsplit

import conversation

ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
MAX_REQUEST_BYTES = 16_384
MAX_COMMAND_LENGTH = 1_000
MAX_SESSION_LENGTH = 128


def _clean_command(command: str) -> str:
    """Normalize text and strip the optional Alexa wake word."""
    command = re.sub(r"\balexa\b", " ", command, flags=re.IGNORECASE)
    # Speech and quick typing often drop apostrophes ("whats the time"), so
    # remove them before matching and write patterns without them.
    command = command.replace("'", "").replace("\u2019", "")
    return " ".join(command.lower().split()).strip(" ,.!?\t\n")


# Conversation patterns. These are anchored so that a greeting only matches on
# its own ("hello") and never swallows a real command ("hello, open youtube"
# still opens YouTube). Patterns are written without apostrophes because
# _clean_command strips them.
_GREETING = re.compile(
    r"^(?:good (?:morning|afternoon|evening)|hello|hi+|hey+|yo|hola|namaste|what(?:s| is) up)"
    r"(?: there| alexa| assistant| buddy| friend)?$"
)
_GOODBYE = re.compile(
    r"^(?:bye+(?: bye)?|good ?bye|see (?:you|ya)|good night|catch you later|i(?:m| am) done)$"
)
_HOW_ARE_YOU = re.compile(r"\bhow (?:are you|are things|is it going|you doing|are you doing)\b")
_IDENTITY = re.compile(r"\b(?:who are you|what(?:s| is) your name|your name)\b")
_THANKS = re.compile(
    r"^(?:thanks|thank you|thankyou|thanx|thx|ty|great|awesome|nice|cool)"
    r"(?: a lot| so much| very much| man| bro)?$"
)
_HELP = re.compile(
    r"^(?:help|help me|commands?|what can you do|what do you do|"
    r"what can i (?:do|say|ask)|show me (?:the )?commands?)$"
)
_TIME = re.compile(
    r"\b(?:what(?:s| is) the time|what time is it|current time|time now|tell me the time|clock)\b"
)
_DATE = re.compile(
    r"\b(?:what(?:s| is) the date|what(?:s| is) todays? date|what day is it|"
    r"todays? date|current date|what(?:s| is) the day)\b"
)


_HELP_REPLY = (
    "I can open sites and search for you, one app at a time. Try: 'open YouTube', "
    "or just the one-word keyword 'youtube', 'gmail', 'drive', 'classroom', "
    "'github', 'whatsapp', 'claude', 'gemini', 'chatgpt', 'instagram' or 'google'. "
    "You can also say 'play lofi beats on YouTube' or 'search for Python tutorials'. "
    "Ask me for the time, or say 'stop' to end the session."
)

# Single source of truth for what the assistant can do. The frontend renders
# its quick-launch buttons, command cards and suggestions from /api/commands,
# so the UI can never drift away from what the backend actually understands.
# A test asserts every offered example resolves to a real intent.
CAPABILITIES: tuple[dict[str, object], ...] = (
    {
        "id": "launch",
        "label": "Open a site",
        "hint": "One app at a time: say \"open YouTube\" or just the one-word keyword \"youtube\".",
        "items": (
            {"label": "YouTube", "command": "open youtube", "icon": "play"},
            {"label": "Google", "command": "open google", "icon": "search"},
            {"label": "Gmail", "command": "open gmail", "icon": "mail"},
            {"label": "Google Drive", "command": "open google drive", "icon": "drive"},
            {"label": "Google Classroom", "command": "open google classroom", "icon": "book"},
            {"label": "GitHub", "command": "open github", "icon": "code"},
            {"label": "WhatsApp", "command": "open whatsapp", "icon": "chat"},
            {"label": "Claude", "command": "open claude ai", "icon": "spark"},
            {"label": "Gemini", "command": "open gemini", "icon": "spark"},
            {"label": "ChatGPT", "command": "open chatgpt", "icon": "spark"},
            {"label": "Instagram", "command": "open instagram", "icon": "camera"},
            {"label": "Keyword: drive", "command": "drive", "icon": "drive"},
            {"label": "Keyword: classroom", "command": "classroom", "icon": "book"},
            {"label": "Keyword: gmail", "command": "gmail", "icon": "mail"},
            {"label": "Keyword: whatsapp", "command": "whatsapp", "icon": "chat"},
            {"label": "Keyword: insta", "command": "insta", "icon": "camera"},
        ),
    },
    {
        "id": "play",
        "label": "Play on YouTube",
        "hint": "Name a song or topic and I search YouTube for it.",
        "items": (
            {"label": "lofi beats", "command": "play lofi beats on youtube", "icon": "play"},
            {"label": "study music", "command": "play study music on youtube", "icon": "play"},
            {"label": "workout playlist", "command": "play workout playlist on youtube", "icon": "play"},
        ),
    },
    {
        "id": "search",
        "label": "Search the web",
        "hint": "Look something up without opening a tab first.",
        "items": (
            {"label": "Python tutorials", "command": "search for python tutorials", "icon": "search"},
            {"label": "weather in Ahmedabad", "command": "search for weather in ahmedabad", "icon": "search"},
            {"label": "best study playlists", "command": "search for best study playlists", "icon": "search"},
        ),
    },
    {
        "id": "chat",
        "label": "Just talk",
        "hint": "Real back-and-forth: feelings, jokes, facts, memory of this chat.",
        "items": (
            {"label": "Say hello", "command": "hello", "icon": "wave"},
            {"label": "What can you do", "command": "help", "icon": "info"},
            {"label": "Tell me a joke", "command": "tell me a joke", "icon": "spark"},
            {"label": "I'm tired", "command": "i am tired", "icon": "chat"},
            {"label": "Flip a coin", "command": "flip a coin", "icon": "play"},
            {"label": "Do some maths", "command": "what is 12 * 8", "icon": "info"},
            {"label": "The time", "command": "what time is it", "icon": "clock"},
            {"label": "Today's date", "command": "what is todays date", "icon": "calendar"},
            {"label": "What did I ask", "command": "what did i ask", "icon": "chat"},
        ),
    },
)


# Everything the assistant can open, one app at a time, as:
#   (label, url, open-phrases, one-word keywords)
# The first entry of `open_phrases` is the canonical keyword the UI offers; the
# rest are aliases. `one-word keywords` are matched only when the whole sentence
# is just that word, so "gmail" opens Gmail while "gmail is slow today" still
# falls through to normal conversation. Order matters: more specific phrases
# come first ("open google drive" before "open google").
APPS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "Google Classroom",
        "https://classroom.google.com",
        ("open google classroom", "open classroom"),
        ("classroom", "google classroom"),
    ),
    (
        "Google Drive",
        "https://drive.google.com",
        ("open google drive", "open drive"),
        ("drive", "google drive"),
    ),
    ("Claude", "https://claude.ai", ("open claude ai", "open claude"), ("claude", "claude ai")),
    ("Gemini", "https://gemini.google.com", ("open gemini",), ("gemini",)),
    ("Google", "https://www.google.com", ("open google",), ("google",)),
    ("GitHub", "https://github.com", ("open github",), ("github",)),
    ("WhatsApp", "https://web.whatsapp.com", ("open whatsapp",), ("whatsapp", "whats app", "web whatsapp")),
    # No bare "gmail" in the phrases: the one-word form is handled by the
    # whole-sentence keyword check, so "gmail is slow today" stays chatter.
    (
        "Gmail",
        "https://mail.google.com",
        ("open email", "open gmail"),
        ("gmail", "email", "mail"),
    ),
    ("ChatGPT", "https://chatgpt.com", ("open chatgpt",), ("chatgpt", "gpt", "chat gpt")),
    (
        "Instagram",
        "https://www.instagram.com",
        ("open instagram",),
        ("instagram", "insta"),
    ),
)

# YouTube is matched separately (it also carries a search query), but it still
# answers to its own one-word keywords.
YOUTUBE_KEYWORDS = ("youtube", "you tube", "yt")


def capabilities() -> dict[str, object]:
    """Return a JSON-friendly description of the supported commands."""
    return {
        "categories": [
            {
                "id": category["id"],
                "label": category["label"],
                "hint": category["hint"],
                "items": [dict(item) for item in category["items"]],
            }
            for category in CAPABILITIES
        ]
    }


def _time_and_date(now: datetime) -> str:
    time_text = now.strftime("%I:%M %p").lstrip("0")
    return f"{time_text} on {now:%A}, {now.day} {now:%B %Y}"


def _action(label: str, url: str) -> dict[str, str]:
    return {"type": "open_url", "label": label, "url": url}


def _response(
    command: str,
    reply: str,
    *,
    action: dict[str, str] | None = None,
    status: str = "ready",
    should_continue: bool = True,
) -> dict[str, object]:
    return {
        "command": command,
        "reply": reply,
        "action": action,
        "status": status,
        "should_continue": should_continue,
    }


def process_command(command: str) -> dict[str, object]:
    """Interpret a supported command and return a frontend-friendly response.

    External pages are returned as actions instead of being opened on the
    server: a browser-based backend cannot open a tab on the user's own device.
    The frontend opens the URL by itself as soon as the reply arrives, so the
    command happens without anyone pressing a button.
    """
    if not isinstance(command, str):
        command = ""

    original = command.strip()
    normalized = _clean_command(original)

    if not normalized:
        return _response(
            original,
            "I didn't catch a command. Try typing one or use the microphone.",
            status="empty",
        )

    if re.search(r"\b(exit|stop|quit)\b", normalized):
        return _response(
            original,
            "Goodbye. I'm here whenever you want to start again.",
            status="stopped",
            should_continue=False,
        )

    # Small talk first: these are anchored matches, so real commands such as
    # "hello, open youtube" still fall through to the action handlers below.
    if _GOODBYE.match(normalized):
        return _response(
            original,
            "Goodbye. I'm here whenever you want to start again.",
            status="stopped",
            should_continue=False,
        )

    if _GREETING.match(normalized):
        return _response(
            original,
            "Hello! How can I help you? You can say things like 'open YouTube' "
            "or 'search for Python tutorials', or just ask 'help'.",
            status="smalltalk",
        )

    if _HOW_ARE_YOU.search(normalized):
        return _response(
            original,
            "I'm running well, thank you for asking. What can I do for you?",
            status="smalltalk",
        )

    if _IDENTITY.search(normalized):
        return _response(
            original,
            "I'm Alexa, your voice assistant in this browser. I can open sites "
            "and run searches for you.",
            status="smalltalk",
        )

    if _THANKS.match(normalized):
        return _response(
            original,
            "You're welcome! Anything else I can do?",
            status="smalltalk",
        )

    if _HELP.match(normalized):
        return _response(original, _HELP_REPLY, status="help")

    if _TIME.search(normalized):
        return _response(
            original,
            f"It's {_time_and_date(datetime.now())}.",
            status="time",
        )

    if _DATE.search(normalized):
        return _response(
            original,
            f"Today is {datetime.now():%A}, {datetime.now().day} {datetime.now():%B %Y}.",
            status="time",
        )

    # YouTube carries an optional search query, so it is matched on its own
    # before the plain "open <app>" table below: both the original
    # "open YouTube and play …" form and the natural "play … on YouTube"
    # phrasing, plus the bare keyword "youtube".
    youtube_query = ""
    bare_youtube = normalized in YOUTUBE_KEYWORDS
    open_youtube = re.search(r"\bopen you ?tube\b(.*)$", normalized)
    play_on_youtube = re.search(r"\bplay\s+(.+?)\s+on you ?tube\b", normalized)
    if open_youtube:
        tail = open_youtube.group(1).strip()
        tail = re.sub(r"^(?:and\s+)?play\b", "", tail).strip()
        youtube_query = tail
    elif play_on_youtube:
        youtube_query = play_on_youtube.group(1).strip()

    if open_youtube or play_on_youtube or bare_youtube:
        if youtube_query:
            safe_query = quote_plus(youtube_query)
            return _response(
                original,
                f"Here are YouTube results for {youtube_query}.",
                action=_action(
                    "Search YouTube",
                    f"https://www.youtube.com/results?search_query={safe_query}",
                ),
                status="action",
            )
        return _response(
            original,
            "Opening YouTube.",
            action=_action("Open YouTube", "https://www.youtube.com"),
            status="action",
        )

    if re.search(r"\bsearch\b", normalized):
        query = re.sub(r"^.*?\bsearch\b", "", normalized, count=1).strip()
        query = re.sub(r"^(?:for|about)\s+", "", query).strip()
        if query:
            return _response(
                original,
                f"Searching the web for {query}.",
                action=_action(
                    "View search results",
                    f"https://www.google.com/search?q={quote_plus(query)}",
                ),
                status="action",
            )
        return _response(
            original,
            "Opening Google so you can search.",
            action=_action("Open Google", "https://www.google.com"),
            status="action",
        )

    # One app at a time: either the "open …" phrase anywhere in the sentence, or
    # the app's one-word keyword when that is all that was said ("gmail").
    for label, url, open_phrases, one_words in APPS:
        if any(phrase in normalized for phrase in open_phrases) or normalized in one_words:
            return _response(
                original,
                f"Opening {label}.",
                action=_action(f"Open {label}", url),
                status="action",
            )

    if "open settings" in normalized:
        return _response(
            original,
            "Settings can only be opened from a desktop assistant. This browser version can't change your device settings.",
            status="unsupported",
        )

    return _response(
        original,
        "I don't know that one yet. Try 'open YouTube', 'play lofi beats on YouTube', "
        "'search for Python tutorials', or say 'help' to hear what I can do.",
        status="unknown",
    )


# One engine for the process: it owns the per-session conversation memory.
ENGINE = conversation.ConversationEngine(
    intent_handler=process_command,
    normalize=_clean_command,
    llm=conversation.LlmChat.from_env(),
)


def handle_command(command: str, session_id: str | None = None) -> dict[str, object]:
    """Conversational entry point used by the HTTP API (keeps session memory)."""
    return ENGINE.respond(command, session_id)


def reset_session(session_id: str | None = None) -> None:
    """Forget a conversation, e.g. when the user clears the log."""
    ENGINE.sessions.reset(session_id)


class AssistantRequestHandler(BaseHTTPRequestHandler):
    """Serve the single-page frontend and its small JSON API."""

    server_version = "BackendAssistant/1.0"

    def log_message(self, format: str, *args: object) -> None:
        # Keep server output useful without exposing noisy default request logs.
        sys.stdout.write("[backend] " + (format % args) + "\n")

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: dict[str, object], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802 - method name required by BaseHTTPRequestHandler
        request_path = urlsplit(self.path).path
        if request_path == "/api/health":
            self._send_json({"status": "ok", "assistant": "Alexa"})
            return

        if request_path == "/api/commands":
            self._send_json(capabilities())
            return

        relative_path = unquote(request_path).lstrip("/") or "index.html"
        target = (STATIC_ROOT / relative_path).resolve()
        static_root = STATIC_ROOT.resolve()
        if target != static_root and static_root not in target.parents:
            self._send_json({"error": "Not found"}, 404)
            return
        if not target.is_file():
            self._send_json({"error": "Not found"}, 404)
            return

        content_type, _ = mimetypes.guess_type(target.name)
        if target.suffix == ".js":
            content_type = "text/javascript"
        self._send_bytes(
            target.read_bytes(),
            f"{content_type or 'application/octet-stream'}; charset=utf-8",
        )

    def do_POST(self) -> None:  # noqa: N802 - method name required by BaseHTTPRequestHandler
        route = urlsplit(self.path).path
        if route not in {"/api/command", "/api/reset"}:
            self._send_json({"error": "Not found"}, 404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "Invalid Content-Length"}, 400)
            return
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json({"error": "Request is too large"}, 413)
            return

        try:
            raw = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
            payload = json.loads(raw or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"error": "Expected a JSON request body"}, 400)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "Expected a JSON object"}, 400)
            return

        session_id = payload.get("session")
        if session_id is not None and (not isinstance(session_id, str) or len(session_id) > MAX_SESSION_LENGTH):
            self._send_json({"error": "The 'session' field must be a short string"}, 400)
            return

        if route == "/api/reset":
            reset_session(session_id)
            self._send_json({"status": "ok", "session": session_id or "anonymous"})
            return

        if not isinstance(payload.get("command"), str):
            self._send_json({"error": "The 'command' field must be a string"}, 400)
            return

        command = payload["command"]
        if len(command) > MAX_COMMAND_LENGTH:
            self._send_json({"error": "Command is too long"}, 422)
            return

        self._send_json(handle_command(command, session_id))


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the frontend and API on one origin."""
    server = ThreadingHTTPServer((host, port), AssistantRequestHandler)
    # 0.0.0.0 is a "listen on every interface" bind address; it is not a
    # browsable address, so show something clickable instead.
    display_host = "localhost" if host in {"0.0.0.0", "::", ""} else host
    print(f"Backend assistant is listening on {host}:{port}")
    print(f"Open http://{display_host}:{port} in a browser on this machine.")
    print("(In a hosted sandbox, open the forwarded preview link for this port instead.)")
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down the assistant server.")
    finally:
        server.server_close()


def run_text_cli() -> None:
    """Offer a lightweight text-only CLI using the same command interpreter."""
    print("Alexa: Hello. I am Alexa. How can I help you?")
    while True:
        try:
            command = input("You (type command): ")
        except (EOFError, KeyboardInterrupt):
            print("\nAlexa: Goodbye.")
            return

        result = process_command(command)
        print(f"Alexa: {result['reply']}")
        action = result.get("action")
        if isinstance(action, dict):
            url = action.get("url")
            if isinstance(url, str):
                print(f"Opening: {url}")
                webbrowser.open(url)
        if not result["should_continue"]:
            return


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Alexa-style assistant frontend.")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument(
        "--cli",
        action="store_true",
        help="use the text-only terminal interface instead of the web app",
    )
    args = parser.parse_args(argv)
    if args.cli:
        run_text_cli()
    else:
        run_server(args.host, args.port)


if __name__ == "__main__":
    main()
