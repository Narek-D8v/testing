import sqlite3
import threading
import time
import logging
import os

logger = logging.getLogger(__name__)


_VALID_TABLES = frozenset({
    'bot_state', 'saved_data', 'notes', 'todos', 'stats',
    'reply_settings', 'sessions', 'protected_chats',
    'online_watch', 'pm_msg_cache', 'reminders',
    'ai_msgs', 'ai_facts', 'ai_auto', 'ai_lists', 'ai_flags', 'ai_quest',
    'ai_promises',
})

# Чёрный список ЛС для слежки за удалениями/правками (!pmwatch mute).
# Хранится в saved_data как «id,id,...» — миграция схемы не нужна.
PM_MUTED_KEY = '_pm_muted_chats'


def _validate_table(table):
    if table not in _VALID_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    return table


class Storage:
    # инстансы по пути к БД: повторный Storage('userbot.db') возвращает тот же
    # объект, а Storage('/tmp/other.db') — отдельную БД (раньше путь после
    # первого вызова молча игнорировался)
    _instances = {}
    _lock = threading.Lock()

    def __new__(cls, db_path='userbot.db'):
        with cls._lock:
            inst = cls._instances.get(db_path)
            if inst is None:
                inst = super().__new__(cls)
                inst._init(db_path)
                cls._instances[db_path] = inst
            return inst

    def _init(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Одно соединение используется и event loop'ом, и Flask-потоком.
        # WAL снижает конкуренцию между чтением и записью, busy_timeout —
        # защищает от мгновенного «database is locked» при коротком конфликте.
        try:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA busy_timeout=5000')
        except sqlite3.Error:
            pass
        self.lock = threading.Lock()
        self._create_tables()

    def close(self):
        """Закрывает коннект и убирает инстанс из кэша.

        Без deregister'а class-level _instances держал закрытые соединения до
        конца процесса, поэтому повторный Storage(тот же путь) возвращал
        объект с закрытым коннектом.
        """
        with self.lock:
            try:
                self.conn.close()
            except Exception:
                pass
        with Storage._lock:
            if Storage._instances.get(self.db_path) is self:
                Storage._instances.pop(self.db_path, None)

    def _create_tables(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.executescript('''
                CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS saved_data (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS notes (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, text TEXT, done INTEGER DEFAULT 0, created INTEGER);
                CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS reply_settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS protected_chats (chat_id TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS online_watch (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL UNIQUE,
                    target_username TEXT,
                    message TEXT NOT NULL,
                    owner_id INTEGER NOT NULL,
                    chat_id INTEGER,
                    last_status TEXT DEFAULT 'unknown',
                    enabled INTEGER DEFAULT 1,
                    created_at INTEGER,
                    last_fired INTEGER DEFAULT 0,
                    status_since INTEGER DEFAULT 0,
                    md INTEGER DEFAULT 0,
                    fires INTEGER DEFAULT 0,
                    last_source TEXT,
                    last_error TEXT,
                    last_activity INTEGER DEFAULT 0,
                    last_exit INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS online_watch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    source TEXT
                );
                CREATE TABLE IF NOT EXISTS pm_msg_cache (
                    chat_id INTEGER,
                    msg_id INTEGER,
                    sender_id INTEGER,
                    sender_name TEXT,
                    text TEXT,
                    has_media INTEGER DEFAULT 0,
                    media_type TEXT,
                    date INTEGER,
                    verified INTEGER DEFAULT 1,
                    last_seen INTEGER DEFAULT 0,
                    media_file TEXT,
                    file_name TEXT,
                    file_size INTEGER DEFAULT 0,
                    PRIMARY KEY(chat_id, msg_id)
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    due_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai_msgs (
                    id INTEGER PRIMARY KEY,
                    peer_id INTEGER NOT NULL,
                    sender_id INTEGER,
                    text TEXT,
                    date INTEGER,
                    msg_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS ai_facts (
                    user_id INTEGER,
                    fact TEXT,
                    date INTEGER,
                    PRIMARY KEY (user_id, fact)
                );
                CREATE TABLE IF NOT EXISTS ai_auto (
                    user_id INTEGER PRIMARY KEY,
                    enabled INTEGER DEFAULT 0,
                    role TEXT DEFAULT '',
                    prompt TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS ai_lists (
                    user_id INTEGER PRIMARY KEY,
                    list_type TEXT
                );
                CREATE TABLE IF NOT EXISTS ai_flags (
                    user_id INTEGER PRIMARY KEY,
                    prompt_mode INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS ai_quest (
                    chat_id INTEGER PRIMARY KEY,
                    state TEXT
                );
                CREATE TABLE IF NOT EXISTS ai_promises (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id INTEGER NOT NULL,
                    promiser_id INTEGER,
                    who_label TEXT DEFAULT '',
                    source_msg_id INTEGER,
                    text TEXT NOT NULL,
                    due_at TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at INTEGER NOT NULL,
                    resolved_at INTEGER,
                    last_checked_at INTEGER,
                    note TEXT DEFAULT ''
                );
            ''')
            # Миграция для существующих баз: добавляем отсутствующие колонки
            cur.execute('PRAGMA table_info(pm_msg_cache)')
            cols = {row[1] for row in cur.fetchall()}
            if 'verified' not in cols:
                cur.execute('ALTER TABLE pm_msg_cache ADD COLUMN verified INTEGER DEFAULT 1')
            if 'last_seen' not in cols:
                cur.execute('ALTER TABLE pm_msg_cache ADD COLUMN last_seen INTEGER DEFAULT 0')
            if 'media_file' not in cols:
                cur.execute('ALTER TABLE pm_msg_cache ADD COLUMN media_file TEXT')
            if 'file_name' not in cols:
                cur.execute('ALTER TABLE pm_msg_cache ADD COLUMN file_name TEXT')
            if 'file_size' not in cols:
                cur.execute('ALTER TABLE pm_msg_cache ADD COLUMN file_size INTEGER DEFAULT 0')
            # Миграция online_watch: кулдаун последней отправки уведомления
            cur.execute('PRAGMA table_info(online_watch)')
            ocols = {row[1] for row in cur.fetchall()}
            if 'last_fired' not in ocols:
                cur.execute('ALTER TABLE online_watch ADD COLUMN last_fired INTEGER DEFAULT 0')
            if 'status_since' not in ocols:
                cur.execute('ALTER TABLE online_watch ADD COLUMN status_since INTEGER DEFAULT 0')
            if 'md' not in ocols:
                cur.execute('ALTER TABLE online_watch ADD COLUMN md INTEGER DEFAULT 0')
            if 'fires' not in ocols:
                cur.execute('ALTER TABLE online_watch ADD COLUMN fires INTEGER DEFAULT 0')
            if 'last_source' not in ocols:
                cur.execute('ALTER TABLE online_watch ADD COLUMN last_source TEXT')
            if 'last_error' not in ocols:
                cur.execute('ALTER TABLE online_watch ADD COLUMN last_error TEXT')
            if 'last_activity' not in ocols:
                cur.execute('ALTER TABLE online_watch ADD COLUMN last_activity INTEGER DEFAULT 0')
            if 'last_exit' not in ocols:
                cur.execute('ALTER TABLE online_watch ADD COLUMN last_exit INTEGER DEFAULT 0')
            cur.execute('PRAGMA table_info(online_watch_log)')
            lcols = {row[1] for row in cur.fetchall()}
            if 'source' not in lcols:
                cur.execute('ALTER TABLE online_watch_log ADD COLUMN source TEXT')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_owlog_target_ts ON online_watch_log(target_id, ts)')
            # Миграция AI-истории: msg_id появился позже, на нём уникальность
            # (повторный !sync не должен дублировать сообщения).
            cur.execute('PRAGMA table_info(ai_msgs)')
            acols = {row[1] for row in cur.fetchall()}
            if 'msg_id' not in acols:
                cur.execute('ALTER TABLE ai_msgs ADD COLUMN msg_id INTEGER')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_msgs_peer ON ai_msgs(peer_id, date)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_ai_promises_peer_status '
                        'ON ai_promises(peer_id, status, created_at)')
            cur.execute(
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_msgs_peer_msg '
                'ON ai_msgs(peer_id, msg_id) WHERE msg_id IS NOT NULL'
            )
            cur.execute('CREATE INDEX IF NOT EXISTS idx_pm_msg_id ON pm_msg_cache(msg_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_pm_chat_date ON pm_msg_cache(chat_id, date)')
            self.conn.commit()

    # ── Generic helpers ──

    def _get(self, table, key, default=None):
        _validate_table(table)
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(f'SELECT value FROM {table} WHERE key = ?', (key,))
            row = cur.fetchone()
            return row[0] if row else default

    def _set(self, table, key, value):
        _validate_table(table)
        with self.lock:
            self.conn.execute(f'INSERT OR REPLACE INTO {table} (key, value) VALUES (?, ?)', (key, value))
            self.conn.commit()

    def _delete(self, table, key):
        _validate_table(table)
        with self.lock:
            self.conn.execute(f'DELETE FROM {table} WHERE key = ?', (key,))
            self.conn.commit()

    def _get_all(self, table):
        _validate_table(table)
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(f'SELECT key, value FROM {table}')
            return {row[0]: row[1] for row in cur.fetchall()}

    def _delete_all(self, table):
        _validate_table(table)
        with self.lock:
            self.conn.execute(f'DELETE FROM {table}')
            self.conn.commit()

    def _keys(self, table):
        _validate_table(table)
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(f'SELECT key FROM {table}')
            return [row[0] for row in cur.fetchall()]

    def _search(self, table, query):
        _validate_table(table)
        q = f'%{query.lower()}%'
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(f'SELECT key, value FROM {table} WHERE LOWER(key) LIKE ? OR LOWER(value) LIKE ?', (q, q))
            return cur.fetchall()

    # ── Bot state ──

    def get_state(self, key, default=None):
        return self._get('bot_state', key, default)

    def set_state(self, key, value):
        self._set('bot_state', key, str(value))

    # ── Saved data ──

    def get_saved(self, key, default=None):
        return self._get('saved_data', key, default)

    def set_saved(self, key, value):
        self._set('saved_data', key, value)

    def del_saved(self, key):
        self._delete('saved_data', key)

    def all_saved(self):
        return self._get_all('saved_data')

    def search_saved(self, query):
        return self._search('saved_data', query)

    # ── Notes ──

    def get_note(self, key, default=None):
        return self._get('notes', key, default)

    def set_note(self, key, value):
        self._set('notes', key, value)

    def del_note(self, key):
        self._delete('notes', key)

    def all_notes(self):
        return self._get_all('notes')

    def search_notes(self, query):
        return self._search('notes', query)

    # ── Todos ──

    def add_todo(self, text):
        with self.lock:
            # id в миллисекундах + защита от коллизии: две задачи в одну
            # секунду раньше роняли INSERT (UNIQUE constraint) без ответа
            tid = int(time.time() * 1000)
            while self.conn.execute('SELECT 1 FROM todos WHERE id = ?', (tid,)).fetchone():
                tid += 1
            self.conn.execute(
                'INSERT INTO todos (id, text, done, created) VALUES (?, ?, 0, ?)',
                (tid, text, tid)
            )
            self.conn.commit()
        return tid

    def get_todos(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT id, text, done, created FROM todos ORDER BY created')
            return [dict(row) for row in cur.fetchall()]

    def update_todo(self, tid, done=True):
        with self.lock:
            self.conn.execute('UPDATE todos SET done = ? WHERE id = ?', (1 if done else 0, tid))
            self.conn.commit()

    def del_todo(self, tid):
        with self.lock:
            self.conn.execute('DELETE FROM todos WHERE id = ?', (tid,))
            self.conn.commit()

    # ── Напоминания (!remind) — персистентные, переживают рестарт ──

    def add_reminder(self, chat_id, text, due_at):
        with self.lock:
            cur = self.conn.execute(
                'INSERT INTO reminders (chat_id, text, due_at) VALUES (?, ?, ?)',
                (int(chat_id), text, int(due_at))
            )
            self.conn.commit()
            return cur.lastrowid

    def get_due_reminders(self, now):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT id, chat_id, text, due_at FROM reminders WHERE due_at <= ? ORDER BY due_at',
                        (int(now),))
            return [dict(row) for row in cur.fetchall()]

    def get_pending_reminders(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT id, chat_id, text, due_at FROM reminders ORDER BY due_at')
            return [dict(row) for row in cur.fetchall()]

    def del_reminder(self, rid):
        with self.lock:
            self.conn.execute('DELETE FROM reminders WHERE id = ?', (int(rid),))
            self.conn.commit()

    # ── Stats ──

    def bump_stat(self, key, n=1):
        with self.lock:
            self.conn.execute(
                'INSERT INTO stats (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = value + ?',
                (key, n, n)
            )
            self.conn.commit()

    def all_stats(self):
        raw = self._get_all('stats')
        return {k: int(v) for k, v in raw.items()}

    def clear_all(self):
        with self.lock:
            for t in _VALID_TABLES:
                self.conn.execute(f'DELETE FROM {t}')
            self.conn.commit()

    # ── Reply settings (per-user + default) ──

    def get_reply_text(self, uid):
        return self._get('reply_settings', str(uid))

    def set_reply_text(self, uid, text):
        self._set('reply_settings', str(uid), text)

    def get_default_reply(self):
        return self._get('reply_settings', 'default')

    def set_default_reply(self, text):
        self._set('reply_settings', 'default', text)

    # ── Sessions (!watch) ──

    def all_sessions(self):
        raw = self._get_all('sessions')
        return {k: v for k, v in raw.items()}

    def save_session(self, hash_key, data_json):
        self._set('sessions', hash_key, data_json)

    def clear_sessions(self):
        self._delete_all('sessions')

    # ── Protected chats (!protect) ──

    # В таблице первичный ключ называется chat_id (а не key), поэтому
    # generic-хелперы _set/_keys/_delete тут не подходят — используем явный SQL.
    def add_protected_chat(self, chat_id):
        with self.lock:
            self.conn.execute(
                'INSERT OR REPLACE INTO protected_chats (chat_id, value) VALUES (?, ?)',
                (str(chat_id), str(int(time.time())))
            )
            self.conn.commit()

    def get_protected_chat_ids(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT chat_id FROM protected_chats')
            out = []
            for row in cur.fetchall():
                try:
                    out.append(int(row[0]))
                except (TypeError, ValueError):
                    continue
            return out

    def del_protected_chat(self, chat_id):
        with self.lock:
            self.conn.execute('DELETE FROM protected_chats WHERE chat_id = ?', (str(chat_id),))
            self.conn.commit()

    def clear_protected_chats(self):
        with self.lock:
            self.conn.execute('DELETE FROM protected_chats')
            self.conn.commit()

    # ── AI: история переписки ──

    def save_ai_msg(self, peer_id, sender_id, text, ts=None, msg_id=None):
        """Сохранить реплику для AI-контекста. msg_id делает вставку
        идемпотентной: повторный !sync не плодит дубликаты."""
        ts = int(ts or time.time())
        with self.lock:
            cur = self.conn.cursor()
            try:
                if msg_id is None:
                    cur.execute(
                        'INSERT INTO ai_msgs (peer_id, sender_id, text, date) VALUES (?, ?, ?, ?)',
                        (int(peer_id), int(sender_id or 0), text or '', ts)
                    )
                else:
                    cur.execute(
                        'INSERT OR IGNORE INTO ai_msgs (peer_id, sender_id, text, date, msg_id) '
                        'VALUES (?, ?, ?, ?, ?)',
                        (int(peer_id), int(sender_id or 0), text or '', ts, int(msg_id))
                    )
                self.conn.commit()
            except (ValueError, TypeError):
                return
            self._ai_saves = getattr(self, '_ai_saves', 0) + 1
            due = self._ai_saves % 100 == 0
        if due:
            try:
                self.prune_ai_msgs()
            except Exception as ex:
                logger.debug(f"prune_ai_msgs: {ex}")

    def get_ai_msgs(self, peer_id, limit=50, sender=None):
        with self.lock:
            cur = self.conn.cursor()
            if sender is not None:
                cur.execute(
                    'SELECT sender_id, text, date FROM ai_msgs '
                    'WHERE peer_id = ? AND sender_id = ? '
                    'ORDER BY date DESC, id DESC LIMIT ?',
                    (int(peer_id), int(sender), int(limit))
                )
            else:
                cur.execute(
                    'SELECT sender_id, text, date FROM ai_msgs '
                    'WHERE peer_id = ? ORDER BY date DESC, id DESC LIMIT ?',
                    (int(peer_id), int(limit))
                )
            rows = list(reversed(cur.fetchall()))
            return [dict(r) for r in rows]

    # ─── Обещания: открытые записи живут в БД и переживают перезапуск ───

    def save_ai_promises(self, peer_id, items):
        """Добавить обещания; дубли по тексту/source_msg_id игнорируются."""
        try:
            peer = int(peer_id)
        except (TypeError, ValueError):
            return 0
        now = int(time.time())
        added = 0
        with self.lock:
            cur = self.conn.cursor()
            known = {(row[0], row[1]) for row in cur.execute(
                'SELECT text, source_msg_id FROM ai_promises WHERE peer_id = ?',
                (peer,)).fetchall()}
            for item in items or ():
                if not isinstance(item, dict):
                    continue
                text = ' '.join(str(item.get('text') or '').split())[:1000]
                if not text:
                    continue
                raw_src = item.get('source_msg_id')
                try:
                    src = int(raw_src) if raw_src not in (None, '') else None
                except (TypeError, ValueError):
                    src = None
                if (text, src) in known:
                    continue
                raw_promiser = item.get('promiser_id')
                try:
                    promiser = int(raw_promiser) if raw_promiser not in (None, '') else None
                except (TypeError, ValueError):
                    promiser = None
                cur.execute(
                    "INSERT INTO ai_promises "
                    "(peer_id, promiser_id, who_label, source_msg_id, text, due_at, "
                    "status, created_at, last_checked_at, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                    (peer, promiser,
                     ' '.join(str(item.get('who_label') or '').split())[:120],
                     src, text,
                     ' '.join(str(item.get('due_at') or '').split())[:120],
                     now, now,
                     ' '.join(str(item.get('note') or '').split())[:300]))
                known.add((text, src))
                added += 1
            self.conn.commit()
        return added

    def get_ai_promises(self, peer_id=None, status='open', limit=100):
        """status=None — все записи, включая архив (никогда не удаляются)."""
        with self.lock:
            cur = self.conn.cursor()
            where, args = [], []
            if peer_id is not None:
                where.append('peer_id = ?')
                args.append(int(peer_id))
            if status is not None:
                where.append('status = ?')
                args.append(status)
            sql = 'SELECT * FROM ai_promises'
            if where:
                sql += ' WHERE ' + ' AND '.join(where)
            sql += (" ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, "
                    'id DESC LIMIT ?')
            args.append(int(limit))
            return [dict(r) for r in cur.execute(sql, args).fetchall()]

    def mark_ai_promise(self, promise_id, status, note=''):
        """Сменить статус; закрытые строки остаются в архиве."""
        if status not in ('open', 'done', 'cancelled'):
            return False
        try:
            pid = int(promise_id)
        except (TypeError, ValueError):
            return False
        with self.lock:
            cur = self.conn.execute(
                "UPDATE ai_promises SET status = ?, resolved_at = ?, "
                "note = CASE WHEN ? = '' THEN note ELSE ? END, last_checked_at = ? "
                "WHERE id = ?",
                (status, int(time.time()) if status != 'open' else None,
                 note or '', note or '', int(time.time()), pid))
            self.conn.commit()
            return cur.rowcount > 0

    def count_ai_msgs(self, peer_id=None, sender=None):
        with self.lock:
            cur = self.conn.cursor()
            if peer_id is not None and sender is not None:
                cur.execute('SELECT COUNT(*) FROM ai_msgs WHERE peer_id = ? AND sender_id = ?',
                            (int(peer_id), int(sender)))
            elif peer_id is not None:
                cur.execute('SELECT COUNT(*) FROM ai_msgs WHERE peer_id = ?', (int(peer_id),))
            else:
                cur.execute('SELECT COUNT(*) FROM ai_msgs')
            row = cur.fetchone()
            return row[0] if row else 0

    def clear_ai_msgs(self, peer_id=None):
        with self.lock:
            if peer_id is None:
                self.conn.execute('DELETE FROM ai_msgs')
            else:
                self.conn.execute('DELETE FROM ai_msgs WHERE peer_id = ?', (int(peer_id),))
            self.conn.commit()

    def prune_ai_msgs(self, per_peer=None, total=None):
        """Ретеншн: не даём AI-истории расти бесконечно (в отличие от Prim)."""
        def _int_env(name, default):
            try:
                return int(os.environ.get(name, default) or default)
            except (TypeError, ValueError):
                return default
        per_peer = int(per_peer or _int_env('AI_HISTORY_PER_PEER', 1500))
        total = int(total or _int_env('AI_HISTORY_TOTAL', 50000))
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT DISTINCT peer_id FROM ai_msgs')
            peers = [r[0] for r in cur.fetchall()]
            for peer in peers:
                cur.execute(
                    'DELETE FROM ai_msgs WHERE peer_id = ? AND id NOT IN '
                    '(SELECT id FROM ai_msgs WHERE peer_id = ? ORDER BY date DESC, id DESC LIMIT ?)',
                    (peer, peer, per_peer)
                )
            cur.execute(
                'DELETE FROM ai_msgs WHERE id NOT IN '
                '(SELECT id FROM ai_msgs ORDER BY date DESC, id DESC LIMIT ?)',
                (total,)
            )
            self.conn.commit()

    # ── AI: факты о пользователях ──

    def add_ai_fact(self, user_id, fact):
        with self.lock:
            self.conn.execute(
                'INSERT OR IGNORE INTO ai_facts (user_id, fact, date) VALUES (?, ?, ?)',
                (int(user_id), fact, int(time.time()))
            )
            self.conn.commit()

    def get_ai_facts(self, user_id):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT fact FROM ai_facts WHERE user_id = ? ORDER BY date', (int(user_id),))
            return [r[0] for r in cur.fetchall()]

    def clear_ai_facts(self, user_id):
        with self.lock:
            self.conn.execute('DELETE FROM ai_facts WHERE user_id = ?', (int(user_id),))
            self.conn.commit()

    # ── AI: автоответчик на пользователя ──

    def set_ai_auto(self, user_id, enabled, role='', prompt=''):
        with self.lock:
            self.conn.execute(
                'INSERT INTO ai_auto (user_id, enabled, role, prompt) VALUES (?, ?, ?, ?) '
                'ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled, '
                'role = excluded.role, prompt = excluded.prompt',
                (int(user_id), 1 if enabled else 0, role or '', prompt or '')
            )
            self.conn.commit()

    def get_ai_auto(self, user_id):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT enabled, role, prompt FROM ai_auto WHERE user_id = ?', (int(user_id),))
            row = cur.fetchone()
        if row is None:
            return {'enabled': False, 'role': '', 'prompt': ''}
        return {'enabled': bool(row[0]), 'role': row[1] or '', 'prompt': row[2] or ''}

    def is_ai_auto_on(self, user_id):
        return self.get_ai_auto(user_id)['enabled']

    # ── AI: blacklist / whitelist ──

    def toggle_ai_list(self, user_id, list_type):
        """True — добавлен, False — удалён (переключение)."""
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT list_type FROM ai_lists WHERE user_id = ?', (int(user_id),))
            row = cur.fetchone()
            if row is None:
                self.conn.execute(
                    'INSERT INTO ai_lists (user_id, list_type) VALUES (?, ?)',
                    (int(user_id), list_type)
                )
                self.conn.commit()
                return True
            self.conn.execute('DELETE FROM ai_lists WHERE user_id = ?', (int(user_id),))
            self.conn.commit()
            return False

    def clear_ai_list(self, list_type):
        with self.lock:
            self.conn.execute('DELETE FROM ai_lists WHERE list_type = ?', (list_type,))
            self.conn.commit()

    def get_ai_list(self, list_type):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT user_id FROM ai_lists WHERE list_type = ?', (list_type,))
            return [r[0] for r in cur.fetchall()]

    def is_ai_blocked(self, user_id):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT 1 FROM ai_lists WHERE user_id = ? AND list_type = ?',
                        (int(user_id), 'black'))
            return cur.fetchone() is not None

    # ── AI: режим подсказок (prompt) ──

    def set_ai_prompt_mode(self, user_id, on):
        with self.lock:
            self.conn.execute(
                'INSERT INTO ai_flags (user_id, prompt_mode) VALUES (?, ?) '
                'ON CONFLICT(user_id) DO UPDATE SET prompt_mode = excluded.prompt_mode',
                (int(user_id), 1 if on else 0)
            )
            self.conn.commit()

    def get_ai_prompt_mode(self, user_id):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT prompt_mode FROM ai_flags WHERE user_id = ?', (int(user_id),))
            row = cur.fetchone()
            return bool(row and row[0])

    # ── AI: квест ──

    def set_ai_quest(self, chat_id, state_json):
        with self.lock:
            self.conn.execute(
                'INSERT INTO ai_quest (chat_id, state) VALUES (?, ?) '
                'ON CONFLICT(chat_id) DO UPDATE SET state = excluded.state',
                (int(chat_id), state_json)
            )
            self.conn.commit()

    def get_ai_quest(self, chat_id):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT state FROM ai_quest WHERE chat_id = ?', (int(chat_id),))
            row = cur.fetchone()
            return row[0] if row else None

    def clear_ai_quest(self, chat_id):
        with self.lock:
            self.conn.execute('DELETE FROM ai_quest WHERE chat_id = ?', (int(chat_id),))
            self.conn.commit()

    # ── Online watch ──

    def add_online_watch(self, target_id, target_username, message, owner_id, chat_id, md=0):
        """Добавить трек или обновить существующий (текст/username/владелец).

        Повторное добавление той же цели НЕ сбрасывает last_fired/created_at/
        last_status/status_since: иначе повтор обошёл бы кулдаун и уведомление
        ушло бы второй раз.
        Возвращает True — трек добавлен, False — существующий обновлён."""
        with self.lock:
            cur = self.conn.cursor()
            exists = cur.execute(
                'SELECT 1 FROM online_watch WHERE target_id = ?',
                (int(target_id),)).fetchone()
            if exists:
                cur.execute(
                    'UPDATE online_watch SET target_username = ?, message = ?, owner_id = ?, chat_id = ?, md = ? '
                    'WHERE target_id = ?',
                    (target_username or '', message, int(owner_id), int(chat_id), 1 if md else 0,
                     int(target_id)))
            else:
                cur.execute(
                    'INSERT INTO online_watch (target_id, target_username, message, owner_id, chat_id, last_status, enabled, created_at, md) '
                    'VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)',
                    (int(target_id), target_username or '', message, int(owner_id), int(chat_id),
                     'unknown', int(time.time()), 1 if md else 0))
            self.conn.commit()
            return not exists

    def get_online_watches(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT * FROM online_watch WHERE enabled = 1')
            return [dict(row) for row in cur.fetchall()]

    def update_online_watch_status(self, watch_id, last_status, source='status'):
        """Смена статуса цели: фиксирует момент (status_since — для длительностей
        в логах) и пишет переход в online_watch_log (история для !onwatchlog).
        Смена определяется чтением текущего значения под локом: одна строка лога
        на реальную смену, status_since не сдвигается при повторном том же статусе.
        Ретеншн — НЕ здесь: DELETE вынесен в prune_online_watch_log (периодически),
        чтобы не тормозить путь записи уведомления."""
        with self.lock:
            cur = self.conn.cursor()
            row = cur.execute(
                'SELECT target_id, last_status FROM online_watch WHERE id = ?',
                (int(watch_id),)).fetchone()
            if not row or row[1] == last_status:
                return False
            now = int(time.time())
            cur.execute('UPDATE online_watch SET last_status = ?, status_since = ? WHERE id = ?',
                        (last_status, now, int(watch_id)))
            cur.execute('INSERT INTO online_watch_log (target_id, status, ts, source) VALUES (?, ?, ?, ?)',
                        (row[0], last_status, now, source))
            self.conn.commit()
            return True

    def remove_online_watch(self, target_id=None, watch_id=None):
        with self.lock:
            if watch_id is not None:
                self.conn.execute('DELETE FROM online_watch WHERE id = ?', (watch_id,))
            elif target_id is not None:
                self.conn.execute('DELETE FROM online_watch WHERE target_id = ?', (int(target_id),))
            self.conn.commit()

    def touch_online_watch_fired(self, watch_id, ts=None):
        """Отметка момента последней ОТПРАВКИ уведомления «появился в сети».
        Кулдаун меряется от неё (а не от последней активности) и хранится в БД —
        иначе активный человек навсегда глушит все уведомления."""
        with self.lock:
            self.conn.execute(
                'UPDATE online_watch SET last_fired = ? WHERE id = ?',
                (int(ts if ts else time.time()), int(watch_id))
            )
            self.conn.commit()

    def touch_online_watch_session(self, watch_id, ts=None):
        """Продлить активность текущей онлайн-сессии (typing/сообщения/галочки
        и успешная отправка уведомления). Пока now - last_activity <=
        SESSION_TIMEOUT, сессия открыта и новые триггеры только обновляют
        таймер — одно уведомление на сессию, а не раз в кулдаун."""
        with self.lock:
            self.conn.execute(
                'UPDATE online_watch SET last_activity = ? WHERE id = ?',
                (int(ts if ts else time.time()), int(watch_id)))
            self.conn.commit()

    def touch_online_watch_exit(self, watch_id, ts=None):
        """Зафиксировать момент выхода цели из сети.

        Строгий режим ONWATCH: уведомление при каждом входе. Если выход
        наблюдён (событие offline или свежий was_online), следующий вход
        считается новой сессией и не глушится обычным кулдауном отправки."""
        with self.lock:
            self.conn.execute(
                'UPDATE online_watch SET last_exit = ? WHERE id = ?',
                (int(ts if ts else time.time()), int(watch_id)))
            self.conn.commit()

    def close_online_watch_session(self, watch_id):
        """Явное закрытие сессии (выход из сети / уход из «недавно»):
        следующая активность — новая сессия и новое уведомление."""
        with self.lock:
            self.conn.execute('UPDATE online_watch SET last_activity = 0 WHERE id = ?',
                              (int(watch_id),))
            self.conn.commit()

    def get_online_watch_by_target(self, target_id):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT * FROM online_watch WHERE target_id = ?', (int(target_id),))
            row = cur.fetchone()
            return dict(row) if row else None

    def mark_online_watch_fire(self, watch_id, source, ok=True, error=''):
        """Итог отправки уведомления: счётчик fires, последний источник триггера
        (метрика «событие vs поллер») и текст ошибки — см. !onwatchstatus."""
        with self.lock:
            self.conn.execute(
                'UPDATE online_watch SET last_source = ?, last_error = ?, fires = fires + ? WHERE id = ?',
                (source, None if ok else (error or '')[:200],
                 1 if ok else 0, int(watch_id)))
            self.conn.commit()

    def get_online_watch_log(self, target_id, since_ts=0):
        """Переходы статуса цели с since_ts (для !onwatchlog), по возрастанию."""
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT status, ts, source FROM online_watch_log '
                        'WHERE target_id = ? AND ts >= ? ORDER BY ts',
                        (int(target_id), int(since_ts)))
            return [{'status': r[0], 'ts': r[1], 'source': r[2]}
                    for r in cur.fetchall()]

    def append_online_watch_log(self, target_id, status, ts, source='status'):
        """Строка лога сессий БЕЗ смены last_status — например, «мигающая»
        сессия по was_online: статус не изменился (offline → offline), но сама
        сессия была и должна быть видна в !onwatchlog. source различает
        реальный вход (status), пропущенную сессию (flicker) и канал активности
        (typing/message/read) — для скрытого last-seen это и есть история."""
        with self.lock:
            self.conn.execute(
                'INSERT INTO online_watch_log (target_id, status, ts, source) VALUES (?, ?, ?, ?)',
                (int(target_id), status, int(ts), source))
            self.conn.commit()

    def prune_online_watch_log(self, days=90):
        """Ретеншн истории сессий. Вызывается периодически из поллера —
        DELETE вне критического пути записи уведомления."""
        with self.lock:
            cur = self.conn.execute(
                'DELETE FROM online_watch_log WHERE ts < ?',
                (int(time.time()) - int(days) * 86400,))
            self.conn.commit()
            return cur.rowcount

    # ── Чёрный список ЛС (!pmwatch) ──

    def get_pm_muted_chats(self):
        """ID чатов, выключенных из мониторинга удалений/правок."""
        raw = self.get_saved(PM_MUTED_KEY, '') or ''
        out = set()
        for part in str(raw).split(','):
            part = part.strip()
            if not part:
                continue
            try:
                out.add(int(part))
            except ValueError:
                continue
        return out

    def set_pm_muted_chats(self, chat_ids):
        ids = sorted({int(i) for i in chat_ids})
        self.set_saved(PM_MUTED_KEY, ','.join(str(i) for i in ids))
        return set(ids)

    def mute_pm_chat(self, chat_id):
        return self.set_pm_muted_chats(self.get_pm_muted_chats() | {int(chat_id)})

    def unmute_pm_chat(self, chat_id):
        return self.set_pm_muted_chats(self.get_pm_muted_chats() - {int(chat_id)})

    def pm_chat_monitored(self, chat_id):
        """Следим ли за чатом. Ошибка/пустой ID — считаем, что следим:
        лучше лишнее уведомление, чем молча потерянная функция."""
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            return True
        return cid not in self.get_pm_muted_chats()

    def purge_pm_chat(self, chat_id):
        """Удаляет кэш сообщений чата. Возвращает пути медиафайлов —
        файлы удаляет вызывающий код (Storage не знает про диск/media)."""
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT media_file FROM pm_msg_cache '
                'WHERE chat_id = ? AND media_file IS NOT NULL AND media_file != ""',
                (int(chat_id),)
            )
            files = [row[0] for row in cur.fetchall() if row[0]]
            cur.execute('DELETE FROM pm_msg_cache WHERE chat_id = ?', (int(chat_id),))
            self.conn.commit()
        return files

    # ── PM message cache ──

    def cache_pm_message(self, chat_id, msg_id, sender_id, sender_name, text, has_media, media_type):
        now = int(time.time())
        with self.lock:
            # UPSERT, а не INSERT OR REPLACE: правка подписи не должна стирать
            # уже сохранённый медиафайл (иначе при удалении его не переслать).
            self.conn.execute(
                'INSERT INTO pm_msg_cache (chat_id, msg_id, sender_id, sender_name, text, has_media, media_type, date, verified, last_seen) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?) '
                'ON CONFLICT(chat_id, msg_id) DO UPDATE SET '
                'sender_id = excluded.sender_id, sender_name = excluded.sender_name, '
                'text = excluded.text, has_media = excluded.has_media, '
                'media_type = excluded.media_type, verified = 1, last_seen = excluded.last_seen',
                (int(chat_id), int(msg_id), int(sender_id) if sender_id else 0, sender_name or '', text or '', 1 if has_media else 0, media_type or '', now, now)
            )
            self.conn.commit()

    def set_pm_media_file(self, chat_id, msg_id, path):
        """Запомнить локальный файл медиа для сообщения (для re-send при удалении)."""
        with self.lock:
            self.conn.execute(
                'UPDATE pm_msg_cache SET media_file = ? WHERE chat_id = ? AND msg_id = ?',
                (path or '', int(chat_id), int(msg_id))
            )
            self.conn.commit()

    def set_pm_file_meta(self, chat_id, msg_id, file_name, file_size):
        """Имя и размер исходного файла — показываются в уведомлении об удалении."""
        with self.lock:
            self.conn.execute(
                'UPDATE pm_msg_cache SET file_name = ?, file_size = ? WHERE chat_id = ? AND msg_id = ?',
                (file_name or '', int(file_size or 0), int(chat_id), int(msg_id))
            )
            self.conn.commit()

    def get_pm_cached(self, chat_id, msg_id):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT * FROM pm_msg_cache WHERE chat_id = ? AND msg_id = ?', (int(chat_id), int(msg_id)))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_pm_cached_by_msg(self, msg_id):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT * FROM pm_msg_cache WHERE msg_id = ? ORDER BY date DESC LIMIT 1', (int(msg_id),))
            row = cur.fetchone()
            return dict(row) if row else None

    def delete_pm_cached(self, chat_id, msg_id):
        with self.lock:
            self.conn.execute('DELETE FROM pm_msg_cache WHERE chat_id = ? AND msg_id = ?', (int(chat_id), int(msg_id)))
            self.conn.commit()

    def get_pm_chats_for_check(self, max_age=259200, limit=20):
        """Чаты с самыми свежими сообщениями в кэше (для сверки)."""
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT chat_id, MAX(date) AS d FROM pm_msg_cache WHERE date >= ? '
                'GROUP BY chat_id ORDER BY d DESC LIMIT ?',
                (int(time.time()) - int(max_age), int(limit))
            )
            return [row['chat_id'] for row in cur.fetchall()]

    def get_pm_rows_for_check(self, chat_id, max_age=259200, limit=100):
        """Сообщения конкретного чата, которые надо проверить на существование."""
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT * FROM pm_msg_cache WHERE chat_id = ? AND date >= ? '
                'ORDER BY msg_id DESC LIMIT ?',
                (int(chat_id), int(time.time()) - int(max_age), int(limit))
            )
            return [dict(row) for row in cur.fetchall()]

    def mark_pm_alive(self, chat_id, msg_ids):
        if not msg_ids:
            return
        ids = [int(i) for i in msg_ids]
        placeholders = ','.join('?' * len(ids))
        with self.lock:
            self.conn.execute(
                f'UPDATE pm_msg_cache SET verified = 1, last_seen = ? '
                f'WHERE chat_id = ? AND msg_id IN ({placeholders})',
                [int(time.time()), int(chat_id), *ids]
            )
            self.conn.commit()

    def prune_pm_media(self, keep=300):
        """Ограничиваем число сохранённых медиафайлов (последние keep).
        Возвращает пути удалённых записей — их файлы удаляет вызывающий код."""
        removed = []
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT chat_id, msg_id, media_file FROM pm_msg_cache '
                'WHERE media_file IS NOT NULL AND media_file != "" '
                'ORDER BY date DESC LIMIT -1 OFFSET ?',
                (int(keep),)
            )
            for row in cur.fetchall():
                removed.append(row['media_file'])
                self.conn.execute(
                    'UPDATE pm_msg_cache SET media_file = NULL WHERE chat_id = ? AND msg_id = ?',
                    (row['chat_id'], row['msg_id'])
                )
            self.conn.commit()
        return removed

    def reset_pm_verified(self):
        """Сброс базы на старте: первая сверка только фиксирует наличие,
        чтобы не заваливать уведомлениями за время оффлайна."""
        with self.lock:
            self.conn.execute('UPDATE pm_msg_cache SET verified = 0')
            self.conn.commit()

    def clean_pm_cache(self, limit=4000):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute('SELECT COUNT(*) as c FROM pm_msg_cache')
            row = cur.fetchone()
            cnt = row['c'] if row else 0
            if cnt > limit:
                cur.execute(
                    'SELECT media_file FROM pm_msg_cache WHERE rowid IN '
                    '(SELECT rowid FROM pm_msg_cache ORDER BY date ASC LIMIT ?) '
                    'AND media_file IS NOT NULL AND media_file != ""',
                    (cnt - limit,)
                )
                files = [r['media_file'] for r in cur.fetchall()]
                cur.execute('DELETE FROM pm_msg_cache WHERE rowid IN (SELECT rowid FROM pm_msg_cache ORDER BY date ASC LIMIT ?)', (cnt - limit,))
                self.conn.commit()
                for p in files:
                    try:
                        if os.path.isfile(p):
                            os.remove(p)
                    except OSError:
                        pass
