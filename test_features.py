"""Offline chat-feature regression tests. All network/model functions are stubbed."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

APP_PATH = Path(__file__).resolve().parent / "app.py"


class FeatureAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        sys.path.insert(0, str(APP_PATH.parent))
        spec = importlib.util.spec_from_file_location("fuadbot_features_test", APP_PATH)
        cls.app = importlib.util.module_from_spec(spec)
        with patch("requests.post", return_value=Mock(status_code=200)), \
             patch("requests.get", return_value=Mock(status_code=404)), \
             patch("threading.Thread.start", lambda self: None):
            spec.loader.exec_module(cls.app)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)
        cls.tmp.cleanup()

    def setUp(self):
        a = self.app
        a.STATE.clear()
        a.STATE.update({"chats": {}, "mem": {}, "cfg": {}, "rems": [], "daily": {},
                        "todos": {}, "notes": [], "streaks": {}, "usage": {}, "spends": {},
                        "links": [], "persona": "default", "dates": [], "studyplan": {},
                        "wikis": [], "dreams": {}, "debate": {}, "ratelimit": {}, "last_action": [],
                        "cmdcount": {}, "alert_day": "", "sumday": "", "users": {}, "journal": {},
                        "goals": {}, "proactive": {}, "last_pdf": {}, "groupmem": {},
                        "goal_checkin": {}, "voice_mode": {}, "last_seen": {}})
        a.save = lambda: None
        a.gh_backup_soon = lambda: None
        a.OWNER_ID = 42
        a.TG_TOKEN = "test-token"
        a.OWNER_ID = 42

    def test_calc_tool_safely_evaluates_and_rejects_code(self):
        a = self.app
        self.assertEqual(a.run_tool("CALC", "(2+3)*4"), "20")
        self.assertEqual(a.run_tool("CALC", "__import__('os')"), "invalid expression")

    def test_tool_tag_extracts_surrounded_call(self):
        self.assertEqual(self.app.extract_tool_tag("Sure! CALC: 2 + 3\n5"), ("CALC", "2 + 3"))

    def test_translate_trims_fields_and_maps_bangla_alias(self):
        self.assertEqual(self.app.translate_text(" ba | hello "), "Translate to Bangla, reply with ONLY the translation:\n\nhello")
        self.assertEqual(self.app.translate_text("en|hola"), "Translate to English, reply with ONLY the translation:\n\nhola")

    def test_translate_rejects_invalid_payload(self):
        for payload in ("", "bn", "|hello", "bn| "):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.app.translate_text(payload)

    def test_translate_bad_format_returns_help_without_llm(self):
        a = self.app
        a.llm = Mock(side_effect=AssertionError("should not invoke model"))
        a.tg = lambda *args, **kwargs: None
        sent = []
        a.send_stream = lambda _uid, out, *_: sent.append(out)
        a.process_text("42", "/translate missing-separator", 1)
        self.assertIn("format:", sent[0])
        self.assertFalse(a.llm.called)

    def test_image_generation_accepts_nonempty_result_and_handles_empty(self):
        a = self.app
        with patch.object(a.requests, "get", return_value=Mock(status_code=200, content=b"x" * 1200)):
            path = a.image_gen("test image")
        self.assertTrue(path and Path(path).exists())
        with patch.object(a.requests, "get", return_value=Mock(status_code=200, content=b"tiny")):
            self.assertIsNone(a.image_gen("test image"))

    def test_vision_sends_image_payload_and_uses_llm_stub_response(self):
        a = self.app
        response = Mock(json=lambda: {"choices": [{"message": {"content": "read image"}}]})
        with patch.object(a.requests, "post", return_value=response) as post:
            self.assertEqual(a.vision_ask(b"imagebytes", "what is this?"), "read image")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["messages"][-1]["content"][0]["text"], "what is this?")
        self.assertTrue(body["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_pdf_text_extract_stubbed_reader(self):
        a = self.app
        class Page:
            def extract_text(self): return "sample pdf content"
        class Reader:
            def __init__(self, _): self.pages = [Page()]
        with patch.dict("sys.modules", {"pypdf": Mock(PdfReader=Reader)}):
            self.assertEqual(a.pdf_text(b"pdf"), "sample pdf content")

    def test_user_limits_zero_override_and_invalid_config(self):
        a = self.app
        a.STATE["public_default_limit"] = 100
        a.STATE["users"] = {"7": {"credits": 0}}
        self.assertEqual(a.user_limit("7"), 0)
        self.assertEqual(a.user_limit("8"), 100)
        a.STATE["public_default_limit"] = "broken"
        self.assertEqual(a.user_limit("8"), 20000)

    def test_admin_credit_command_allows_zero_and_rejects_below_minus_one(self):
        a = self.app
        self.assertIn("limit: 0", a.do_cmd("42", "/admin credit 7 0"))
        self.assertEqual(a.user_limit("7"), 0)
        self.assertIn("credit -1", a.do_cmd("42", "/admin credit 7 -2"))

    def test_regular_user_cannot_run_admin_control_or_adjust_credits(self):
        a = self.app
        self.assertIn("sudhu owner", a.do_cmd("7", "/admin credit 8 500"))
        self.assertNotIn("8", a.STATE.get("users", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
