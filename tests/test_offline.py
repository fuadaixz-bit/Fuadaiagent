"""Offline regression tests for fuadbot. No Telegram or model API calls are made."""
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"

class FuadBotOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("TG_TOKEN", None)
        sys.path.insert(0, str(APP_PATH.parent))
        spec = importlib.util.spec_from_file_location("fuadbot_app_test", APP_PATH)
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
                        "wikis": [], "dreams": {}, "debate": {}, "ratelimit": {},
                        "last_action": [], "cmdcount": {}, "alert_day": "", "sumday": "",
                        "users": {}, "journal": {}, "goals": {}, "proactive": {},
                        "last_pdf": {}, "groupmem": {}, "goal_checkin": {}, "voice_mode": {},
                        "last_seen": {}})
        a.save = lambda: None
        a.gh_backup_soon = lambda: None
        a.OWNER_ID = 42
        a._once = lambda key: True

    def test_time_parser_valid_and_invalid(self):
        a = self.app
        self.assertIsNotNone(a.parse_time_word("15m"))
        self.assertIsNotNone(a.parse_time_word("2h"))
        self.assertIsNotNone(a.parse_time_word("1d"))
        self.assertIsNotNone(a.parse_time_word("23:59"))
        self.assertIsNone(a.parse_time_word("nonsense"))

    def test_reminders_list_and_delete_number(self):
        a = self.app
        a.STATE["rems"] = [
            {"at": 2000000000, "chat": 7, "text": "reminder: first", "repeat_days": 0},
            {"at": 2000000100, "chat": 8, "text": "reminder: other", "repeat_days": 0},
            {"at": 2000000200, "chat": 7, "text": "reminder: second", "repeat_days": 0},
        ]
        self.assertIn("first", a.do_cmd("7", "/remind list"))
        out = a.do_cmd("7", "/remind del 2")
        self.assertIn("second", out)
        self.assertEqual([r["text"] for r in a.STATE["rems"]], ["reminder: first", "reminder: other"])

    def test_pomodoro_reminders_have_delivery_chat(self):
        a = self.app
        a.do_cmd("7", "/pomo 2")
        self.assertEqual(len(a.STATE["rems"]), 4)
        self.assertTrue(all(r.get("chat") == 7 for r in a.STATE["rems"]))

    def test_daily_and_brief_times_validate(self):
        a = self.app
        self.assertIn("daily set", a.do_cmd("7", "/daily 07:05 | hello"))
        self.assertIn("format", a.do_cmd("7", "/daily 29:70 | invalid"))
        self.assertIn("format", a.do_cmd("7", "/brief 29:70"))

    def test_todo_done_requires_positive_valid_index(self):
        a = self.app
        a.do_cmd("7", "/todo add first")
        a.do_cmd("7", "/todo add second")
        self.assertIn("number", a.do_cmd("7", "/todo done 0"))
        self.assertFalse(a.STATE["todos"]["7"][1]["done"])
        self.assertEqual(a.do_cmd("7", "/todo done 2"), "done")

    def test_core_personal_features(self):
        a = self.app
        self.assertIn("mone rekhlam", a.do_cmd("7", "/remember gym daily"))
        self.assertIn("gym", a.do_cmd("7", "/memory"))
        self.assertIn("todo added", a.do_cmd("7", "/todo add math"))
        self.assertIn("math", a.do_cmd("7", "/todo list"))
        self.assertIn("note saved", a.do_cmd("7", "/note revise chapter 1"))
        self.assertIn("revise", a.do_cmd("7", "/notes"))
        self.assertIn("journal e save", a.do_cmd("7", "/journal studied today"))
        self.assertIn("studied today", a.do_cmd("7", "/journal list"))
        self.assertIn("1 din streak", a.do_cmd("7", "/streak study"))
        self.assertIn("already done", a.do_cmd("7", "/streak study"))

    def test_math_and_small_utility_features(self):
        a = self.app
        self.assertEqual(a.run_tool("CALC", "(2+3)*4"), "20")
        self.assertEqual(a.run_tool("CALC", "__import__('os')"), "invalid expression")
        self.assertIn("300 tk", a.cmd_split("1200|4|snacks"))
        self.assertIn("3 shobdo", a.cmd_readtime("one two three"))
        self.assertEqual(a.do_cmd("7", "/ping"), "pong 🏓")
        self.assertIn("Head", "Head Tail")

    def test_dates_goals_study_plan_and_undo(self):
        a = self.app
        self.assertIn("mone rakhlam", a.cmd_rdate("7", "Rahim|5 March|birthday"))
        self.assertIn("Rahim", a.cmd_rdate("7", "list"))
        self.assertIn("goal set", a.do_cmd("7", "/goal set pass exam"))
        self.assertIn("goal done", a.do_cmd("7", "/goal done 1"))
        self.assertIn("plan ready", a.cmd_syllabus("7", "test|2026-11-01|math, accounting"))
        self.assertIn("plan", a.cmd_syllabus("7", "list"))
        a.do_cmd("7", "/todo add undo me")
        self.assertIn("todo muche", a.do_cmd("7", "/undo"))

    def test_admin_quota_and_routes(self):
        a = self.app
        a.STATE["public_mode"] = True
        self.assertIsNone(a.check_user_quota("8"))
        a.STATE["usage"] = {a.now_local().strftime("%Y-%m-%d"): {"per_user": {"8": 20000}}}
        self.assertIn("limit sesh", a.check_user_quota("8"))
        client = a.app.test_client()
        self.assertEqual(client.get("/").data.decode(), "Fuad AI v9.21")
        self.assertEqual(client.get("/ping").data.decode(), "pong")

if __name__ == "__main__":
    unittest.main(verbosity=2)
