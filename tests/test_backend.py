import json
import random
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend import _clean_command, capabilities, process_command
from conversation import ConversationEngine


class ProcessCommandTests(unittest.TestCase):
    def test_google_classroom(self):
        result = process_command("open google classroom")
        self.assertEqual(result["status"], "action")
        self.assertEqual(result["action"]["url"], "https://classroom.google.com")

    def test_youtube_opens_when_no_video_is_named(self):
        result = process_command("Alexa, open YouTube")
        self.assertEqual(result["action"]["url"], "https://www.youtube.com")

    def test_youtube_play_query_becomes_search_link(self):
        result = process_command("open youtube and play lo-fi beats")
        self.assertEqual(result["status"], "action")
        query = parse_qs(urlsplit(result["action"]["url"]).query)["search_query"]
        self.assertEqual(query, ["lo-fi beats"])

    def test_search_query_is_url_encoded(self):
        result = process_command("search for weather in Ahmedabad")
        self.assertEqual(result["status"], "action")
        parsed = urlsplit(result["action"]["url"])
        self.assertEqual(parsed.netloc, "www.google.com")
        self.assertEqual(parse_qs(parsed.query)["q"], ["weather in ahmedabad"])

    def test_supported_destinations(self):
        cases = {
            "open google": "https://www.google.com",
            "open claude ai": "https://claude.ai",
            "open gemini": "https://gemini.google.com",
            "open google drive": "https://drive.google.com",
            "open github": "https://github.com",
            "open whatsapp": "https://web.whatsapp.com",
            "open gmail": "https://mail.google.com",
            "open chatgpt": "https://chatgpt.com",
            "open instagram": "https://www.instagram.com",
        }
        for command, expected_url in cases.items():
            with self.subTest(command=command):
                self.assertEqual(process_command(command)["action"]["url"], expected_url)

    def test_one_word_keywords_open_one_app_each(self):
        """Every app answers to its own single-word keyword."""
        cases = {
            "youtube": "https://www.youtube.com",
            "google": "https://www.google.com",
            "gmail": "https://mail.google.com",
            "email": "https://mail.google.com",
            "drive": "https://drive.google.com",
            "classroom": "https://classroom.google.com",
            "github": "https://github.com",
            "whatsapp": "https://web.whatsapp.com",
            "claude": "https://claude.ai",
            "gemini": "https://gemini.google.com",
            "chatgpt": "https://chatgpt.com",
            "gpt": "https://chatgpt.com",
            "instagram": "https://www.instagram.com",
            "insta": "https://www.instagram.com",
            "yt": "https://www.youtube.com",
        }
        for command, expected_url in cases.items():
            with self.subTest(command=command):
                result = process_command(command)
                self.assertEqual(result["status"], "action")
                self.assertEqual(result["action"]["url"], expected_url)

    def test_one_word_keyword_accepts_the_wake_word(self):
        result = process_command("Alexa, gmail")
        self.assertEqual(result["action"]["url"], "https://mail.google.com")

    def test_keyword_only_matches_a_whole_sentence(self):
        """A keyword inside a sentence must not hijack the conversation."""
        for sentence in ("gmail is slow today", "my insta feed is boring", "i love youtube"):
            with self.subTest(sentence=sentence):
                self.assertNotEqual(process_command(sentence)["status"], "action")

    def test_open_phrases_and_aliases_still_work(self):
        cases = {
            "open classroom": "https://classroom.google.com",
            "open drive": "https://drive.google.com",
            "open claude": "https://claude.ai",
            "open email": "https://mail.google.com",
            "open youtube": "https://www.youtube.com",
        }
        for command, expected_url in cases.items():
            with self.subTest(command=command):
                self.assertEqual(process_command(command)["action"]["url"], expected_url)

    def test_settings_explains_browser_limitation(self):
        result = process_command("open settings")
        self.assertEqual(result["status"], "unsupported")
        self.assertIsNone(result["action"])

    def test_stop_command_ends_session(self):
        result = process_command("Alexa stop")
        self.assertFalse(result["should_continue"])
        self.assertEqual(result["status"], "stopped")

    def test_empty_command_prompts_for_input(self):
        result = process_command("   ")
        self.assertEqual(result["status"], "empty")
        self.assertIsNone(result["action"])

    def test_unrecognized_command_has_no_action(self):
        result = process_command("make me a sandwich")
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["action"])

    def test_greetings_are_answered(self):
        for command in ("Hi", "hello", "hey there", "good morning", "whats up", "hi alexa"):
            with self.subTest(command=command):
                result = process_command(command)
                self.assertEqual(result["status"], "smalltalk")
                self.assertIsNone(result["action"])
                self.assertTrue(result["should_continue"])

    def test_greeting_does_not_swallow_a_real_command(self):
        result = process_command("hello, open youtube")
        self.assertEqual(result["status"], "action")
        self.assertEqual(result["action"]["url"], "https://www.youtube.com")

    def test_small_talk(self):
        for command in ("how are you", "what is your name", "who are you", "thanks", "thank you so much"):
            with self.subTest(command=command):
                result = process_command(command)
                self.assertEqual(result["status"], "smalltalk")
                self.assertTrue(result["reply"])

    def test_help_lists_capabilities(self):
        for command in ("help", "what can you do", "what can i say"):
            with self.subTest(command=command):
                result = process_command(command)
                self.assertEqual(result["status"], "help")
                self.assertIn("youtube", result["reply"].lower())

    def test_time_and_date_commands(self):
        for command in ("what time is it", "whats the time", "what is todays date", "what day is it"):
            with self.subTest(command=command):
                result = process_command(command)
                self.assertEqual(result["status"], "time")
                self.assertIsNone(result["action"])
        self.assertIn(":", process_command("what time is it")["reply"])
        self.assertIn(str(datetime.now().year), process_command("whats todays date")["reply"])

    def test_goodbye_ends_session(self):
        for command in ("bye", "goodbye", "see you", "good night"):
            with self.subTest(command=command):
                result = process_command(command)
                self.assertEqual(result["status"], "stopped")
                self.assertFalse(result["should_continue"])


