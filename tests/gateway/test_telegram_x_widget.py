import asyncio
import json
import sqlite3
import subprocess
import sys
import time
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from gateway.platforms import telegram_x_widget as widget

HTML = Path(widget.__file__).resolve().parents[2] / 'widgets' / 'x-command-deck.html'


class Runner:
    def __init__(self):
        self._running_agents = {}

    def handle(self):
        pass

    def _session_key_for_source(self, source):
        return f'tg:{source.chat_id}'


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_TELEGRAM_X_WIDGET', '1')
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '123')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(widget, '_usage_value', lambda billing: ['openai-codex · Weekly', 42, 'Thu 01:15'])
    runner = Runner()
    return SimpleNamespace(_bot=SimpleNamespace(_post=AsyncMock(return_value=True)),
                           _is_callback_user_authorized=lambda *a, **k: True,
                           _message_handler=runner.handle, runner=runner,
                           build_source=lambda **k: SimpleNamespace(**k),
                           handle_message=AsyncMock())


def query(**overrides):
    q = {'id': 'query', 'from': {'id': 123, 'is_bot': False, 'first_name': 'A'},
         'chat': {'id': 123, 'type': 'private'}, 'message_id': 1,
         'type': {'type': 'data'}, 'data': 'a', 'chat_instance': 'ci'}
    q.update(overrides)
    return SimpleNamespace(api_kwargs={'widget_query': q})


def answer(setup, **overrides):
    asyncio.run(widget.handle(setup, query(**overrides), None))
    method, args = setup._bot._post.await_args.args
    assert method == 'answerWidgetQuery' and len(args['data'].encode()) <= 1024
    return json.loads(args['data'])


def make_db(home, rows):
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('CREATE TABLE sessions (id TEXT, title TEXT, started_at REAL, ended_at REAL,'
                   ' billing_provider TEXT, billing_base_url TEXT)')
        db.execute('CREATE TABLE messages (session_id TEXT, timestamp REAL)')
        db.executemany('INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, NULL)', rows)


def test_ptb_unknown_update_survives():
    subprocess.run([sys.executable, '-c', "from telegram import Update; from telegram.ext import TypeHandler; u=Update.de_json({'update_id':1,'widget_query':{'id':'q','data':'a'}},None); assert u.api_kwargs['widget_query']['data']=='a'; assert TypeHandler(Update, lambda u,c: None).check_update(u)"], check=True)


@pytest.mark.parametrize('overrides', [
    {'chat': {'id': -123, 'type': 'group'}},
    {'chat': {'id': 999, 'type': 'private'}},
    {'from': {'id': 999}}, {'inline_message_id': 'inline'},
    {'widget_id': 'shared'}, {'message_id': None},
])
def test_denies_non_private_or_untrusted(setup, overrides):
    assert answer(setup, data='k:stop', **overrides) == {'t': 'Not authorized.'}
    setup.handle_message.assert_not_awaited()


@pytest.mark.parametrize('data', ['; rm -rf /', 'k:rm', 'k:', 'r:../x', 'l:9', 'l:٣', 'j:a b', 'x' * 65])
def test_unrecognized_data_never_runs_input(setup, data):
    assert answer(setup, data=data)['t'] in ('Unknown action.', 'No longer available.', 'Temporarily unavailable.')
    setup.handle_message.assert_not_awaited()


def test_fails_closed_without_explicit_numeric_allowlist(setup, monkeypatch):
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '*')
    assert answer(setup) == {'t': 'Not authorized.'}


def test_widget_denies_if_helper_raises(setup):
    def broken(*args, **kwargs):
        raise RuntimeError('unavailable')
    setup._is_callback_user_authorized = broken
    assert answer(setup) == {'t': 'Not authorized.'}


def test_send_and_size(setup):
    msg = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(id=123, type='private'), reply_text=AsyncMock())
    assert asyncio.run(widget.send(setup, msg))
    method, args = setup._bot._post.await_args.args
    assert method == 'sendWidget' and args['allow_queries'] is True and args['chat_id'] == 123
    raw = HTML.read_bytes()
    assert args['html'].encode() == raw and len(zlib.compress(raw, 9)) <= 4096


def test_opt_in_and_delivery_types(setup, monkeypatch):
    assert 'widget_query' in widget.allowed_updates(Update)
    monkeypatch.delenv('HERMES_TELEGRAM_X_WIDGET')
    assert 'widget_query' not in widget.allowed_updates(Update)
    asyncio.run(widget.handle(setup, query(), None))
    setup._bot._post.assert_not_awaited()


@pytest.mark.parametrize('result', [True, False, 'error', 'absent'])
def test_widget_gateway_authorization(setup, result):
    from gateway.platforms.telegram import TelegramAdapter

    class AuthRunner(Runner):
        def _is_user_authorized(self, source):
            assert source.user_id == '123' and source.chat_type == 'dm'
            if result == 'error':
                raise RuntimeError('authorization unavailable')
            return result

    adapter = object.__new__(TelegramAdapter)
    adapter._bot = setup._bot
    adapter._message_handler = None if result == 'absent' else AuthRunner().handle
    asyncio.run(widget.handle(adapter, query(data='zz'), None))
    response = json.loads(adapter._bot._post.await_args.args[1]['data'])
    assert (response == {'t': 'Not authorized.'}) == (result is not True)


