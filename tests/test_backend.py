import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from backend import process_command


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


if __name__ == "__main__":
    unittest.main()
