"""Tests for the conversational layer: session memory, small talk, free-form."""

import json
import random
import unittest
import urllib.error

import conversation
from backend import _clean_command, capabilities, process_command
from conversation import ConversationEngine, LlmChat, LlmError, SessionStore, MathError, safe_math


def make_engine(**kwargs) -> ConversationEngine:
    return ConversationEngine(
        intent_handler=process_command,
        normalize=_clean_command,
        rng=random.Random(7),
        **kwargs,
    )


class SafeMathTests(unittest.TestCase):
    def test_basic_arithmetic(self):
        self.assertEqual(safe_math("12 * 8"), 96)
        self.assertEqual(safe_math("2 + 2"), 4)
        self.assertEqual(safe_math("7 / 2"), 3.5)
        self.assertEqual(safe_math("2 ^ 5"), 32)

    def test_rejects_dangerous_or_huge_input(self):
        with self.assertRaises(MathError):
            safe_math("__import__('os').system('echo hi')")
        with self.assertRaises(MathError):
            safe_math("9 ** 999")


class SmallTalkTests(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def ask(self, text, session="s1"):
        return self.engine.respond(text, session)

    def test_joke_and_fact(self):
        for text in ("tell me a joke", "say something funny", "make me laugh"):
            with self.subTest(text=text):
                result = self.ask(text)
                self.assertEqual(result["status"], "chat")
                self.assertTrue(result["reply"])

    def test_feelings_are_acknowledged(self):
        tired = self.ask("I am tired")
        self.assertEqual(tired["status"], "chat")
        self.assertIn("break", tired["reply"].lower())
        sad = self.ask("i feel sad today")
        self.assertIn("sorry", sad["reply"].lower())

    def test_identity_questions_are_honest(self):
        for text in ("are you human", "are you an ai", "do you have feelings"):
            with self.subTest(text=text):
                result = self.ask(text)
                self.assertEqual(result["status"], "chat")

    def test_praise_and_apology(self):
        self.assertEqual(self.ask("you are awesome")["status"], "chat")
        self.assertEqual(self.ask("sorry, my bad")["status"], "chat")

    def test_weather_admits_limit_and_offers_search(self):
        result = self.ask("what is the weather like")
        self.assertEqual(result["status"], "chat")
        self.assertIn("search", result["reply"].lower())

    def test_fun_commands_use_the_rng(self):
        coin = self.ask("flip a coin")
        self.assertIn(coin["reply"].split()[-1].strip("."), {"heads", "tails"})
        self.assertIn("rolled", self.ask("roll a dice")["reply"])
        self.assertTrue(self.ask("what is 12 * 8")["reply"].startswith("96"))
        self.assertTrue(self.ask("pick a random number")["reply"])

    def test_maths_error_is_explained(self):
        result = self.ask("what is 9 ^ 999")
        self.assertEqual(result["status"], "chat")
        self.assertIn("too large", result["reply"].lower())


class SessionMemoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def test_follow_up_open_it_reuses_the_last_link(self):
        first = self.engine.respond("open github", "a")
        self.assertEqual(first["status"], "action")
        follow = self.engine.respond("open it", "a")
        self.assertEqual(follow["status"], "action")
        self.assertEqual(follow["action"]["url"], "https://github.com")

    def test_search_that_uses_the_remembered_topic(self):
        self.engine.respond("play lofi beats on youtube", "a")
        follow = self.engine.respond("search that", "a")
        self.assertIn("lofi+beats", follow["action"]["url"])

    def test_again_repeats_the_previous_command(self):
        self.engine.respond("open gmail", "a")
        again = self.engine.respond("again", "a")
        self.assertEqual(again["action"]["url"], "https://mail.google.com")

    def test_recall_questions(self):
        self.engine.respond("open youtube", "a")
        asked = self.engine.respond("what did i ask", "a")
        self.assertIn("open youtube", asked["reply"].lower())
        said = self.engine.respond("what did you say", "a")
        self.assertIn("youtube", said["reply"].lower())

    def test_sessions_are_isolated(self):
        self.engine.respond("open github", "a")
        other = self.engine.respond("open it", "b")
        self.assertIsNone(other["action"])
        self.assertEqual(other["status"], "followup")

    def test_forget_clears_only_this_session(self):
        self.engine.respond("open github", "a")
        self.engine.respond("open gmail", "b")
        self.engine.respond("forget this conversation", "a")
        after = self.engine.respond("open it", "a")
        self.assertIsNone(after["action"])
        still = self.engine.respond("open it", "b")
        self.assertIsNotNone(still["action"])

    def test_store_prunes_idle_sessions(self):
        store = SessionStore(ttl=0)
        store.get("gone")
        store.get("other")
        self.assertEqual(store.count(), 1)  # the first one aged out immediately


class FreeformTests(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def test_question_gets_a_prose_answer_and_a_search_link(self):
        result = self.engine.respond("how do i bake sourdough bread", "f")
        self.assertEqual(result["status"], "search_suggested")
        self.assertIn("search", result["action"]["url"])
        self.assertIn("sourdough", result["action"]["url"])

    def test_statement_gets_a_conversational_reply_and_a_way_to_look_it_up(self):
        result = self.engine.respond("my neighbour plays drums at midnight", "f")
        self.assertEqual(result["status"], "chat")
        self.assertTrue(result["reply"])
        # It cannot know the answer, so it offers a search with a cleaned topic.
        self.assertIsNotNone(result["action"])
        self.assertIn("neighbour+plays+drums+midnight", result["action"]["url"])

    def test_search_topic_drops_question_scaffolding(self):
        result = self.engine.respond("how do i bake sourdough bread", "f")
        self.assertIn("bake+sourdough+bread", result["action"]["url"])

    def test_open_it_does_not_double_the_word_open(self):
        self.engine.respond("open github", "f")
        follow = self.engine.respond("open it", "f")
        self.assertEqual(follow["reply"], "Opening GitHub again.")

    def test_commands_still_win_over_chat(self):
        result = self.engine.respond("hello, open youtube", "f")
        self.assertEqual(result["status"], "action")
        self.assertEqual(result["action"]["url"], "https://www.youtube.com")


class CapabilityConversationTests(unittest.TestCase):
    def test_every_advertised_chat_command_is_answered_conversationally(self):
        engine = make_engine()
        chat = next(c for c in capabilities()["categories"] if c["id"] == "chat")
        for item in chat["items"]:
            with self.subTest(command=item["command"]):
                result = engine.respond(item["command"], "cap")
                self.assertNotEqual(result["status"], "unknown", item["command"])
                self.assertTrue(result["reply"])


class LlmChatTests(unittest.TestCase):
    """The optional model backend: config, request shape and failure fallback."""

    def test_from_env_requires_a_key(self):
        self.assertIsNone(LlmChat.from_env({}))
        self.assertIsNone(LlmChat.from_env({"ALEXA_LLM_PROVIDER": "anthropic"}))
        anthropic = LlmChat.from_env({"ANTHROPIC_API_KEY": "k"})
        self.assertEqual((anthropic.provider, anthropic.model), ("anthropic", "claude-3-5-haiku-latest"))
        openai = LlmChat.from_env({"OPENAI_API_KEY": "k", "ALEXA_LLM_MODEL": "gpt-4o"})
        self.assertEqual((openai.provider, openai.model), ("openai", "gpt-4o"))

    def test_anthropic_request_shape(self):
        seen = {}

        def transport(url, headers, body):
            seen.update(url=url, headers=headers, body=body)
            return {"content": [{"text": "Hello there."}]}

        chat = LlmChat("anthropic", "secret", "claude-3-5-haiku-latest", transport)
        reply = chat.reply([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "how are you")
        self.assertEqual(reply, "Hello there.")
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(seen["headers"]["x-api-key"], "secret")
        self.assertEqual(seen["body"]["messages"][-1]["content"], "how are you")
        self.assertEqual(seen["body"]["messages"][0]["content"], "hi")

    def test_openai_request_shape(self):
        seen = {}

        def transport(url, headers, body):
            seen.update(url=url, headers=headers, body=body)
            return {"choices": [{"message": {"content": "Hi!"}}]}

        chat = LlmChat("openai", "secret", "gpt-4o-mini", transport)
        self.assertEqual(chat.reply([], "hello"), "Hi!")
        self.assertEqual(seen["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(seen["body"]["messages"][0]["role"], "system")

    def test_model_failure_falls_back_to_the_local_answer(self):
        def broken(url, headers, body):
            raise urllib.error.URLError("no network")

        engine = make_engine(llm=LlmChat("anthropic", "secret", "m", broken))
        result = engine.respond("how do i bake sourdough bread", "llm")
        self.assertEqual(result["status"], "search_suggested")  # local fallback used
        self.assertIn("sourdough", result["action"]["url"])

    def test_model_success_is_used_and_flagged(self):
        chat = LlmChat("anthropic", "secret", "m", lambda url, headers, body: {"content": [{"text": "Try a slow rise."}]})
        engine = make_engine(llm=chat)
        result = engine.respond("how do i bake sourdough bread", "llm")
        self.assertEqual(result["status"], "chat_llm")
        self.assertEqual(result["reply"], "Try a slow rise.")
        self.assertEqual(result["llm"], "on")

    def test_malformed_response_raises_llm_error(self):
        chat = LlmChat("anthropic", "secret", "m", lambda url, headers, body: {"unexpected": True})
        with self.assertRaises(LlmError):
            chat.reply([], "hello")


class HttpApiTests(unittest.TestCase):
    """The HTTP layer keeps session memory; the pure function stays stateless."""

    def setUp(self):
        import backend

        self.backend = backend
        backend.reset_session("http-test")

    def test_session_is_echoed_and_remembered(self):
        opened = self.backend.handle_command("open github", "http-test")
        self.assertEqual(opened["session"], "http-test")
        follow = self.backend.handle_command("open it", "http-test")
        self.assertEqual(follow["action"]["url"], "https://github.com")

    def test_reset_forgets_the_session(self):
        self.backend.handle_command("open github", "http-test")
        self.backend.reset_session("http-test")
        after = self.backend.handle_command("open it", "http-test")
        self.assertIsNone(after["action"])

    def test_process_command_stays_stateless(self):
        first = process_command("make me a sandwich")
        self.assertEqual(first["status"], "unknown")
        self.assertNotIn("session", first)


if __name__ == "__main__":
    unittest.main()
