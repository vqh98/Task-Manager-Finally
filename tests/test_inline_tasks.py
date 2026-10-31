import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.methods import AnswerInlineQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineQuery, Message, Update, User

import app.bot as bot_module
from app.db import Database
from app.inline_tasks import InlineTaskFlow, InlineTaskStates, extract_task
from app.persistent_fsm import PersistentFSMStorage


class CaptureSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=len(self.requests), date=0,
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        return True

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""


class InlineTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")
        await self.db.init()
        await self.db.seed_owners(frozenset({1}))
        await self.db.add_team_member(1, 2, "طرف مقابل")
        await self.db.remember_telegram_account(2, "recipient", "طرف مقابل")
        self.tz = ZoneInfo("Asia/Tehran")
        self.ctx = SimpleNamespace(
            settings=SimpleNamespace(platform_key="telegram", timezone=self.tz),
            db=self.db, ai_service=SimpleNamespace(available=False, parse_task=AsyncMock()),
            allowed=AsyncMock(return_value=True), report_created_task=AsyncMock(),
            ActiveStateNavigationFilter=bot_module.ActiveStateNavigationFilter,
            cancel=bot_module.cancel, leave_stale_form_and_navigate=AsyncMock(),
        )
        self.flow = InlineTaskFlow(self.ctx)
        self.storage = MemoryStorage()
        self.state = FSMContext(self.storage, StorageKey(bot_id=123, chat_id=1, user_id=1))
        self.message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=1), from_user=SimpleNamespace(id=1),
            text="", answer=AsyncMock(),
        )
        self.due = datetime(2026, 11, 5, 18, tzinfo=self.tz)

    async def asyncTearDown(self):
        await self.storage.close()
        self.temp.cleanup()

    async def draft(self, source="گزارش فروش"):
        return await self.db.create_inline_task_draft(1, source)

    def callback(self, data):
        return SimpleNamespace(data=data, from_user=self.message.from_user,
                               message=self.message, answer=AsyncMock())

    async def test_inline_query_only_answers_with_private_switch_and_never_calls_ai(self):
        query = SimpleNamespace(from_user=SimpleNamespace(id=1), query="گزارش فروش", answer=AsyncMock())
        await self.flow.query(query)
        kwargs = query.answer.await_args.kwargs
        self.assertEqual(kwargs["results"], [])
        self.assertTrue(kwargs["is_personal"])
        self.assertEqual(kwargs["cache_time"], 0)
        token = kwargs["button"].start_parameter[3:]
        self.assertEqual((await self.db.inline_task_draft(token, 1))["source"], query.query)
        self.ctx.ai_service.parse_task.assert_not_awaited()
        self.message.answer.assert_not_awaited()

    async def test_empty_and_unauthorized_queries_do_not_create_drafts(self):
        for text, authorized in [("", True), ("گزارش", False)]:
            self.ctx.allowed.return_value = authorized
            query = SimpleNamespace(from_user=SimpleNamespace(id=1), query=text, answer=AsyncMock())
            await self.flow.query(query)
            self.assertEqual(query.answer.await_args.kwargs["button"].start_parameter, "it_help")
        async with self.db._connect() as connection:
            count = await (await connection.execute("SELECT COUNT(*) FROM inline_task_drafts")).fetchone()
        self.assertEqual(count[0], 0)

    async def test_complete_text_still_requires_explicit_recipient_and_ignores_ai_assignee(self):
        self.ctx.ai_service.available = True
        self.ctx.ai_service.parse_task.return_value = SimpleNamespace(
            title="گزارش", deadline=self.due, priority=1, assignee="شخص دیگر",
        )
        token = await self.draft("گزارش فروش تا پنجشنبه، مسئول: شخص دیگر")
        await self.flow.open(self.message, self.state, token, 1)
        self.assertEqual(await self.state.get_state(), InlineTaskStates.assignee.state)
        self.assertEqual(await self.db.tasks(1), [])
        await self.flow.member_callback(self.callback(f"it:member:{token}:2"), self.state)
        tasks = await self.db.tasks(1)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].assignee_user_id, 2)
        self.assertEqual(tasks[0].assignee, "طرف مقابل")
        self.assertEqual(tasks[0].last_note, "گزارش فروش تا پنجشنبه، مسئول: شخص دیگر")
        self.assertIsNone(await self.state.get_state())
        self.ctx.report_created_task.assert_awaited_once()

    async def test_missing_fields_are_requested_once_and_auto_finalize(self):
        token = await self.draft()
        await self.flow.open(self.message, self.state, token, 1)
        self.message.text = "@recipient"
        await self.flow.answer_field(self.message, self.state)
        self.assertEqual(await self.state.get_state(), InlineTaskStates.deadline.state)
        self.message.text = "۱۴۰۵/۰۸/۲۰"
        await self.flow.answer_field(self.message, self.state)
        self.assertEqual(await self.state.get_state(), InlineTaskStates.priority.state)
        data = await self.state.get_data()
        self.assertEqual(data["assignee_user_id"], 2)
        await self.flow.priority_callback(self.callback("priority:2"), self.state)
        task = (await self.db.tasks(1))[0]
        self.assertEqual(task.title, "گزارش فروش")
        self.assertEqual(task.assignee_user_id, 2)
        self.assertEqual(task.last_note, "گزارش فروش")

    async def test_foreign_owner_expired_and_repeated_links_do_not_erase_existing_state(self):
        token = await self.draft()
        await self.state.set_state("NewTask:note")
        await self.state.update_data(title="فرم قبلی")
        await self.flow.open(self.message, self.state, token, 99)
        self.assertEqual((await self.state.get_data())["title"], "فرم قبلی")
        async with self.db._connect() as connection:
            await connection.execute("UPDATE inline_task_drafts SET expires_at=0 WHERE token=?", (token,))
            await connection.commit()
        await self.flow.open(self.message, self.state, token, 1)
        self.assertEqual(await self.state.get_state(), "NewTask:note")

    async def test_existing_form_requires_explicit_replacement(self):
        token = await self.draft()
        await self.state.set_state("NewTask:note")
        await self.state.update_data(title="فرم قبلی")
        await self.flow.open(self.message, self.state, token, 1)
        self.assertEqual(await self.state.get_state(), "NewTask:note")
        await self.flow.keep_callback(self.callback(f"it:keep:{token}"), self.state)
        self.assertEqual((await self.state.get_data())["title"], "فرم قبلی")
        await self.flow.open_callback(self.callback(f"it:open:{token}"), self.state)
        self.assertEqual(await self.state.get_state(), InlineTaskStates.assignee.state)

    async def test_unknown_member_and_stale_button_do_not_assign(self):
        token = await self.draft()
        await self.flow.open(self.message, self.state, token, 1)
        await self.flow.member_callback(self.callback(f"it:member:{token}:999"), self.state)
        self.assertFalse((await self.state.get_data()).get("assignee_user_id"))
        await self.flow.member_callback(self.callback("it:member:old:2"), self.state)
        self.assertFalse((await self.state.get_data()).get("assignee_user_id"))
        self.assertEqual(await self.db.team_members(1), [(2, "طرف مقابل")])

    async def test_finalize_is_atomic_idempotent_and_survives_database_reopen(self):
        token = await self.draft()
        results = await asyncio.gather(*[
            self.db.finalize_inline_task(token, 1, "گزارش", self.due, 2, 2) for _ in range(3)
        ])
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len(set(task_id for task_id, _ in results)), 1)
        reopened = Database(self.db.path)
        await reopened.init()
        task_id, created = await reopened.finalize_inline_task(token, 1, "دیگر", self.due, 1, 2)
        self.assertFalse(created)
        self.assertEqual(task_id, results[0][0])
        self.assertEqual(len(await self.db.tasks(1)), 1)
        self.assertEqual(len(await self.db.assigned_tasks(2)), 1)

    async def test_deleted_member_prevents_creation_and_keeps_draft(self):
        token = await self.draft()
        await self.db.remove_team_member(1, 2)
        with self.assertRaisesRegex(ValueError, "member_unavailable"):
            await self.db.finalize_inline_task(token, 1, "گزارش", self.due, 2, 2)
        self.assertIsNone((await self.db.inline_task_draft(token, 1))["task_id"])
        self.assertEqual(await self.db.tasks(1), [])

    async def test_transaction_failure_rolls_back_task_and_draft_consumption(self):
        token = await self.draft()
        async with self.db._connect() as connection:
            await connection.execute("CREATE TRIGGER fail_history BEFORE INSERT ON task_progress_history "
                                     "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            await connection.commit()
        with self.assertRaises(Exception):
            await self.db.finalize_inline_task(token, 1, "گزارش", self.due, 2, 2)
        self.assertEqual(await self.db.tasks(1), [])
        self.assertIsNone((await self.db.inline_task_draft(token, 1))["task_id"])

    async def test_repeated_query_reuses_pending_draft_and_storage_is_bounded(self):
        token = await self.draft()
        self.assertEqual(await self.draft(), token)
        for index in range(55):
            await self.draft(f"task {index}")
        async with self.db._connect() as connection:
            count = await (await connection.execute("SELECT COUNT(*) FROM inline_task_drafts")).fetchone()
        self.assertEqual(count[0], 50)

    async def test_model_outage_preserves_text_and_unknown_deadline(self):
        self.ctx.ai_service.available = True
        self.ctx.ai_service.parse_task.side_effect = RuntimeError("offline")
        fields = await extract_task("گزارش فروش", self.ctx.ai_service, self.tz)
        self.assertEqual(fields["title"], "گزارش فروش")
        self.assertIsNone(fields["deadline"])
        self.assertIsNone(fields["priority"])

    async def test_persistent_form_resumes_after_restart_and_cancel_does_not_create_task(self):
        token = await self.draft()
        path = str(Path(self.temp.name) / "forms.sqlite3")
        key = StorageKey(bot_id=123, chat_id=1, user_id=1)
        first = FSMContext(PersistentFSMStorage(path), key)
        await self.flow.open(self.message, first, token, 1)
        await self.flow.choose_member(self.message, first, 1, 2)
        second = FSMContext(PersistentFSMStorage(path), key)
        await self.flow.open(self.message, second, token, 1)
        self.assertEqual(await second.get_state(), InlineTaskStates.deadline.state)
        self.assertEqual((await second.get_data())["assignee_user_id"], 2)
        with patch.object(bot_module, "reject_if_needed", new=AsyncMock(return_value=False)):
            await self.flow.cancel(self.message, second)
        self.assertIsNone(await second.get_state())
        self.assertEqual(await self.db.tasks(1), [])
        self.assertIsNone((await self.db.inline_task_draft(token, 1))["task_id"])

    async def test_reopened_completed_draft_does_not_duplicate_confirmation_or_assignment(self):
        token = await self.draft()
        await self.db.finalize_inline_task(token, 1, "گزارش", self.due, 2, 2)
        await self.flow.open(self.message, self.state, token, 1)
        self.ctx.report_created_task.assert_not_awaited()
        self.assertEqual(len(await self.db.tasks(1)), 1)

    async def test_revoked_access_and_group_deep_link_cannot_start_capture(self):
        token = await self.draft()
        self.message.text = f"/start it_{token}"
        self.ctx.allowed.return_value = False
        await self.flow.start(self.message, self.state)
        self.assertIsNone(await self.state.get_state())
        self.ctx.allowed.return_value = True
        self.message.chat.type = "group"
        await self.flow.start(self.message, self.state)
        self.assertIsNone(await self.state.get_state())

    async def test_notification_failure_reports_saved_task_without_rolling_it_back(self):
        token = await self.draft()
        task_id, _ = await self.db.finalize_inline_task(token, 1, "گزارش", self.due, 2, 2)
        self.message.bot = SimpleNamespace()
        with patch.object(bot_module, "db", self.db, create=True), \
             patch.object(bot_module, "settings", self.ctx.settings, create=True), \
             patch.object(bot_module, "notify_team_member", new=AsyncMock(return_value=False)) as notify:
            await bot_module.report_created_task(self.message, task_id, 1, 1)
        notify.assert_awaited_once()
        self.assertIn("ثبت شد", self.message.answer.await_args_list[0].args[0])
        self.assertIn("اعلان", self.message.answer.await_args_list[-1].args[0])
        self.assertEqual(len(await self.db.tasks(1)), 1)

    async def test_persian_deadline_and_priority_without_ai(self):
        now = datetime(2026, 10, 31, 12, tzinfo=self.tz)
        fields = await extract_task("گزارش فروش\nددلاین: ۱۴۰۵/۰۸/۲۰\nاولویت: ۲", self.ctx.ai_service, self.tz, now)
        self.assertEqual(datetime.fromisoformat(fields["deadline"]).date().isoformat(), "2026-11-11")
        self.assertEqual(fields["priority"], 2)

    async def test_dispatcher_inline_query_and_start_reach_new_router_without_outbound_chat_post(self):
        session = CaptureSession()
        bot = Bot("123456789:fake_token_for_local_testing_only", session=session)
        dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
        dp.include_router(self.flow.router)
        user = User(id=1, is_bot=False, first_name="Owner")
        await dp.feed_update(bot, Update(update_id=1, inline_query=InlineQuery(
            id="query", from_user=user, query="گزارش فروش", offset="", chat_type="sender",
        )))
        self.assertEqual(len(session.requests), 1)
        self.assertIsInstance(session.requests[0], AnswerInlineQuery)
        self.assertEqual(session.requests[0].results, [])
        parameter = session.requests[0].button.start_parameter
        await dp.feed_update(bot, Update(update_id=2, message=Message(
            message_id=1, date=0, from_user=user, chat=Chat(id=1, type="private"),
            text=f"/start {parameter}",
        )))
        self.assertTrue(any(isinstance(method, SendMessage) for method in session.requests))
        self.assertTrue(all(method.chat_id == 1 for method in session.requests if isinstance(method, SendMessage)))
        token = parameter[3:]
        private_message = Message(message_id=2, date=0, chat=Chat(id=1, type="private"))
        await dp.feed_update(bot, Update(update_id=3, callback_query=CallbackQuery(
            id="member", from_user=user, chat_instance="private",
            data=f"it:member:{token}:2", message=private_message,
        )))
        await dp.feed_update(bot, Update(update_id=4, message=Message(
            message_id=3, date=0, from_user=user, chat=Chat(id=1, type="private"), text="۱۴۰۵/۰۸/۲۰",
        )))
        await dp.feed_update(bot, Update(update_id=5, callback_query=CallbackQuery(
            id="priority", from_user=user, chat_instance="private", data="priority:2", message=private_message,
        )))
        self.assertEqual(len(await self.db.assigned_tasks(2)), 1)
        self.ctx.report_created_task.assert_awaited_once()
        await dp.storage.close()
        await bot.session.close()
