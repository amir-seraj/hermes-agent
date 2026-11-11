"""Opt-in Telegram widget deck: live Hermes agent state plus one-tap controls.

Uses Bot API 10.4 widgets (sendWidget / widget_query / answerWidgetQuery).
PTB 22.x has no widget types yet, so queries arrive via Update.api_kwargs and
calls go through Bot._post. Every action is a whitelisted slash command fed
through the gateway's normal message path, so it gets the same handling as a
typed command; cron "run now" is CLI-only there, so it calls cron.jobs directly.
"""
import asyncio
import json
import os
import re
import sqlite3
import stat
import time
import zlib
from contextlib import closing
from pathlib import Path

import yaml

from hermes_constants import get_hermes_home

_ID = re.compile(r'[A-Za-z0-9_.-]{1,48}')  # session and cron job ids
_COMMANDS = {'stop': ('/stop', 'Stopping the run.'), 'approve': ('/approve', 'Approved.'),
             'deny': ('/deny', 'Denied.'), 'restart': ('/restart', 'Restarting the gateway.'),
             'fast': ('/fast', 'Toggled fast mode.'), 'yolo': ('/yolo', 'Toggled auto-approve.')}
_PROVIDERS = {'openai-codex': 'Codex', 'anthropic': 'Claude', 'openrouter': 'OpenRouter'}
_usage = {'at': 0.0, 'value': None, 'task': None}


def _read(path, limit=262144):
    # Refuse special files (including FIFOs) and symlinks at the leaf.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError('not a regular file')
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError('source too large')
    return data.decode('utf-8')


def _text(value, limit):
    return ' '.join(str(value).split())[:limit]


def _ago(ts):
    s = max(0, int(time.time() - (ts or 0)))
    if s < 3600:
        return f'{s // 60}m'
    return f'{s // 3600}h {s % 3600 // 60}m' if s < 86400 else f'{s // 86400}d'


def _db():
    uri = (get_hermes_home() / 'state.db').resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=0.3)
    db.set_progress_handler(lambda: 1, 200000)
    return closing(db)


def _sessions(running_ids):
    """Three most recently active titled sessions (titles are what /resume takes)."""
    with _db() as db:
        rows = db.execute(
            'SELECT id, title, ended_at, coalesce((SELECT max(timestamp) FROM messages m'
            ' WHERE m.session_id = s.id), started_at) AS seen FROM sessions s'
            " WHERE coalesce(title, '') != '' ORDER BY started_at DESC LIMIT 20").fetchall()
        billing = db.execute(
            'SELECT billing_provider, billing_base_url FROM sessions WHERE billing_provider'
            ' IS NOT NULL ORDER BY started_at DESC LIMIT 1').fetchone()
    rows.sort(key=lambda r: r[3] or 0, reverse=True)
    out = [[sid, _text(title, 28), 'w' if sid in running_ids else 'd' if ended else 'i', _ago(seen)]
           for sid, title, ended, seen in rows[:3]]
    return out, billing


def _title(session_id):
    with _db() as db:
        row = db.execute('SELECT title FROM sessions WHERE id = ?', (session_id,)).fetchone()
    return _text(row[0], 200) if row and row[0] else None


def _refresh_usage(billing):
    from agent.account_usage import fetch_account_usage
    snap = fetch_account_usage(billing[0], base_url=billing[1]) if billing else None
    windows = [w for w in (snap.windows if snap else ()) if w.used_percent is not None]
    w = next((w for w in windows if 'week' in w.label.lower()), windows[0] if windows else None)
    name = _PROVIDERS.get(snap.provider, snap.provider) if snap else ''
    _usage['value'] = w and [_text(f'{name} {w.label.lower()}', 24), round(w.used_percent),
                             w.reset_at.astimezone().strftime('%a %H:%M') if w.reset_at else '']


def _usage_value(billing):
    # Provider usage APIs are slow; refresh at most every 5 minutes off the request path.
    if time.time() - _usage['at'] > 300 and not (_usage['task'] and not _usage['task'].done()):
        _usage['at'] = time.time()
        _usage['task'] = asyncio.ensure_future(asyncio.to_thread(_refresh_usage, billing))
    return _usage['value']


def _launchers():
    try:
        items = yaml.safe_load(_read(get_hermes_home() / 'xdeck.yaml', 65536))['launchers']
        return [(_text(i['label'], 18), _text(i['prompt'], 2000)) for i in items[:6]
                if isinstance(i, dict) and i.get('label') and i.get('prompt')]
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        return []


def _fit(payload, key):
    # answerWidgetQuery data is at most 1024 UTF-8 bytes: drop list items until it fits.
    while len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > 1024 and payload[key]:
        payload[key].pop()
    return payload


def _controls():
    from cron.jobs import load_jobs
    jobs = sorted(load_jobs(), key=lambda j: (j.get('last_status') not in ('error', 'failed'),
                                             j.get('enabled', True) is False, str(j.get('name'))))
    status = lambda j: 'e' if j.get('last_status') in ('error', 'failed') else 'o' if j.get('enabled', True) else 'p'
    return _fit({'l': [label for label, _ in _launchers()],
                 'j': [[j['id'], _text(j.get('name') or j['id'], 22), status(j)]
                       for j in jobs if isinstance(j.get('id'), str) and _ID.fullmatch(j['id'])]}, 'j')


def _run_job(job_id):
    from cron.jobs import load_jobs, trigger_job
    job = next((j for j in load_jobs() if j.get('id') == job_id), None)
    if not job or not trigger_job(job_id):
        return 'Job not found.'
    return f"Queued {_text(job.get('name') or job_id, 40)}."



def _runner(adapter):
    return getattr(getattr(adapter, '_message_handler', None), '__self__', None)


