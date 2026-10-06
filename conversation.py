#!/usr/bin/env python3
"""Conversation layer for the Alexa assistant.

The command interpreter in ``backend.py`` answers a fixed set of intents. This
module wraps it with everything needed for ordinary back-and-forth:

* **Session memory** — the last command, reply, action and a short transcript, so
  follow-ups like "open it", "search that", "again" or "what did I ask?" work.
* **Small talk** — greetings, feelings, compliments, apologies, jokes, coins,
  dice, safe arithmetic, "are you an AI?" and dozens of other everyday phrasings,
  all answered locally with no network and no API key.
* **A conversational fallback** — an unknown sentence no longer produces
  "Command not recognized"; it is answered in prose, and if it looks like a
  question it comes with a "search the web for it" action.
* **Optional LLM chat** — if ``ANTHROPIC_API_KEY`` or ``OPENAI_API_KEY`` is set,
  free-form sentences are answered by that model instead. Without a key (or if
  the call fails) everything still works offline.

Only the standard library is used.
"""

from __future__ import annotations

import ast
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

# ─────────────────────────────── session memory ───────────────────────────────

SESSION_TTL_SECONDS = 2 * 60 * 60  # forget idle conversations after two hours
MAX_SESSIONS = 500
TRANSCRIPT_LIMIT = 12


class Session:
    """Remembered state for one browser conversation."""

    def __init__(self) -> None:
        self.transcript: list[tuple[str, str]] = []  # (role, text)
        self.last_command = ""
        self.last_reply = ""
        self.last_action: dict[str, str] | None = None
        self.last_query = ""
        self.updated = time.time()

    def remember_turn(self, role: str, text: str) -> None:
        self.transcript.append((role, text))
        del self.transcript[:-TRANSCRIPT_LIMIT]
        self.updated = time.time()

    def last_user_text(self) -> str:
        for role, text in reversed(self.transcript):
            if role == "user":
                return text
        return ""

    def last_assistant_text(self) -> str:
        for role, text in reversed(self.transcript):
            if role == "assistant":
                return text
        return ""


class SessionStore:
    """Small bounded store of live sessions, keyed by an id from the client."""

    def __init__(self, ttl: int = SESSION_TTL_SECONDS, max_sessions: int = MAX_SESSIONS) -> None:
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl
        self._max_sessions = max_sessions

    def _prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        stale = [key for key, session in self._sessions.items() if now - session.updated > self._ttl]
        for key in stale:
            del self._sessions[key]
        # If still too many, drop the least recently used.
        if len(self._sessions) > self._max_sessions:
            ordered = sorted(self._sessions.items(), key=lambda item: item[1].updated)
            for key, _ in ordered[: len(self._sessions) - self._max_sessions]:
                del self._sessions[key]

    def get(self, session_id: str | None) -> Session:
        self._prune()
        key = session_id or "anonymous"
        session = self._sessions.get(key)
        if session is None:
            session = Session()
            self._sessions[key] = session
        return session

    def reset(self, session_id: str | None) -> None:
        self._sessions.pop(session_id or "anonymous", None)

    def count(self) -> int:
        return len(self._sessions)


# ─────────────────────────────── safe arithmetic ───────────────────────────────

_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}
_UNARY_OPS = {ast.UAdd: lambda a: a, ast.USub: lambda a: -a}


_TOPIC_STOPWORDS = frozenset(
    {
        "how", "what", "whats", "who", "when", "where", "why", "which", "is", "are", "was",
        "do", "does", "did", "can", "could", "should", "would", "will", "the", "and", "for",
        "you", "your", "me", "my", "our", "their", "his", "her", "its", "it", "this", "that",
        "there", "here", "with", "about", "into", "from", "get", "got", "give", "want", "need",
        "please", "tell", "show", "help", "know", "like", "just", "some", "any", "not",
    }
)


class MathError(ValueError):
    """Raised when an expression is not safe or not computable."""


def safe_math(expression: str) -> float:
    """Evaluate a small arithmetic expression without using eval()."""
    expression = expression.replace("x", "*").replace("^", "**").replace("÷", "/")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:  # pragma: no cover - message only
        raise MathError("I couldn't read that as a sum.") from error

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise MathError("That exponent is too large for me.")
            return _BIN_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](walk(node.operand))
        raise MathError("I can only do plain arithmetic.")

    result = walk(tree)
    if result != result or result in (float("inf"), float("-inf")):  # NaN / inf
        raise MathError("That doesn't come out to a number.")
    return result


