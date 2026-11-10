import asyncio
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram.ext import TypeHandler
from gateway.platforms import telegram_x_widget as widget


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('HERMES_TELEGRAM_X_WIDGET', '1')
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '123')
    return SimpleNamespace(_bot=SimpleNamespace(_post=AsyncMock(return_value=True)),
                           _is_callback_user_authorized=lambda *a, **k: True)


def query(**overrides):
    q = {'id': 'query', 'from': {'id': 123, 'is_bot': False, 'first_name': 'A'},
         'chat': {'id': 123, 'type': 'private'}, 'message_id': 1,
         'type': {'type': 'data'}, 'data': 'x:status', 'chat_instance': 'ci'}
    q.update(overrides)
    return SimpleNamespace(api_kwargs={'widget_query': q})


def test_ptb_unknown_update_survives_and_handler_accepts(setup):
    u = query()
    assert u.api_kwargs['widget_query']['data'] == 'x:status'
    subprocess.run([sys.executable, '-c', "from telegram import Update; from telegram.ext import TypeHandler; u=Update.de_json({'update_id':1,'widget_query':{'id':'q','data':'x:status'}},None); assert u.api_kwargs['widget_query']['data']=='x:status'; assert TypeHandler(Update, lambda u,c: None).check_update(u)"], check=True)
    asyncio.run(widget.handle(setup, u, None))
    assert setup._bot._post.await_args.args[0] == 'answerWidgetQuery'
    assert 'Gateway responding.' in setup._bot._post.await_args.args[1]['data']


@pytest.mark.parametrize('overrides', [
    {'chat': {'id': -123, 'type': 'group'}},
    {'chat': {'id': 999, 'type': 'private'}},
    {'from': {'id': 999}}, {'inline_message_id': 'inline'},
    {'widget_id': 'shared'}, {'message_id': None},
])
def test_denies_non_private_or_untrusted(setup, overrides):
    asyncio.run(widget.handle(setup, query(**overrides), None))
    assert setup._bot._post.await_args.args[1]['data'] == 'Not authorized.'


def test_unrecognized_data_never_runs_input(setup):
    asyncio.run(widget.handle(setup, query(data='; rm -rf /'), None))
    assert setup._bot._post.await_args.args[1]['data'] == 'Unknown action.'


def test_fails_closed_without_explicit_numeric_allowlist(setup, monkeypatch):
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '*')
    asyncio.run(widget.handle(setup, query(), None))
    assert setup._bot._post.await_args.args[1]['data'] == 'Not authorized.'


def test_send_and_size(setup):
    msg = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(id=123, type='private'), reply_text=AsyncMock())
    assert asyncio.run(widget.send(setup, msg))
    method, args = setup._bot._post.await_args.args
    assert method == 'sendWidget' and args['allow_queries'] is True and args['chat_id'] == 123
    assert 'Command deck' in args['html']


def test_opt_in_and_delivery_types(setup, monkeypatch):
    assert 'widget_query' in widget.allowed_updates(Update)
    monkeypatch.delenv('HERMES_TELEGRAM_X_WIDGET')
    assert 'widget_query' not in widget.allowed_updates(Update)
    asyncio.run(widget.handle(setup, query(), None))
    setup._bot._post.assert_not_awaited()


def test_profile_data_counts_and_secrets(tmp_path, monkeypatch):
    import json, sqlite3, time
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron/jobs.json").write_text(json.dumps({"jobs": [{"enabled": True, "last_status": "error", "prompt": "SECRET"}, {"enabled": False}]}))
    assert "Enabled: 1" in widget._cron()
    assert "SECRET" not in widget._cron()
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute("CREATE TABLE sessions (started_at REAL)")
        db.execute("CREATE TABLE messages (timestamp REAL)")
        db.execute("INSERT INTO sessions VALUES (?)", (time.time(),))
        db.execute("INSERT INTO messages VALUES (?)", (time.time(),))
    assert "Sessions started: 1" in widget._activity()
    assert "Messages recorded: 1" in widget._activity()


