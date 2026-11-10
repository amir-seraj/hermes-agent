"""Opt-in, read-only native Telegram widget for the private command deck.

PTB 22.x preserves unrecognized Update fields in api_kwargs. This bridge uses
that compatibility path, not a second poller or private PTB deserialization.
"""
import asyncio
import json
import os
import re
import stat

import yaml
import sqlite3
import zlib
from datetime import datetime
from contextlib import closing
from pathlib import Path

from hermes_constants import get_hermes_home


def _status():
    import shutil
    total, used, _ = shutil.disk_usage(get_hermes_home())
    load = os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0
    return f'Gateway responding.\nSystem load (1 min): {load:.2f}\nProfile disk used: {used * 100 / total:.0f}%'


def _cron():
    path = get_hermes_home() / 'cron' / 'jobs.json'
    with path.open('rb') as f:
        raw = f.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('too large')
    jobs = json.loads(raw)['jobs']
    if not isinstance(jobs, list) or any(not isinstance(j, dict) for j in jobs):
        raise ValueError('invalid jobs')
    if any(type(j.get('enabled', True)) is not bool for j in jobs):
        raise ValueError('invalid enabled flag')
    enabled = sum(j.get('enabled', True) is True for j in jobs)
    completed = sum(j.get('enabled', True) is False and j.get('state') == 'completed' for j in jobs)
    paused = sum(j.get('enabled', True) is False and j.get('state') != 'completed' for j in jobs)
    failed = sum(j.get('last_status') in ('error', 'failed') for j in jobs)
    return f'Cron jobs: {len(jobs)}\nEnabled: {enabled} · paused: {paused} · completed: {completed}\nLast run failed: {failed}\nCounts only; scheduler health is not checked.'


def _activity():
    path = get_hermes_home() / 'state.db'
    # mode=ro never creates a database. No message text or session titles leave it.
    uri = path.resolve().as_uri() + '?mode=ro'
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with closing(sqlite3.connect(uri, uri=True, timeout=0.3)) as db:
        db.set_progress_handler(lambda: 1, 200000)
        sessions = db.execute('SELECT count(*) FROM sessions WHERE started_at >= ?', (start,)).fetchone()[0]
        messages = db.execute('SELECT count(*) FROM messages WHERE timestamp >= ?', (start,)).fetchone()[0]
    return f'Today (local time)\nSessions started: {sessions}\nMessages recorded: {messages}\nCounts across this profile.'


# Vault access is strictly read-only.
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


def _registry():
    try:
        registry = os.getenv('HERMES_X_WIDGET_PROJECTS')
        path = Path(registry).expanduser() if registry else get_hermes_home() / 'projects.yaml'
        obj = yaml.safe_load(_read(path))
        projects = obj['projects']
        if not isinstance(projects, dict) or len(projects) > 500:
            raise ValueError('invalid registry')
        if any(not isinstance(p, dict) or not isinstance(p.get('status'), str)
               or not p['status'].strip() for p in projects.values()):
            raise ValueError('invalid project entry')
        active = []
        for key, p in projects.items():
            status = ' '.join(p['status'].strip().lower().replace('_', ' ').replace('-', ' ').split())
            if status == 'active':
                active.append((str(p.get('name') or key), p.get('obsidian_path')))
            elif status not in {'archived', 'inactive', 'paused', 'completed', 'on hold'}:
                raise ValueError('unrecognized project status')
        return active
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        return None


def _vault():
    configured = os.getenv('HERMES_X_WIDGET_VAULT')
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        config = json.loads(_read(Path.home() / '.config/obsidian/obsidian.json', 65536))
        opened = [v['path'] for v in config['vaults'].values() if v.get('open') is True]
        return Path(opened[0]).resolve() if len(opened) == 1 else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _safe_text(value):
    return ' '.join(str(value).split())[:180]


def _projects():
    projects = _registry()
    if projects is None:
        return 'Project registry unavailable.'
    if not projects:
        return 'No active projects.'
    result = 'Active projects:'
    items = ['\n- ' + _safe_text(name) for name, _ in projects]
    if len((result + ''.join(items)).encode('utf-8')) <= 1024:
        return result + ''.join(items)
    for index, item in enumerate(items):
        marker = f'\n... {len(items) - index} more not shown.'
        next_marker = f'\n... {len(items) - index - 1} more not shown.'
        if len((result + item + next_marker).encode('utf-8')) > 1024:
            return result + marker
        result += item
    return result