def format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.6g}"


# ─────────────────────────────── small talk ───────────────────────────────

JOKES = (
    "Why did the Python programmer need glasses? Because they couldn't C.",
    "There are only two hard things in computing: cache invalidation, naming things, and off-by-one errors.",
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break, and it said: no problem, I'll go to sleep.",
    "A SQL query walks into a bar, approaches two tables and asks: may I join you?",
    "Why was the JavaScript developer sad? Because they didn't know how to null their feelings.",
    "I would tell you a UDP joke, but you might not get it.",
)

FACTS = (
    "The first computer bug was a real moth, found in a Harvard Mark II relay in 1947.",
    "Python was named after Monty Python, not the snake.",
    "The word 'robot' comes from the Czech 'robota', meaning forced labour.",
    "There are more possible chess games than atoms in the observable universe.",
    "A day on Venus is longer than its year.",
)

FEELINGS = {
    "sad": "I'm sorry you're feeling low. A short walk or a glass of water helps more than people expect. Want me to play something cheerful on YouTube?",
    "tired": "Sounds like you need a break. Want me to queue up some calm music on YouTube?",
    "stressed": "That's a lot to carry. Try naming the one next thing you need to do — I can search for help with it.",
    "happy": "Love to hear it. What are we celebrating?",
    "bored": "Then let's fix that — say 'tell me a joke' or 'play lofi beats on YouTube'.",
    "angry": "That sounds frustrating. Tell me what happened, or I can search for a solution.",
    "sick": "Sorry to hear that — rest up. I can search for remedies if you like.",
    "hungry": "Then go eat! I can search for something quick to make.",
    "great": "Glad to hear it. What can I do for you?",
    "good": "Nice one. What's next?",
    "ok": "Alright. What would you like to do?",
}

