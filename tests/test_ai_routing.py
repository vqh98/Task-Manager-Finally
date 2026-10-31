import unittest
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from app.config import Settings

from app.ai import AIInterpreter


class AIModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_management_command_is_validated_and_clamped(self):
        from app.ai import AIService
        settings = Settings(
            bot_token="test", allowed_user_ids=frozenset({1}), timezone=ZoneInfo("Asia/Tehran"),
            digest_hour=22, digest_minute=0, morning_hour=9, morning_minute=0,
            deadline_warning_days=2, report_warning_hours=48, database_path=None,
            openai_api_key="test", openai_base_url="https://example.test",
            openai_model="test", openai_whisper_model="test",
        )
        service = AIService(settings)
        service._chat_json = AsyncMock(return_value={
            "operation": "task_query", "filters": {"progress_lt": 50},
            "sort": "progress_asc", "limit": 999,
        })
        plan = await service.parse_management_command(
            "تسک‌هایی که کمتر از ۵۰ درصد پیشرفت کردند را بفرست",
            datetime.now(ZoneInfo("Asia/Tehran")),
        )
        self.assertEqual(plan["filters"], {"progress_lt": 50})
        self.assertEqual(plan["limit"], 50)

    async def test_management_command_accepts_unchanged_days_filter(self):
        from app.ai import AIService
        settings = Settings(
            bot_token="test", allowed_user_ids=frozenset({1}), timezone=ZoneInfo("Asia/Tehran"),
            digest_hour=22, digest_minute=0, morning_hour=9, morning_minute=0,
            deadline_warning_days=2, report_warning_hours=48, database_path=None,
            openai_api_key="test", openai_base_url="https://example.test",
            openai_model="test", openai_whisper_model="test",
        )
        service = AIService(settings)
        service._chat_json = AsyncMock(return_value={
            "operation": "task_query", "filters": {},
            "sort": "deadline_asc", "limit": 20,
        })
        plan = await service.parse_management_command(
            "مواردی که در یک هفته اخیر هیچ تغییری نکردن",
            datetime.now(ZoneInfo("Asia/Tehran")),
        )
        self.assertEqual(plan["filters"], {"unchanged_days": 7})
    def test_simple_command_stays_on_economical_model(self):
        self.assertFalse(AIInterpreter.command_is_complex("تسک گزارش فروش را تا فردا ثبت کن", "{}"))

    def test_contextual_reference_uses_advanced_model(self):
        context='{"last_results":{"meetings":[{"id":"1","title":"جلسه فروش"}]}}'
        self.assertTrue(AIInterpreter.command_is_complex("همان دومی را دو ساعت عقب بینداز", context))

    def test_multiple_operations_use_advanced_model(self):
        self.assertTrue(AIInterpreter.command_is_complex(
            "جلسه فروش را ببر فردا ساعت ده و بعد پیشرفت تسک گزارش را پنجاه درصد کن", "{}"))

    def test_uncertain_or_unknown_plan_is_retried(self):
        action={"operation":"unknown","confidence":.95,"missing_fields":[]}
        self.assertTrue(AIInterpreter._plan_needs_retry({"actions":[action]}))
        action={"operation":"list_tasks","confidence":.5,"missing_fields":[]}
        self.assertTrue(AIInterpreter._plan_needs_retry({"actions":[action]}))
        action={"operation":"list_tasks","confidence":.95,"missing_fields":[]}
        self.assertFalse(AIInterpreter._plan_needs_retry({"actions":[action]}))

    def test_complex_message_routes_to_advanced_model(self):
        self.assertTrue(AIInterpreter.command_is_complex(
            "جلسه فروش را ببر فردا ساعت ده و بعد تسک‌های پرریسک را نشان بده", "{}"))


    async def test_mutation_rejects_unknown_target_id(self):
        from app.ai import AIService
        settings = Settings(
            bot_token="test", allowed_user_ids=frozenset({1}), timezone=ZoneInfo("Asia/Tehran"),
            digest_hour=22, digest_minute=0, morning_hour=9, morning_minute=0,
            deadline_warning_days=2, report_warning_hours=48, database_path=None,
            openai_api_key="test", openai_base_url="https://example.test",
            openai_model="test", openai_whisper_model="test",
        )
        service = AIService(settings)
        service._chat_json = AsyncMock(return_value={
            "operation": "update_task_progress", "target_task_id": 999,
            "target_title": "گزارش", "progress": 80, "confidence": .98,
            "requires_confirmation": True,
        })
        plan = await service.parse_management_command(
            "پیشرفت گزارش را هشتاد درصد کن",
            datetime.now(ZoneInfo("Asia/Tehran")),
            {"tasks": [{"id": 12, "title": "گزارش"}], "meetings": []},
        )
        self.assertEqual(plan["operation"], "unsupported")

    async def test_mutation_without_confidence_is_rejected(self):
        from app.ai import AIService
        settings = Settings(
            bot_token="test", allowed_user_ids=frozenset({1}), timezone=ZoneInfo("Asia/Tehran"),
            digest_hour=22, digest_minute=0, morning_hour=9, morning_minute=0,
            deadline_warning_days=2, report_warning_hours=48, database_path=None,
            openai_api_key="test", openai_base_url="https://example.test",
            openai_model="test", openai_whisper_model="test",
        )
        service = AIService(settings)
        service._chat_json = AsyncMock(return_value={
            "operation": "update_task_status", "target_task_id": 12,
            "target_title": "گزارش", "status": "done", "requires_confirmation": True,
        })
        plan = await service.parse_management_command(
            "گزارش را انجام شده بزن",
            datetime.now(ZoneInfo("Asia/Tehran")),
            {"tasks": [{"id": 12, "title": "گزارش"}], "meetings": []},
        )
        self.assertEqual(plan["operation"], "unsupported")

    async def test_ai_deadline_is_timezone_aware(self):
        from app.ai import AIService
        settings = Settings(
            bot_token="test", allowed_user_ids=frozenset({1}), timezone=ZoneInfo("Asia/Tehran"),
            digest_hour=22, digest_minute=0, morning_hour=9, morning_minute=0,
            deadline_warning_days=2, report_warning_hours=48, database_path=None,
            openai_api_key="test", openai_base_url="https://example.test",
            openai_model="test", openai_whisper_model="test",
        )
        service = AIService(settings)
        service._chat_json = AsyncMock(return_value={
            "deadline": "2026-09-03T23:59:00+03:30", "confidence": .97, "ambiguous": False,
        })
        deadline = await service.parse_natural_deadline(
            "تا وقتی هفته کاری بعد تمام می‌شود",
            ZoneInfo("Asia/Tehran"), datetime(2026, 8, 28, 12, tzinfo=ZoneInfo("Asia/Tehran")),
        )
        self.assertIsNotNone(deadline)
        self.assertEqual(deadline.utcoffset().total_seconds(), 12600)

    async def test_meeting_summary_receives_the_complete_source_message(self):
        from app.ai import AIService
        settings = Settings(
            bot_token="test", allowed_user_ids=frozenset({1}), timezone=ZoneInfo("Asia/Tehran"),
            digest_hour=22, digest_minute=0, morning_hour=9, morning_minute=0,
            deadline_warning_days=2, report_warning_hours=48, database_path=None,
            openai_api_key="test", openai_base_url="https://example.test",
            openai_model="test", openai_whisper_model="test",
        )
        service = AIService(settings)
        source = "سلام و عرض ادب\n\nفردا از ساعت ۸ تا ۱۱ در محل موکب برای خدمت‌رسانی حاضر می‌شویم."
        service._chat_json = AsyncMock(return_value={"title": "خدمت‌رسانی در موکب"})

        title = await service.summarize_meeting(source)

        self.assertEqual(title, "خدمت‌رسانی در موکب")
        _, user_prompt = service._chat_json.await_args.args
        self.assertIn(source, user_prompt)

if __name__ == "__main__":
    unittest.main()