class FixtureTests(unittest.TestCase):
    """The frontend smoke test renders from this fixture, so keep it in sync."""

    def test_capabilities_fixture_matches_backend(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "capabilities.json"
        if not fixture_path.exists():
            self.skipTest("fixture not generated yet")
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture, capabilities(), "regenerate tests/fixtures/capabilities.json")


class CapabilitiesTests(unittest.TestCase):
    def test_capabilities_shape(self):
        data = capabilities()
        self.assertIn("categories", data)
        self.assertGreaterEqual(len(data["categories"]), 3)
        for category in data["categories"]:
            self.assertTrue(category["label"])
            self.assertTrue(category["items"])

    def test_every_offered_example_is_understood(self):
        """The UI renders these straight from the API, so they must all work.

        Checked through the full conversation engine, because the "Just talk"
        entries are answered by the conversational layer rather than by
        process_command directly.
        """
        engine = ConversationEngine(
            intent_handler=process_command,
            normalize=_clean_command,
            rng=random.Random(0),
        )
        for category in capabilities()["categories"]:
            for item in category["items"]:
                with self.subTest(category=category["id"], command=item["command"]):
                    result = engine.respond(item["command"], f"cap-{category['id']}")
                    self.assertNotEqual(result["status"], "unknown", f"{item['command']!r} is not understood")
                    self.assertNotEqual(result["status"], "empty")
                    self.assertTrue(result["reply"], f"{item['command']!r} produced no reply")

    def test_capabilities_returns_copies(self):
        first = capabilities()
        first["categories"][0]["items"][0]["label"] = "mutated"
        first["categories"][0]["label"] = "mutated"
        self.assertNotEqual(capabilities()["categories"][0]["items"][0]["label"], "mutated")
        self.assertNotEqual(capabilities()["categories"][0]["label"], "mutated")


if __name__ == "__main__":
    unittest.main()