def test_state_idle_with_sessions(setup, tmp_path):
    now = time.time()
    make_db(tmp_path, [('s1', 'Old', now - 900, now - 600), ('s2', 'Recent', now - 60, None),
                       ('s3', None, now, None), ('s4', 'Running', now - 30, None)])
    setup.runner._running_agents = {'other': SimpleNamespace(session_id='s4')}
    state = answer(setup)
    assert state['s'] == 'i' and state['u'] == ['openai-codex · Weekly', 42, 'Thu 01:15']
    assert [(r[0], r[2]) for r in state['l']] == [('s4', 'w'), ('s2', 'i'), ('s1', 'd')]


def test_state_working_and_your_turn(setup, tmp_path):
    from tools import approval
    make_db(tmp_path, [])
    setup.runner._running_agents = {'tg:123': SimpleNamespace(session_id='x')}
    assert answer(setup)['s'] == 'w'
    entry = approval._ApprovalEntry({'command': 'rm -rf build  ' + 'é' * 500})
    approval._gateway_queues['tg:123'] = [entry, entry]
    try:
        state = answer(setup)
    finally:
        approval._gateway_queues.pop('tg:123', None)
    assert state['s'] == 't' and state['n'] == 2 and state['c'].startswith('rm -rf build é')


def test_state_unavailable_without_db(setup, tmp_path):
    assert answer(setup) == {'t': 'Temporarily unavailable.'}
    assert not (tmp_path / 'state.db').exists()


@pytest.mark.parametrize('data,command', [('k:stop', '/stop'), ('k:approve', '/approve'), ('k:deny', '/deny'),
                                          ('k:restart', '/restart'), ('k:fast', '/fast'), ('k:yolo', '/yolo')])
def test_actions_dispatch_whitelisted_commands(setup, data, command):
    assert answer(setup, data=data) == {'t': 'Sent ' + command}
    event = setup.handle_message.await_args.args[0]
    assert event.text == command and event.message_type.name == 'COMMAND'
    assert event.source.chat_id == '123' and event.source.user_id == '123' and event.source.chat_type == 'dm'


def test_resume_uses_title_from_db(setup, tmp_path):
    make_db(tmp_path, [('s1', 'Deck  redesign', 1, None), ('s2', None, 2, None)])
    assert answer(setup, data='r:s1') == {'t': 'Sent /resume'}
    assert setup.handle_message.await_args.args[0].text == '/resume Deck redesign'
    assert answer(setup, data='r:s2') == {'t': 'No longer available.'}


def test_launchers(setup, tmp_path):
    (tmp_path / 'xdeck.yaml').write_text('launchers:\n  - {label: Brief, prompt: "Give me\n\n my brief"}\n  - {label: Bad}\n')
    assert answer(setup, data='l:0') == {'t': 'Sent /background'}
    assert setup.handle_message.await_args.args[0].text == '/background Give me my brief'
    assert answer(setup, data='l:1') == {'t': 'No longer available.'}


def test_controls_and_run_job(setup, monkeypatch):
    import cron.jobs
    jobs = [{'id': f'job{i}', 'name': f'Job {i} ' + 'é' * 30, 'enabled': i % 3 != 0,
             'last_status': 'error' if i == 5 else 'ok', 'prompt': 'SECRET'} for i in range(40)]
    triggered = []
    monkeypatch.setattr(cron.jobs, 'load_jobs', lambda: jobs)
    monkeypatch.setattr(cron.jobs, 'trigger_job', lambda job_id: triggered.append(job_id) or {'id': job_id})
    controls = answer(setup, data='c')
    assert controls['j'][0] == ['job5', jobs[5]['name'][:22], 'e'] and 0 < len(controls['j']) < 40
    assert 'SECRET' not in json.dumps(controls) and controls['l'] == []
    assert answer(setup, data='j:job7')['t'].startswith('Queued Job 7')
    assert answer(setup, data='j:Job') == {'t': 'Job not found.'} and triggered == ['job7']


def test_usage_picks_weekly_window(monkeypatch):
    import agent.account_usage as usage
    from datetime import datetime, timezone
    snap = usage.AccountUsageSnapshot(provider='openai-codex', source='x', fetched_at=datetime.now(timezone.utc), windows=(
        usage.AccountUsageWindow('Session', 10.0), usage.AccountUsageWindow('Weekly', 41.6, datetime(2026, 11, 12, tzinfo=timezone.utc))))
    monkeypatch.setattr(usage, 'fetch_account_usage', lambda provider, base_url=None: snap)
    widget._refresh_usage(('openai-codex', None))
    assert widget._usage['value'][:2] == ['openai-codex · Weekly', 42]
