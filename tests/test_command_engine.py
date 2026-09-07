import unittest

from app.command_engine import (dump_context, load_context, remember_results,
                                remember_selected, normalize_action, resolve_references)


class CommandEngineTests(unittest.TestCase):
    def test_ordinal_reference_uses_last_visible_order(self):
        context=remember_results({},"tasks",[
            {"id":11,"title":"گزارش فروش"},{"id":12,"title":"سرمقاله خبرفردا"},{"id":13,"title":"بودجه"}],"list_tasks")
        action=resolve_references({"operation":"update_task_deadline","target":"همان دومی"},context)
        self.assertEqual(action["target"],"سرمقاله خبرفردا"); self.assertEqual(action["_target_id"],12)

    def test_demonstrative_reference_uses_last_selected(self):
        context=remember_selected({},"meetings",{"id":"g-2","title":"جلسه فروش"},"move_meeting")
        action=resolve_references({"operation":"move_meeting","target":"این جلسه"},context)
        self.assertEqual(action["target"],"جلسه فروش"); self.assertEqual(action["_target_id"],"g-2")

    def test_context_round_trip_is_structured(self):
        context=remember_results({},"tasks",[{"id":1,"title":"الف"}],"list_tasks")
        self.assertEqual(load_context(dump_context(context)),context)
        self.assertEqual(load_context("متن قدیمی"),{"legacy":"متن قدیمی"})

    def test_model_time_inconsistencies_are_normalized(self):
        context={"last_operation":"list_tasks"}
        action=normalize_action({"operation":"move_meeting","target":"","relative_days":-3,
            "relative_minutes":0,"date_phrase":"","start_time":""},"همان دومی را سه روز عقب بینداز",context)
        self.assertEqual(action["operation"],"update_task_deadline"); self.assertEqual(action["relative_days"],3)
        created=normalize_action({"operation":"create_task","relative_days":-3,"relative_minutes":20},
                                 "تا آخر هفته تسک بساز",{})
        self.assertEqual((created["relative_days"],created["relative_minutes"]),(0,0))

    def test_absolute_meeting_time_wins_over_spurious_relative_value(self):
        action=normalize_action({"operation":"update_task_deadline","target":"جلسه فروش","date_phrase":"فردا",
            "start_time":"11:00","relative_days":1,"relative_minutes":0},"این جلسه را فردا ساعت ۱۱ بگذار",
            {"last_operation":"list_tasks"})
        self.assertEqual(action["operation"],"move_meeting"); self.assertEqual(action["relative_days"],0)

    def test_create_task_recovers_deadline_from_date_field(self):
        action=normalize_action({"operation":"create_task","deadline_phrase":"تا","date_phrase":"تا آخر هفته",
            "relative_days":0,"relative_minutes":0},"تا آخر هفته تسک بساز",{})
        self.assertEqual(action["deadline_phrase"],"تا آخر هفته"); self.assertEqual(action["date_phrase"],"")

    def test_commitment_is_creation_not_existing_deadline_update(self):
        action=normalize_action({"operation":"update_task_deadline","title":"","target":"سرمقاله خبرفردا",
            "deadline_phrase":"تا آخر هفته","relative_days":0,"relative_minutes":0},
            "تا آخر هفته باید سرمقاله خبرفردا را تحویل بدهم",{"last_operation":"list_tasks"})
        self.assertEqual(action["operation"],"create_task")
        self.assertEqual(action["title"],"سرمقاله خبرفردا")
        self.assertEqual(action["target"],"")

    def test_follow_up_from_person_assigns_that_person(self):
        action=normalize_action({"operation":"create_task","assignee":"خودم","relative_days":0,"relative_minutes":0},
            "تا فردا باید گزارش فروش را از علی رضایی پیگیری کنم",{})
        self.assertEqual(action["assignee"],"علی رضایی")

    def test_create_task_recovers_title_from_wrong_model_field(self):
        action=normalize_action({"operation":"create_task","title":"","target":"گزارش فروش",
            "relative_days":0,"relative_minutes":0},"گزارش فروش را تا فردا از علی پیگیری کنم",{})
        self.assertEqual(action["title"],"گزارش فروش")
        self.assertEqual(action["target"],"")

    def test_note_instruction_cannot_create_duplicate_task(self):
        action=normalize_action({"operation":"create_task","title":"بودجه بنویس","target":"",
            "note":"منتظر تأیید مالی هستیم","relative_days":0,"relative_minutes":0},
            "برای تسک بودجه بنویس منتظر تأیید مالی هستیم",{})
        self.assertEqual(action["operation"],"update_task_note")
        self.assertEqual(action["target"],"بودجه")
        self.assertEqual(action["note"],"منتظر تأیید مالی هستیم")

    def test_meeting_move_recovers_target_from_title(self):
        action=normalize_action({"operation":"update_task_deadline","title":"جلسه فروش","target":"",
            "date_phrase":"فردا","start_time":"10:00","relative_days":0,"relative_minutes":0},
            "جلسه فروش را ببر فردا ساعت ده",{})
        self.assertEqual(action["operation"],"move_meeting")
        self.assertEqual(action["target"],"جلسه فروش")

    def test_task_query_in_same_message_does_not_reclassify_meeting_action(self):
        action=normalize_action({"operation":"move_meeting","title":"","target":"جلسه فروش",
            "date_phrase":"فردا","start_time":"10:00","relative_days":0,"relative_minutes":0},
            "جلسه فروش را ببر فردا ساعت ده و بعد تسک‌های پرریسک را نشان بده",{})
        self.assertEqual(action["operation"],"move_meeting")


if __name__=="__main__": unittest.main()