def test_missing_data_and_bounded_response(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    asyncio.run(widget.handle(setup, query(data="x:cron"), None))
    assert setup._bot._post.await_args.args[1]["data"] == "This information is temporarily unavailable."
    assert len(widget._bounded("é" * 1000).encode()) <= 1024
    assert not (tmp_path / "state.db").exists()


def test_project_sources(setup, tmp_path, monkeypatch):
    monkeypatch.setattr(widget.Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault = tmp_path / "vault"
    project = vault / "10 Projects" / "alpha"
    (project / "Bugs").mkdir(parents=True)
    monkeypatch.setenv("HERMES_X_WIDGET_VAULT", str(vault))
    (home / "projects.yaml").write_text("projects:\n  alpha:\n    name: Alpha\n    status: active\n    obsidian_path: 10 Projects/alpha\n  old:\n    name: Old\n    status: archived\n")
    (project / "Index.md").write_text("## Workstreams\n- [ ] Ship it\n- [x] Done\n## Log\n- [ ] Historical\n")
    (project / "Bugs" / "one.md").write_text("---\nstatus: open\n---\n# Broken thing\n")
    (project / "Bugs" / "done.md").write_text("---\nstatus: closed\n---\n# Fixed\n")
    for action, expected in [("x:projects", "Alpha"), ("x:next", "Ship it"), ("x:bugs", "Broken thing")]:
        asyncio.run(widget.handle(setup, query(data=action), None))
        response = setup._bot._post.await_args.args[1]["data"]
        assert expected in response
        assert "Old" not in response and "Fixed" not in response and "Historical" not in response
        assert len(response.encode()) <= 1024
    (project / "Bugs" / "one.md").unlink()
    assert "No open bugs" in widget._bugs()
    (project / "Index.md").unlink()
    assert "unavailable" in widget._next().lower()


def test_missing_registry_and_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(widget.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("HERMES_X_WIDGET_VAULT", str(tmp_path / "vault"))
    assert "unavailable" in widget._projects().lower()
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes/projects.yaml").write_text("projects:\n  bad:\n    name: Bad\n    status: active\n    obsidian_path: ../outside\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Index.md").write_text("## Workstreams\n- [ ] SECRET\n")
    assert "SECRET" not in widget._next()
    assert "unavailable" in widget._next().lower()


def test_vault_discovery_and_symlink_escape(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(widget.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.delenv("HERMES_X_WIDGET_VAULT", raising=False)
    (tmp_path / ".hermes").mkdir()
    config = tmp_path / ".config/obsidian"
    config.mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    (config / "obsidian.json").write_text(json.dumps({"vaults": {"v": {"path": str(vault), "open": True}}}))
    (tmp_path / ".hermes/projects.yaml").write_text("projects:\n  a:\n    status: active\n    obsidian_path: link\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Index.md").write_text("## Workstreams\n- [ ] SECRET\n")
    (vault / "link").symlink_to(outside, target_is_directory=True)
    assert widget._vault() == vault
    assert "SECRET" not in widget._next()
    assert "unavailable" in widget._next().lower()


def test_empty_and_unclassified_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(widget.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("HERMES_X_WIDGET_VAULT", str(tmp_path / "vault"))
    (tmp_path / ".hermes").mkdir()
    registry = tmp_path / ".hermes/projects.yaml"
    registry.write_text("projects: {}\n")
    assert widget._projects() == "No active projects."
    registry.write_text("projects:\n  a:\n    status: active\n    obsidian_path: a\n")
    bugs = tmp_path / "vault/a/Bugs"
    bugs.mkdir(parents=True)
    (bugs / "unknown.md").write_text("# Unknown status\n")
    assert "unclassified" in widget._bugs()
    assert "Unknown status" not in widget._bugs()


@pytest.mark.parametrize("result", [True, False, "error", "absent", "missing_method", "noncallable"])
def test_widget_gateway_authorization(setup, result):
    from gateway.platforms.telegram import TelegramAdapter

    class Runner:
        def handle(self):
            pass

        def _is_user_authorized(self, source):
            assert source.user_id == "123" and source.chat_type == "dm"
            if result == "error":
                raise RuntimeError("authorization unavailable")
            return result

    adapter = object.__new__(TelegramAdapter)
    adapter._bot = setup._bot
    if result == "absent":
        adapter._message_handler = None
    elif result in ("missing_method", "noncallable"):
        class NoAuthorizer:
            def handle(self):
                pass

        runner = NoAuthorizer()
        if result == "noncallable":
            runner._is_user_authorized = True
        adapter._message_handler = runner.handle
    else:
        adapter._message_handler = Runner().handle
    asyncio.run(widget.handle(adapter, query(), None))
    response = adapter._bot._post.await_args.args[1]["data"]
    assert (response == "Not authorized.") == (result is not True)
    if result is not True:
        msg = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(id=123, type="private"), reply_text=AsyncMock())
        adapter._bot._post.reset_mock()
        assert asyncio.run(widget.send(adapter, msg))
        adapter._bot._post.assert_not_awaited()


def test_projects_truncation_is_explicit_and_utf8_bounded(monkeypatch):
    monkeypatch.setattr(widget, "_registry", lambda: [(f"Project {i} " + "é" * 100, None) for i in range(30)])
    result = widget._projects()
    shown = result.count("\n- ")
    assert shown < 30
    assert result.endswith(f"... {30 - shown} more not shown.")
    assert len(result.encode('utf-8')) <= 1024


def test_projects_complete_list_has_no_truncation_marker(monkeypatch):
    monkeypatch.setattr(widget, "_registry", lambda: [("Alpha", None), ("Beta", None)])
    assert widget._projects() == "Active projects:\n- Alpha\n- Beta"


def test_widget_denies_if_helper_raises(setup):
    def broken(*args, **kwargs):
        raise RuntimeError("unavailable")
    setup._is_callback_user_authorized = broken
    asyncio.run(widget.handle(setup, query(), None))
    assert setup._bot._post.await_args.args[1]["data"] == "Not authorized."


def test_bug_status_classification(tmp_path, monkeypatch):
    monkeypatch.setattr(widget, "_registry", lambda: [("Alpha", "a")])
    monkeypatch.setattr(widget, "_vault", lambda: tmp_path)
    bugs = tmp_path / "a/Bugs"
    bugs.mkdir(parents=True)
    statuses = [" Open ", "IN_PROGRESS", "reopened", "new", "closed", "Resolved", "fixed", "done", "duplicate", "wontfix", "mystery", ""]
    for index, status in enumerate(statuses):
        (bugs / f"{index}.md").write_text(f'---\nstatus: "{status}"\n---\n# Bug {index}\n')
    result = widget._bugs()
    for index in range(4):
        assert f"Alpha: Bug {index}" in result
    for index in range(4, 12):
        assert f"Alpha: Bug {index}" not in result
    assert "unclassified: 2" in result


def test_cron_distinguishes_completed_from_paused(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'cron').mkdir()
    jobs = [{'enabled': True}, {}, {'enabled': False, 'state': 'paused'},
            {'enabled': False}, {'enabled': False, 'state': 'completed', 'last_status': 'error'}]
    path = tmp_path / 'cron/jobs.json'
    path.write_text(json.dumps({'jobs': jobs}))
    result = widget._cron()
    assert 'Cron jobs: 5' in result
    assert 'Enabled: 2 · paused: 2 · completed: 1' in result
    assert 'Last run failed: 1' in result
    jobs.append({'enabled': 'false'})
    path.write_text(json.dumps({'jobs': jobs}))
    with pytest.raises(ValueError):
        widget._cron()


@pytest.mark.parametrize('entry', ['null', 'bad', '{}', '{status: null}', '{status: ""}'])
def test_malformed_registry_entries_are_not_silently_ignored(tmp_path, monkeypatch, entry):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'projects.yaml').write_text(
        'projects:\n  good: {status: active, obsidian_path: good}\n  bad: ' + entry + '\n')
    for action in (widget._projects, widget._next, widget._bugs):
        assert action() == 'Project registry unavailable.'


def test_registry_normalizes_known_statuses_and_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    registry = tmp_path / 'projects.yaml'
    registry.write_text('projects:\n  a: {status: " Active "}\n  b: {status: " ARCHIVED "}\n'
                        '  c: {status: "ON_HOLD"}\n  d: {status: "Completed"}\n')
    assert widget._projects() == 'Active projects:\n- a'
    registry.write_text('projects:\n  a: {status: " Paused "}\n')
    assert widget._projects() == 'No active projects.'
    for entries in ('  a: {status: mystery}\n',
                    '  a: {status: active}\n  b: {status: mystery}\n'):
        registry.write_text('projects:\n' + entries)
        for action in (widget._projects, widget._next, widget._bugs):
            assert action() == 'Project registry unavailable.'


def test_bad_bug_files_do_not_hide_other_bugs(tmp_path, monkeypatch):
    monkeypatch.setattr(widget, '_registry', lambda: [('Alpha', 'a')])
    monkeypatch.setattr(widget, '_vault', lambda: tmp_path)
    bugs = tmp_path / 'a/Bugs'
    bugs.mkdir(parents=True)
    (bugs / '00-permission.md').write_text('unreadable')
    (bugs / '01-invalid-yaml.md').write_text('---\nstatus: [broken\n---\n')
    (bugs / '02-invalid-utf8.md').write_bytes(b'\xff')
    (bugs / '03-too-large.md').write_bytes(b'x' * 65537)
    (bugs / '04-symlink.md').symlink_to(bugs / '05-valid.md')
    (bugs / '05-valid.md').write_text('---\nstatus: open\n---\n# Visible bug\n')
    original_read = widget._read

    def read_with_denial(path, *args):
        if path.name == '00-permission.md':
            raise PermissionError('denied')
        return original_read(path, *args)

    monkeypatch.setattr(widget, '_read', read_with_denial)
    result = widget._bugs()
    assert 'Alpha: Visible bug' in result
    assert 'unclassified: 5' in result
    assert 'No open bugs' not in result


def test_next_missing_heading_is_unclassified_but_empty_heading_is_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(widget, '_registry', lambda: [('Alpha', 'a'), ('Beta', 'b')])
    monkeypatch.setattr(widget, '_vault', lambda: tmp_path)
    for name in ('a', 'b'):
        (tmp_path / name).mkdir()
    (tmp_path / 'a/Index.md').write_text('## Workstreams\n- [ ] Ship\n')
    (tmp_path / 'b/Index.md').write_text('## Other\n- [ ] Not a next action\n')
    result = widget._next()
    assert 'Alpha: Ship' in result
    assert 'unclassified: 1' in result
    assert 'Not a next action' not in result
    (tmp_path / 'a/Index.md').write_text('## Next actions\n- [x] Done\n')
    assert 'unclassified: 1' in widget._next()
    (tmp_path / 'b/Index.md').write_text('## Next steps\n- [x] Done\n')
    assert widget._next() == 'No next workstreams found.'