def _project_dir(vault, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        return None
    path = (vault / relative).resolve()
    return path if path != vault and path.is_relative_to(vault) else None


def _collect(kind):
    projects = _registry()
    if projects is None:
        return 'Project registry unavailable.'
    if not projects:
        return 'No active projects.'
    vault = _vault()
    if vault is None or not vault.is_dir():
        return 'Project vault unavailable.'
    lines, missing = [], 0
    for name, relative in projects:
        try:
            directory = _project_dir(vault, relative)
            if directory is None:
                raise ValueError('invalid project path')
            if kind == 'next':
                text = _read(directory / 'Index.md')
                in_workstreams = False
                found_heading = False
                for line in text.splitlines():
                    heading = re.match(r'^##\s+(.+?)\s*$', line)
                    if heading:
                        in_workstreams = heading.group(1).lower() in ('workstreams', 'next actions', 'next steps')
                        found_heading = found_heading or in_workstreams
                    match = re.match(r'^\s*[-*]\s+\[ \]\s+(.+)', line)
                    if in_workstreams and match:
                        lines.append(f'{_safe_text(name)}: {_safe_text(match.group(1))}')
                        break
                if not found_heading:
                    missing += 1
            else:
                bugs = directory / 'Bugs'
                if not bugs.resolve().is_relative_to(directory) or not bugs.is_dir():
                    raise ValueError('missing bugs')
                entries = []
                with os.scandir(bugs) as scan:
                    for entry in scan:
                        if len(entries) >= 200:
                            raise ValueError('too many bugs')
                        entries.append(entry.name)
                for entry in sorted(entries):
                    if not entry.endswith('.md'):
                        continue
                    try:
                        text = _read(bugs / entry, 65536)
                        parts = text.split('---', 2)
                        if len(parts) < 3 or parts[0].strip():
                            raise ValueError('invalid frontmatter')
                        meta = yaml.safe_load(parts[1])
                        if not isinstance(meta, dict) or not isinstance(meta.get('status'), str):
                            raise ValueError('invalid status')
                        status = meta['status'].strip().lower().replace('_', ' ').replace('-', ' ')
                        status = ' '.join(status.split())
                        if status in {'open', 'in progress', 'reopened', 'new'}:
                            title = re.search(r'^#\s+(.+)$', parts[2], re.MULTILINE)
                            lines.append(f'{_safe_text(name)}: {_safe_text(title.group(1) if title else entry[:-3])}')
                        elif status not in {'closed', 'resolved', 'fixed', 'done', 'duplicate', 'wontfix', "won't fix", 'wont fix'}:
                            missing += 1
                    except (OSError, ValueError, TypeError, yaml.YAMLError):
                        missing += 1
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            missing += 1
    header = 'Next workstreams:' if kind == 'next' else 'Open bugs:'
    empty = 'No next workstreams found.' if kind == 'next' else 'No open bugs found.'
    result = header if lines else (empty if not missing else 'No results found in available sources.')
    if missing:
        result += f'\nSources unavailable or unclassified: {missing}.'
    for index, line in enumerate(lines):
        item = '\n- ' + line
        # Reserve space for an explicit truncation marker.
        if len((result + item).encode('utf-8')) > 980:
            result += f'\n... {len(lines) - index} more not shown.'
            break
        result += item
    return _bounded(result)


def _next():
    return _collect('next')


def _bugs():
    return _collect('bugs')


_ACTIONS = {'x:status': _status, 'x:cron': _cron, 'x:activity': _activity,
            'x:projects': _projects, 'x:next': _next, 'x:bugs': _bugs,
            'x:help': lambda: 'Read-only deck. Counts are profile-local. No shell commands or changes.'}

def _bounded(text):
    # Telegram answerWidgetQuery data is at most 1024 UTF-8 bytes.
    return text.encode('utf-8')[:1024].decode('utf-8', 'ignore')


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
        response = 'Not authorized.'
    elif not isinstance(data, str) or data not in _ACTIONS:
        response = 'Unknown action.'
    else:
        try:
            response = await asyncio.wait_for(asyncio.to_thread(_ACTIONS[data]), timeout=2)
        except Exception:
            response = 'This information is temporarily unavailable.'
    try:
        await asyncio.wait_for(adapter._bot._post('answerWidgetQuery', {'widget_query_id': qid, 'data': _bounded(response)}), timeout=5)
    except Exception:
        # Queries expire quickly; never log a raw URL or token.
        pass
