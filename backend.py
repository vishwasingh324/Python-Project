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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
MAX_REQUEST_BYTES = 16_384
MAX_COMMAND_LENGTH = 1_000


def _clean_command(command: str) -> str:
    """Normalize text and strip the optional Alexa wake word."""
    command = re.sub(r"\balexa\b", " ", command, flags=re.IGNORECASE)
    return " ".join(command.lower().split()).strip(" ,.!?\t\n")


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

    External pages are returned as links instead of being opened on the server:
    a browser-based backend cannot open a tab on the user's own device.
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

    if "open google classroom" in normalized:
        return _response(
            original,
            "Opening Google Classroom.",
            action=_action("Open Google Classroom", "https://classroom.google.com"),
            status="action",
        )

    # Support both the original "open YouTube and play …" form and the more
    # natural "play … on YouTube" phrasing.
    youtube_query = ""
    open_youtube = re.search(r"\bopen youtube\b(.*)$", normalized)
    play_on_youtube = re.search(r"\bplay\s+(.+?)\s+on youtube\b", normalized)
    if open_youtube:
        tail = open_youtube.group(1).strip()
        tail = re.sub(r"^(?:and\s+)?play\b", "", tail).strip()
        youtube_query = tail
    elif play_on_youtube:
        youtube_query = play_on_youtube.group(1).strip()

    if open_youtube or play_on_youtube:
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

    destinations: tuple[tuple[tuple[str, ...], str, str], ...] = (
        (("open google drive", "open drive"), "Google Drive", "https://drive.google.com"),
        (("open claude ai", "open claude"), "Claude", "https://claude.ai"),
        (("open gemini",), "Gemini", "https://gemini.google.com"),
        (("open google",), "Google", "https://www.google.com"),
        (("open github",), "GitHub", "https://github.com"),
        (("open whatsapp",), "WhatsApp", "https://web.whatsapp.com"),
        (("open email", "open gmail", "gmail"), "Gmail", "https://mail.google.com"),
        (("open chatgpt",), "ChatGPT", "https://chatgpt.com"),
        (("open instagram",), "Instagram", "https://www.instagram.com"),
    )
    for triggers, label, url in destinations:
        if any(trigger in normalized for trigger in triggers):
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
        "Command not recognized. Try one of the quick commands or ask me to search the web.",
        status="unknown",
    )


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
        if urlsplit(self.path).path != "/api/command":
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
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"error": "Expected a JSON request body"}, 400)
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("command"), str):
            self._send_json({"error": "The 'command' field must be a string"}, 400)
            return

        command = payload["command"]
        if len(command) > MAX_COMMAND_LENGTH:
            self._send_json({"error": "Command is too long"}, 422)
            return

        self._send_json(process_command(command))


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the frontend and API on one origin."""
    server = ThreadingHTTPServer((host, port), AssistantRequestHandler)
    print(f"Backend assistant is running at http://{host}:{port}")
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
