import os
import shutil
import tempfile
import time
import unittest


class TestStorage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix='ubtest_')
        # Storage хранит инстансы по пути к БД: тест получает свою БД
        # независимо от порядка запуска (раньше первый вызов в процессе
        # фиксировал путь, и путь молча игнорировался).
        from storage import Storage
        cls.db = Storage(os.path.join(cls.tmpdir, 'test.db'))
        assert cls.db.db_path.endswith('test.db'), \
            f"Storage привязана к {cls.db.db_path} — ошибка в __new__"

    @classmethod
    def tearDownClass(cls):
        try:
            cls.db.conn.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ── Напоминания (персистентный !remind) ──

    def test_reminder_lifecycle(self):
        now = int(time.time())
        rid = self.db.add_reminder(111, 'проверить чайник', now + 60)
        pending = self.db.get_pending_reminders()
        self.assertTrue(any(r['id'] == rid for r in pending))

        due = self.db.get_due_reminders(now + 120)
        self.assertTrue(any(r['id'] == rid for r in due))

        self.db.del_reminder(rid)
        pending = self.db.get_pending_reminders()
        self.assertFalse(any(r['id'] == rid for r in pending))

    def test_due_reminders_ordering_and_cutoff(self):
        now = int(time.time())
        a = self.db.add_reminder(222, 'раньше', now - 10)
        b = self.db.add_reminder(222, 'позже', now + 3600)
        try:
            due = [r['id'] for r in self.db.get_due_reminders(now)]
            self.assertIn(a, due)
            self.assertNotIn(b, due)
        finally:
            self.db.del_reminder(a)
            self.db.del_reminder(b)

    # ── Todos ──

    def test_todo_lifecycle(self):
        tid = self.db.add_todo('купить молоко')
        try:
            todos = self.db.get_todos()
            self.assertTrue(any(t['id'] == tid and t['done'] == 0 for t in todos))
            self.db.update_todo(tid, done=True)
            todos = self.db.get_todos()
            self.assertTrue(any(t['id'] == tid and t['done'] == 1 for t in todos))
        finally:
            self.db.del_todo(tid)
        todos = self.db.get_todos()
        self.assertFalse(any(t['id'] == tid for t in todos))

    # ── Stats ──

    def test_bump_and_read_stats(self):
        before = self.db.all_stats().get('test_cmds', 0)
        self.db.bump_stat('test_cmds', 3)
        after = self.db.all_stats().get('test_cmds', 0)
        self.assertEqual(after, before + 3)

    # ── ONWATCH: кулдаун от последней отправки (last_fired) ──

    def test_online_watch_fired_cooldown(self):
        # новый трек: кулдауна нет (last_fired = 0) — караулить сразу
        self.db.add_online_watch(777000111, 'tester', 'привет', 1, -100)
        row = self.db.get_online_watch_by_target(777000111)
        self.assertIsNotNone(row)
        self.assertEqual(row.get('last_fired') or 0, 0)
        try:
            now = int(time.time())
            self.db.touch_online_watch_fired(row['id'], now)
            row2 = self.db.get_online_watch_by_target(777000111)
            self.assertEqual(row2['last_fired'], now)

            def would_fire(r, now_):
                return now_ - (r.get('last_fired') or 0) >= 600

            # 599с — молчим, 601с — шлём
            self.assertFalse(would_fire(row2, now + 599))
            self.assertTrue(would_fire(row2, now + 601))
            # «залипший» статус online НЕ должен глушить (регресс старого бага)
            row2['last_status'] = 'online'
            self.assertTrue(would_fire(row2, now + 601))
        finally:
            self.db.remove_online_watch(target_id=777000111)
        self.assertIsNone(self.db.get_online_watch_by_target(777000111))

    def test_online_watch_readd_updates_and_keeps_cooldown(self):
        # повторное добавление цели обновляет текст, но НЕ сбрасывает кулдаун
        added = self.db.add_online_watch(777000222, 'tester', 'первый', 1, -100)
        self.assertTrue(added)
        try:
            row = self.db.get_online_watch_by_target(777000222)
            self.db.touch_online_watch_fired(row['id'], 12345)
            added2 = self.db.add_online_watch(777000222, 'tester2', 'второй', 1, -100)
            self.assertFalse(added2)  # обновление, а не новый трек
            row2 = self.db.get_online_watch_by_target(777000222)
            self.assertEqual(row2['message'], 'второй')
            self.assertEqual(row2['target_username'], 'tester2')
            self.assertEqual(row2['last_fired'], 12345)  # кулдаун не сброшен
        finally:
            self.db.remove_online_watch(target_id=777000222)

    def test_online_watch_status_since_on_change(self):
        # status_since обновляется только при РЕАЛЬНОЙ смене статуса —
        # по нему считаются длительности сессий в логах караула
        added = self.db.add_online_watch(777000333, 'tester3', 'msg3', 1, -100)
        self.assertTrue(added)
        try:
            row = self.db.get_online_watch_by_target(777000333)
            self.db.update_online_watch_status(row['id'], 'offline')
            r1 = self.db.get_online_watch_by_target(777000333)
            self.assertEqual(r1['last_status'], 'offline')
            s1 = r1['status_since']
            self.assertTrue(s1 > 0)
            self.db.update_online_watch_status(row['id'], 'offline')
            r2 = self.db.get_online_watch_by_target(777000333)
            self.assertEqual(r2['status_since'], s1)  # тот же статус — не сдвигаем
            time.sleep(1.1)
            self.db.update_online_watch_status(row['id'], 'online')
            r3 = self.db.get_online_watch_by_target(777000333)
            self.assertGreater(r3['status_since'], s1)
        finally:
            self.db.remove_online_watch(target_id=777000333)

    def test_online_watch_exit_closes_session(self):
        # строгий режим: выход фиксируется отдельной меткой и разрывает сессию
        self.db.add_online_watch(777000444, 'tester4', 'msg4', 1, -100)
        try:
            row = self.db.get_online_watch_by_target(777000444)
            self.assertEqual(row.get('last_exit') or 0, 0)
            self.db.touch_online_watch_session(row['id'], 1000)
            self.db.touch_online_watch_exit(row['id'], 1234)
            self.db.close_online_watch_session(row['id'])
            r2 = self.db.get_online_watch_by_target(777000444)
            self.assertEqual(r2['last_exit'], 1234)
            self.assertEqual(r2['last_activity'], 0)
        finally:
            self.db.remove_online_watch(target_id=777000444)

    # ── Чёрный список ЛС (!pmwatch) ──

    def test_pm_mute_roundtrip(self):
        # по умолчанию следим за всеми — обратная совместимость с прежним
        # поведением, когда фильтра не было
        self.assertEqual(self.db.get_pm_muted_chats(), set())
        self.assertTrue(self.db.pm_chat_monitored(555))

        self.db.mute_pm_chat(555)
        self.assertFalse(self.db.pm_chat_monitored(555))
        self.assertTrue(self.db.pm_chat_monitored(777))
        self.assertEqual(self.db.get_pm_muted_chats(), {555})

        self.db.mute_pm_chat(777)
        self.assertEqual(self.db.get_pm_muted_chats(), {555, 777})

        self.db.unmute_pm_chat(555)
        self.assertEqual(self.db.get_pm_muted_chats(), {777})
        self.assertTrue(self.db.pm_chat_monitored(555))

        self.db.set_pm_muted_chats(set())
        self.assertEqual(self.db.get_pm_muted_chats(), set())
        self.assertTrue(self.db.pm_chat_monitored(777))

    def test_pm_mute_survives_reopen(self):
        # список в saved_data, а не в памяти: переживает рестарт бота
        self.db.mute_pm_chat(901)
        try:
            self.assertEqual(self.db.get_pm_muted_chats(), {901})
            self.assertFalse(self.db.pm_chat_monitored(901))
        finally:
            self.db.unmute_pm_chat(901)

    def test_purge_pm_chat_returns_media_and_clears_rows(self):
        self.db.cache_pm_message(902, 10, 5, 'кто-то', 'текст 1', True, 'photo')
        self.db.cache_pm_message(902, 11, 5, 'кто-то', 'текст 2', False, None)
        self.db.cache_pm_message(903, 12, 6, 'другой', 'чужой чат', False, None)
        self.db.set_pm_media_file(902, 10, 'C:/tmp/x.jpg')

        files = self.db.purge_pm_chat(902)
        self.assertEqual(files, ['C:/tmp/x.jpg'])
        self.assertIsNone(self.db.get_pm_cached(902, 10))
        self.assertIsNone(self.db.get_pm_cached(902, 11))
        # соседний чат не тронут
        self.assertIsNotNone(self.db.get_pm_cached(903, 12))
        self.db.delete_pm_cached(903, 12)
        self.assertEqual(self.db.purge_pm_chat(999), [])  # пустой чат

    # ── AI ──

    def test_ai_history_is_deduplicated_and_pruned(self):
        self.db.clear_ai_msgs(1234)
        self.db.save_ai_msg(1234, 1, 'привет', ts=100, msg_id=10)
        self.db.save_ai_msg(1234, 1, 'привет', ts=100, msg_id=10)  # повтор !sync
        self.db.save_ai_msg(1234, 2, 'как дела', ts=101, msg_id=11)
        hist = self.db.get_ai_msgs(1234, limit=10)
        self.assertEqual([m['text'] for m in hist], ['привет', 'как дела'])
        self.assertEqual(self.db.count_ai_msgs(1234), 2)

        self.db.prune_ai_msgs(per_peer=1, total=10)
        hist = self.db.get_ai_msgs(1234, limit=10)
        self.assertEqual([m['text'] for m in hist], ['как дела'])
        self.db.clear_ai_msgs(1234)

    def test_ai_facts_auto_lists_quest(self):
        uid = 4321
        self.db.clear_ai_facts(uid)
        self.db.add_ai_fact(uid, 'любит кофе')
        self.assertEqual(self.db.get_ai_facts(uid), ['любит кофе'])
        self.db.clear_ai_facts(uid)
        self.assertEqual(self.db.get_ai_facts(uid), [])

        self.db.set_ai_auto(uid, True, prompt='коллега')
        auto = self.db.get_ai_auto(uid)
        self.assertTrue(auto['enabled'])
        self.assertEqual(auto['prompt'], 'коллега')
        self.db.set_ai_auto(uid, False)
        self.assertFalse(self.db.is_ai_auto_on(uid))

        self.assertTrue(self.db.toggle_ai_list(uid, 'black'))
        self.assertTrue(self.db.is_ai_blocked(uid))
        self.assertFalse(self.db.toggle_ai_list(uid, 'black'))
        self.assertFalse(self.db.is_ai_blocked(uid))

        self.db.set_ai_prompt_mode(uid, True)
        self.assertTrue(self.db.get_ai_prompt_mode(uid))
        self.db.set_ai_prompt_mode(uid, False)
        self.assertFalse(self.db.get_ai_prompt_mode(uid))

        self.db.set_ai_quest(777, '{"step": 1}')
        self.assertEqual(self.db.get_ai_quest(777), '{"step": 1}')
        self.db.clear_ai_quest(777)
        self.assertIsNone(self.db.get_ai_quest(777))

    # ── Заметки ──

    def test_notes(self):
        self.db.set_note('тест-заметка', 'значение')
        try:
            self.assertEqual(self.db.get_note('тест-заметка'), 'значение')
            hits = self.db.search_notes('заметка')
            self.assertTrue(len(hits) >= 1)
        finally:
            self.db.del_note('тест-заметка')
        self.assertIsNone(self.db.get_note('тест-заметка'))


    def test_promises_survive_restart_and_dedupe(self):
        from storage import Storage
        path = os.path.join(self.tmpdir, 'promises_restart.db')
        db = Storage(path)
        try:
            added = db.save_ai_promises(555, [
                {'text': 'верну телефон', 'who_label': 'Жена',
                 'due_at': '03.01', 'note': 'в поиске'},
                {'text': 'верну телефон', 'who_label': 'Жена'},
            ])
            self.assertEqual(added, 1)
            rows = db.get_ai_promises(555)
            self.assertEqual(len(rows), 1)
            pid = rows[0]['id']
            self.assertEqual(rows[0]['status'], 'open')
        finally:
            db.close()

        db2 = Storage(path)
        try:
            rows = db2.get_ai_promises(555)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['text'], 'верну телефон')
            self.assertTrue(db2.mark_ai_promise(pid, 'done', note='подтверждено'))
            self.assertEqual(db2.get_ai_promises(555), [])
            archive = db2.get_ai_promises(555, status=None)
            self.assertEqual(len(archive), 1)
            self.assertEqual(archive[0]['status'], 'done')
        finally:
            db2.close()


if __name__ == '__main__':
    unittest.main()