def _source(adapter, user, chat):
    return adapter.build_source(chat_id=str(chat['id']), chat_type='dm', user_id=str(user['id']),
                                user_name=user.get('first_name'))


async def _state(adapter, user, chat):
    from tools import approval
    runner = _runner(adapter)
    agents = getattr(runner, '_running_agents', None) or {}
    key = runner._session_key_for_source(_source(adapter, user, chat))
    with approval._lock:
        queue = list(approval._gateway_queues.get(key) or ())
    rows, billing = await asyncio.to_thread(_sessions, {getattr(a, 'session_id', None) for a in agents.values()})
    command = _text(queue[0].data.get('command', ''), 160) if queue else ''
    return {'s': 't' if queue else 'w' if key in agents else 'i', 'c': command, 'n': len(queue),
            'y': int(approval.is_session_yolo_enabled(key)), 'l': rows, 'u': _usage_value(billing)}


async def _answer(adapter, user, chat, data):
    if data == 'a':
        return await _state(adapter, user, chat)
    if data == 'c':
        return await asyncio.to_thread(_controls)
    kind, _, arg = data.partition(':')
    if kind == 'k' and arg in _COMMANDS:
        command, done = _COMMANDS[arg]
    elif kind == 'r' and _ID.fullmatch(arg):
        title = await asyncio.to_thread(_title, arg)
        command, done = title and '/resume ' + title, f'Resuming {_text(title, 40)}.'
    elif kind == 'l' and re.fullmatch(r'[0-5]', arg):
        items = _launchers()
        item = items[int(arg)] if int(arg) < len(items) else None
        command, done = item and '/background ' + item[1], item and f'Started {item[0]}.'
    elif kind == 'j' and _ID.fullmatch(arg):
        return {'t': await asyncio.to_thread(_run_job, arg)}
    else:
        return {'t': 'Unknown action.'}
    if not command:
        return {'t': 'No longer available.'}
    from gateway.platforms.base import MessageEvent, MessageType
    # shortcut: always targets the main DM session, not a DM topic thread; add thread_id if topics matter.
    await adapter.handle_message(MessageEvent(text=command, message_type=MessageType.COMMAND,
                                              source=_source(adapter, user, chat)))
    return {'t': done}


def enabled():
    return os.getenv('HERMES_TELEGRAM_X_WIDGET', '').lower() in ('1', 'true', 'yes', 'on')


def allowed(adapter, user_id, chat_id, chat_type):
    # Require explicit numeric IDs even when the general gateway is open to all.
    if type(user_id) is not int or user_id <= 0 or type(chat_id) is not int:
        return False
    ids = {v.strip() for v in os.getenv('TELEGRAM_ALLOWED_USERS', '').split(',')}
    if str(user_id) not in ids or chat_id != user_id or chat_type != 'private':
        return False
    try:
        return bool(adapter._is_callback_user_authorized(
            str(user_id), chat_id=str(chat_id), chat_type='private', fail_closed_on_error=True))
    except Exception:
        return False


def allowed_updates(update_cls):
    result = list(update_cls.ALL_TYPES)
    if enabled() and 'widget_query' not in result:
        result.append('widget_query')
    return result


async def send(adapter, message):
    if not enabled():
        return False
    user = getattr(message, 'from_user', None)
    chat = getattr(message, 'chat', None)
    if not allowed(adapter, getattr(user, 'id', None), getattr(chat, 'id', None), getattr(chat, 'type', None)):
        return True
    try:
        html_path = os.getenv('HERMES_X_WIDGET_HTML')
        path = Path(html_path).expanduser() if html_path else Path(__file__).resolve().parents[2] / 'widgets' / 'x-command-deck.html'
        html = _read(path, 32768)
        raw = html.encode('utf-8')
        if not raw or len(raw) > 32768 or len(zlib.compress(raw, 9)) > 4096 or b'\x00' in raw:
            raise ValueError('widget HTML exceeds Telegram limits')
        await asyncio.wait_for(adapter._bot._post('sendWidget', {'chat_id': chat.id, 'html': html, 'allow_queries': True}), timeout=5)
    except Exception:
        # Do not log request URL, bot token, or raw API exceptions.
        await message.reply_text('Could not send the widget. Check the widget file and Telegram client support.')
    return True


async def handle(adapter, update, context):
    if not enabled():
        return
    query = getattr(update, 'api_kwargs', {}).get('widget_query')
    if not isinstance(query, dict):
        return
    qid = query.get('id')
    if not isinstance(qid, str) or not qid or len(qid) > 512:
        return
    user = query.get('from') if isinstance(query.get('from'), dict) else {}
    chat = query.get('chat') if isinstance(query.get('chat'), dict) else {}
    data = query.get('data')
    msgid = query.get('message_id')
    valid = (allowed(adapter, user.get('id'), chat.get('id'), chat.get('type'))
             and type(msgid) is int and msgid > 0 and not query.get('inline_message_id')
             and not query.get('widget_id'))
    if not valid:
        response = {'t': 'Not authorized.'}
    elif not isinstance(data, str) or len(data) > 64:
        response = {'t': 'Unknown action.'}
    else:
        try:
            response = await asyncio.wait_for(_answer(adapter, user, chat, data), timeout=8)
        except Exception:
            response = {'t': 'Temporarily unavailable.'}
    text = json.dumps(response, ensure_ascii=False)
    if len(text.encode('utf-8')) > 1024:
        text = '{"t":"Response too large."}'
    try:
        await asyncio.wait_for(adapter._bot._post('answerWidgetQuery', {'widget_query_id': qid, 'data': text}), timeout=5)
    except Exception:
        # Queries expire quickly; never log a raw URL or token.
        pass