_SMALL_TALK: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"\b(?:tell me a|say a|know any) joke\b|\bmake me laugh\b|\bsomething funny\b"),
        JOKES,
    ),
    (
        re.compile(r"\b(?:tell me|give me|share) (?:a |an |some )?(?:fun )?fact\b|\binteresting fact\b|\bdid you know\b"),
        FACTS,
    ),
    (
        re.compile(r"\b(?:i am|im|i feel|feeling) (?:a bit |very |really |so )?(?P<mood>sad|tired|stressed|happy|bored|angry|sick|hungry|great|good|ok)\b"),
        (),  # handled specially by _mood_reply
    ),
    (
        re.compile(r"\b(?:are you|you are|youre) (?:an? )?(?:real|human|robot|machine|ai|bot|person)\b|\bare you alive\b|\bdo you have feelings\b"),
        (
            "I'm a small program running on your own machine — no feelings, but I do try to be useful. "
            "Everything I answer locally works offline.",
            "Not human and not a chatbot model by default: I'm a local Python assistant. "
            "If you set an API key I can hand free-form questions to a language model.",
        ),
    ),
    (
        re.compile(r"\bwho (?:made|created|built|wrote|designed) you\b|\bwho is your (?:maker|creator|developer)\b|\byour (?:maker|creator|developer)\b"),
        ("This project is a small browser frontend plus a Python backend — built as a student project. "
         "The command interpreter, the conversation layer and the interface are all open source in your repo.",),
    ),
    (
        re.compile(r"\bhow (?:old are you|long have you been)\b|\byour age\b"),
        ("I'm as old as the process you started — a few minutes, most likely. I don't keep memories between runs.",),
    ),
    (
        re.compile(r"\bwhere (?:are you|do you live|are you from)\b"),
        ("Right here on your machine, listening on a local port. Nothing about your commands leaves this computer unless you ask me to search the web.",),
    ),
    (
        re.compile(r"\bhow (?:do you work|are you built|does this work)\b"),
        ("You send me a command, a small Python interpreter matches it to an intent, and I send back a reply plus a safe link for your browser to open. "
         "The frontend renders whatever /api/commands advertises.",),
    ),
    (
        re.compile(r"\bcan you (?:sing|dance|rap)\b|\bsing (?:me )?(?:a )?(?:song|something)\b"),
        ("I can't sing — my voice is your browser's text-to-speech, which only speaks. Turn Sound on and I'll say anything you like, flat and tuneless.",),
    ),
    (
        re.compile(r"\b(?:do you|can you) (?:love|like) me\b|\bi love you\b|\bdo you like me\b"),
        ("That's kind. I'm fond of everyone who says hello — in a strictly programmatic way.",),
    ),
    (
        re.compile(r"\bwhat(?:s| is) your favou?rite\b|\bdo you have a favou?rite\b"),
        ("I don't have favourites — I don't get to choose. But I do like it when a command matches on the first try.",),
    ),
    (
        re.compile(r"\bwhat(?:s| is) the meaning of life\b"),
        ("Forty-two, according to Deep Thought. Beyond that, I'd say being kind and shipping your project.",),
    ),
    (
        re.compile(r"\bwhat are you doing\b|\bwhat(?:s| is) up with you\b|\bwhat are you up to\b"),
        ("Just waiting for your next command — happily idle. What do you need?",),
    ),
    (
        re.compile(r"\b(?:i(?:m| am) )?(?:sorry|apologise|apologize)\b|\b(?:my|me) bad\b|\boops\b"),
        ("No need to apologise — nothing here breaks. What would you like to try?",),
    ),
    (
        re.compile(r"\b(?:you(?:re| are) )?(?:awesome|amazing|brilliant|great|the best|smart|clever|helpful)\b|\bwell done\b|\bgreat job\b|\bnice work\b"),
        ("Thank you — I'll take that. Anything else you'd like me to open or look up?",),
    ),
    (
        re.compile(r"\b(?:that|this|it) (?:is|was) (?:wrong|useless|stupid|broken|bad)\b|\byou(?:re| are) (?:wrong|useless|stupid|broken)\b"),
        ("Fair enough — tell me what you expected and I'll try again. If a command isn't understood, 'help' lists everything I know.",),
    ),
    (
        re.compile(r"\b(?:say )?(?:something|anything)\b|\btalk to me\b|\btell me something\b"),
        ("Here's something: you can say 'play study music on YouTube', 'search for the Python docs', or ask me for the time. What sounds useful?",),
    ),
    (
        re.compile(r"\bwhat can you (?:not|not do|do)\b|\bwhat are you (?:bad|good) at\b|\blimitations?\b|\byour limits?\b"),
        ("I open sites and run searches, answer greetings, feelings, jokes, the time and the date, and do simple arithmetic. "
         "I can't control your device settings or remember anything after the session ends.",),
    ),
    (
        re.compile(r"\b(?:good|nice) (?:one|job|work)\b|\bcheers\b|\bhigh five\b"),
        ("Right back at you. What's next?",),
    ),
    (
        re.compile(r"\b(?:you there|are you there|hello again|you up)\b"),
        ("Still here. What do you need?",),
    ),
    (
        re.compile(r"\bweather\b(?!.*\bsearch\b)"),
        ("I don't have a weather feed — that would need an online service. Say 'search for the weather in <your city>' "
         "and I'll hand you the search link.",),
    ),
    (
        re.compile(r"\b(?:news|headlines)\b"),
        ("I can't fetch news on my own, but I can build the search: try 'search for today's headlines'.",),
    ),
    (
        re.compile(r"\b(?:what(?:s| is) the )?(?:date|day) (?:of )?(?:my|the) (?:birth)?day\b"),
        ("I can't store personal details — nothing about you is kept after the session ends.",),
    ),
    (
        re.compile(r"\bhelp me (?:decide|choose|pick)\b|\bwhat should i (?:do|eat|watch)\b"),
        ("I'm better at looking things up than deciding. Want me to search for ideas? Just say 'search for ...'.",),
    ),
)


class ConversationEngine:
    """Turn a single command + session into a conversational response."""

    def __init__(
        self,
        intent_handler: Callable[[str], dict[str, object]],
        normalize: Callable[[str], str],
        llm: "LlmChat | None" = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._intent_handler = intent_handler
        self._normalize = normalize
        self._llm = llm
        self._rng = rng or random.Random()
        self._clock = clock
        self.sessions = SessionStore()

    # ── response helpers ──────────────────────────────────────────────────
    @staticmethod
    def _reply(command: str, reply: str, status: str, **extra: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "command": command,
            "reply": reply,
            "action": None,
            "status": status,
            "should_continue": True,
        }
        payload.update(extra)
        return payload

    def _pick(self, options: tuple[str, ...]) -> str:
        return self._rng.choice(options)

    # ── main entry point ─────────────────────────────────────────────────
    def respond(self, command: str, session_id: str | None = None) -> dict[str, object]:
        original = command if isinstance(command, str) else ""
        session = self.sessions.get(session_id)
        normalized = self._normalize(original)

        if not normalized:
            return self._intent_handler(original)

        result = (
            self._handle_meta(normalized, original, session, session_id)
            or self._handle_follow_up(normalized, original, session)
            or self._handle_small_talk(normalized, original)
            or self._handle_fun(normalized, original)
            or self._handle_intent(original, session)
        )

        if result is None:
            result = self._handle_freeform(original, normalized, session)

        # Handlers read the transcript, so the current turn is recorded only
        # once the answer exists -- otherwise "what did I ask?" sees itself.
        if not result.pop("_forget_transcript", False):
            session.remember_turn("user", original.strip())

        session.last_reply = str(result.get("reply", ""))
        session.remember_turn("assistant", session.last_reply)
        result["session"] = session_id or "anonymous"
        if self._llm is not None:
            result["llm"] = "on" if result.get("status") == "chat_llm" else "available"
        return result

    # ── conversation bookkeeping ─────────────────────────────────────────
    def _handle_meta(
        self, normalized: str, original: str, session: Session, session_id: str | None
    ) -> dict[str, object] | None:
        if re.search(r"\b(?:what did i (?:ask|say)|my last (?:command|question)|repeat my last)\b", normalized):
            if session.last_user_text():
                return self._reply(original, f"You said: “{session.last_user_text()}”.", "recall")
            return self._reply(original, "You haven't asked me anything yet — this is the first one.", "recall")

        if re.search(r"\b(?:what did you (?:say|just say)|your last reply|repeat that reply)\b", normalized):
            if session.last_assistant_text():
                return self._reply(original, f"I said: “{session.last_assistant_text()}”.", "recall")
            return self._reply(original, "I haven't replied to anything yet in this session.", "recall")

        if re.search(r"\b(?:what have we|what did we) (?:talk|discuss|say)\w*(?: about)?\b", normalized):
            user_turns = [text for role, text in session.transcript if role == "user"]
            if len(user_turns) <= 1:
                return self._reply(original, "We've only just started, so there isn't much to summarise yet.", "recall")
            recent = "; ".join(f"“{text}”" for text in user_turns[-3:])
            return self._reply(original, f"So far you've asked me: {recent}.", "recall")

        if re.search(r"\b(?:forget|clear|reset) (?:this|our|the) (?:conversation|chat|history|session)\b", normalized):
            session.transcript.clear()
            session.last_action = None
            session.last_query = ""
            session.last_command = ""
            self.sessions.reset(session_id)
            return self._reply(
                original,
                "Done — I've forgotten this conversation. What shall we start with?",
                "recall",
                _forget_transcript=True,
            )

        if re.search(r"\b(?:do you remember|remember what)\b", normalized):
            if session.last_user_text():
                return self._reply(original, f"I remember this session. You last said “{session.last_user_text()}”.", "recall")
            return self._reply(original, "Nothing to remember yet.", "recall")

        return None

    def _handle_follow_up(self, normalized: str, original: str, session: Session) -> dict[str, object] | None:
        wants_open = re.search(r"\bopen (?:it|that|this|the (?:link|page|site|first one|result))\b", normalized)
        wants_search = re.search(r"\bsearch (?:it|that|this|for that|for it)\b", normalized)
        wants_again = re.search(r"^(?:again|do (?:that|it) again|repeat (?:that|it)|one more time)\b", normalized)
        wants_play = re.search(r"\bplay (?:it|that|this)\b", normalized)

        if wants_open:
            action = session.last_action
            if action and isinstance(action.get("url"), str):
                label = str(action.get("label", "") or "that link")
                if label.lower().startswith("open "):
                    label = label[5:]
                return self._reply(original, f"Opening {label} again.", "action", action=action)
            if session.last_query:
                return self._reply(
                    original,
                    "I don't have a link from before, so here's the search instead.",
                    "action",
                    action={
                        "type": "open_url",
                        "label": "View search results",
                        "url": f"https://www.google.com/search?q={urllib.parse.quote_plus(session.last_query)}",
                    },
                )
            return self._reply(original, "There's no link to reopen yet. Ask me to open something first, like 'open YouTube'.", "followup")

        if wants_search:
            if session.last_query:
                return self._reply(
                    original,
                    f"Searching the web for {session.last_query}.",
                    "action",
                    action={
                        "type": "open_url",
                        "label": "View search results",
                        "url": f"https://www.google.com/search?q={urllib.parse.quote_plus(session.last_query)}",
                    },
                )
            return self._reply(original, "I don't have a topic to search yet — tell me what to look for.", "followup")

        if wants_play:
            if session.last_query:
                return self._reply(
                    original,
                    f"Here are YouTube results for {session.last_query}.",
                    "action",
                    action={
                        "type": "open_url",
                        "label": "Search YouTube",
                        "url": f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(session.last_query)}",
                    },
                )
            return self._reply(original, "Tell me a song or topic first — like 'play lofi beats on YouTube'.", "followup")

        if wants_again:
            if session.last_command:
                previous = session.last_command
                result = self._intent_handler(previous)
                if result.get("status") != "unknown":
                    result["reply"] = f"{result.get('reply', '')} (again)".strip()
                    result["status"] = "followup" if result.get("action") else result.get("status", "followup")
                    return result
            return self._reply(original, "There's nothing to repeat yet — ask me something first.", "followup")

        return None

    def _handle_small_talk(self, normalized: str, original: str) -> dict[str, object] | None:
        for pattern, options in _SMALL_TALK:
            match = pattern.search(normalized)
            if not match:
                continue
            if not options:  # the feelings pattern
                mood = match.groupdict().get("mood", "")
                return self._reply(original, FEELINGS.get(mood, "Thanks for telling me."), "chat")
            return self._reply(original, self._pick(options), "chat")
        return None

    def _handle_fun(self, normalized: str, original: str) -> dict[str, object] | None:
        if re.search(r"\b(?:flip|toss) (?:a )?coin\b|\bcoin (?:flip|toss)\b", normalized):
            return self._reply(original, f"It's {self._rng.choice(('heads', 'tails'))}.", "chat")

        roll = re.search(r"\broll (?:a )?(?:die|dice)(?: with (\d+) sides)?\b", normalized)
        if roll:
            sides = int(roll.group(1)) if roll.group(1) else 6
            if 2 <= sides <= 1000:
                return self._reply(original, f"You rolled a {self._rng.randint(1, sides)} (out of {sides}).", "chat")

        between = re.search(r"\b(?:random|pick a|pick some|choose a) ?number between (\d+) and (\d+)\b", normalized)
        if between:
            low, high = sorted((int(between.group(1)), int(between.group(2))))
            return self._reply(original, f"{self._rng.randint(low, high)} — somewhere between {low} and {high}.", "chat")

        if re.search(r"\b(?:pick|choose) (?:a|one) (?:random )?(?:number|word)\b|\brandom number\b", normalized):
            return self._reply(original, f"{self._rng.randint(1, 100)}.", "chat")

        sum_match = re.search(
            r"^(?:what(?:s| is)|calculate|compute|how much is|how many is)\s+([0-9][0-9\s+\-*/().^x÷%]*)\??$",
            normalized,
        )
        if sum_match:
            try:
                value = safe_math(sum_match.group(1))
            except MathError as error:
                return self._reply(original, f"{error} Try something like 'what is 12 * 8'.", "chat")
            return self._reply(original, f"{format_number(value)}.", "chat")

        return None

    def _handle_intent(self, original: str, session: Session) -> dict[str, object] | None:
        result = self._intent_handler(original)
        if result.get("status") == "unknown":
            return None
        session.last_command = original.strip()
        action = result.get("action")
        if isinstance(action, dict):
            session.last_action = {"type": str(action.get("type", "open_url")), "label": str(action.get("label", "")), "url": str(action.get("url", ""))}
            session.last_query = self._query_from_action(str(action.get("url", ""))) or session.last_query
        return result

    @staticmethod
    def _query_from_action(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        params = urllib.parse.parse_qs(parsed.query)
        for key in ("search_query", "q"):
            if params.get(key):
                return params[key][0]
        return ""

    def _handle_freeform(self, original: str, normalized: str, session: Session) -> dict[str, object]:
        """Nothing matched: answer in prose instead of dead-ending."""
        if self._llm is not None and self._llm.available:
            try:
                reply = self._llm.reply(self._llm_history(session), original.strip())
                if reply:
                    return self._reply(original, reply, "chat_llm")
            except LlmError:
                pass  # fall through to the local answer

        question = re.match(r"^(?:what|who|when|where|why|how|which|is|are|do|does|did|can|could|should|will)\b", normalized)
        words = [
            word
            for word in re.split(r"\W+", normalized)
            if len(word) > 2 and word not in _TOPIC_STOPWORDS
        ]
        topic = " ".join(words[:8]) or normalized

        if question:
            return self._reply(
                original,
                f"I can't answer that from my own knowledge — I'm a small local assistant, not a knowledge model. "
                f"Here's a web search for “{topic}”.",
                "search_suggested",
                action={
                    "type": "open_url",
                    "label": "Search the web",
                    "url": f"https://www.google.com/search?q={urllib.parse.quote_plus(topic)}",
                },
            )

        options = (
            f"I don't have an answer for “{original.strip()}” myself — I'm a small local assistant, not a knowledge model.",
            "I can't work that one out on my own, but I can look it up for you.",
            "That's beyond what I know locally. Here's a search for it if it helps.",
        )
        reply = self._pick(options)
        if topic:
            reply += f" Want me to search for “{topic}”?"
        return self._reply(
            original,
            reply,
            "chat",
            action={
                "type": "open_url",
                "label": "Search the web",
                "url": f"https://www.google.com/search?q={urllib.parse.quote_plus(topic or normalized)}",
            },
        )

    def _llm_history(self, session: Session) -> list[dict[str, str]]:
        return [{"role": role, "content": text} for role, text in session.transcript[:-1]]


# ─────────────────────────────── optional LLM chat ───────────────────────────────


class LlmError(RuntimeError):
    """Raised when the optional language-model backend cannot answer."""


SYSTEM_PROMPT = (
    "You are Alexa, a friendly assistant embedded in a small local Python web app. "
    "Reply conversationally and concisely (one or two short sentences). "
    "The app can open sites and run web searches for the user; if a request needs that, "
    "tell them the exact phrase to type, such as \"open youtube\" or \"search for python decorators\". "
    "Never claim to have performed an action yourself."
)


def _http_post_json(url: str, headers: dict[str, str], body: dict[str, object], timeout: float = 20.0) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
        return json.loads(response.read().decode("utf-8"))


class LlmChat:
    """Free-form chat through Anthropic or OpenAI, used only when a key exists.

    Configure with environment variables (nothing is read from disk):

        ALEXA_LLM_PROVIDER=anthropic|openai   (default: whichever key is present)
        ANTHROPIC_API_KEY=...                 (model: ALEXA_LLM_MODEL, default claude-3-5-haiku-latest)
        OPENAI_API_KEY=...                    (model: ALEXA_LLM_MODEL, default gpt-4o-mini)

    Without a key the engine simply answers locally, so the assistant still works
    offline. Pass ``transport`` to inject a fake in tests.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        transport: Callable[[str, dict[str, str], dict[str, object]], dict[str, object]] = _http_post_json,
    ) -> None:
        self.provider = provider
        self.model = model
        self._api_key = api_key
        self._transport = transport

    @property
    def available(self) -> bool:
        return bool(self._api_key and self.provider)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LlmChat | None":
        env = os.environ if env is None else env
        provider = (env.get("ALEXA_LLM_PROVIDER") or "").strip().lower()
        anthropic_key = (env.get("ANTHROPIC_API_KEY") or "").strip()
        openai_key = (env.get("OPENAI_API_KEY") or "").strip()
        if not provider:
            provider = "anthropic" if anthropic_key else "openai" if openai_key else ""
        key = anthropic_key if provider == "anthropic" else openai_key
        if not provider or not key:
            return None
        default_model = "claude-3-5-haiku-latest" if provider == "anthropic" else "gpt-4o-mini"
        return cls(provider, key, (env.get("ALEXA_LLM_MODEL") or "").strip() or default_model)

    def reply(self, history: list[dict[str, str]], prompt: str) -> str:
        if not self.available:
            raise LlmError("no api key configured")
        try:
            if self.provider == "anthropic":
                return self._anthropic(history, prompt)
            if self.provider == "openai":
                return self._openai(history, prompt)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as error:
            raise LlmError(str(error)) from error
        raise LlmError(f"unknown provider {self.provider!r}")

    def _anthropic(self, history: list[dict[str, str]], prompt: str) -> str:
        messages = [message for message in history if message.get("content")][-8:]
        messages.append({"role": "user", "content": prompt})
        data = self._transport(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
            {"model": self.model, "max_tokens": 400, "system": SYSTEM_PROMPT, "messages": messages},
        )
        blocks = data.get("content") or []
        if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict):
            return str(blocks[0].get("text", "")).strip()
        raise LlmError("unexpected response shape")

    def _openai(self, history: list[dict[str, str]], prompt: str) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(message for message in history if message.get("content"))
        messages.append({"role": "user", "content": prompt})
        data = self._transport(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {self._api_key}"},
            {"model": self.model, "messages": messages, "max_tokens": 400},
        )
        choices = data.get("choices") or []
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            return str(message.get("content", "")).strip()
        raise LlmError("unexpected response shape")

