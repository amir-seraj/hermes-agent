# X Command Deck (experimental)

An opt-in, read-only Telegram command deck. This uses experimental `sendWidget`,
`answerWidgetQuery`, and `widget_query` API extensions and the
`window.TelegramWidget` client API. Availability is not guaranteed in standard
Telegram Bot API servers or clients; this feature requires a compatible server
and client. It uses python-telegram-bot's private `_post` method and relies on
unknown update fields surviving in `Update.api_kwargs` (tested with the installed
PTB version). Upgrades may break compatibility. No second poller is started.

## Setup

1. Set up and authorize the Hermes Telegram gateway as usual. In its environment,
   set `HERMES_TELEGRAM_X_WIDGET=1` and `TELEGRAM_ALLOWED_USERS` to a comma-separated
   list of explicit numeric Telegram user IDs, e.g. `123456789`. Wildcards and
   usernames do not grant deck access. The gateway's own authorization must also
   allow the user. Restart the gateway after changing its environment.
2. Open a private conversation with the bot as an allowed user and send
   `/xdeck`. Group, inline, and shared-widget queries are rejected. Disabling the
   opt-in and restarting turns the feature off.
3. The bundled `widgets/x-command-deck.html` is used by default. An optional
   `HERMES_X_WIDGET_HTML` selects an alternate local file. Treat that file as
   trusted code: the server sends its contents to Telegram. It must fit within
   32 KiB raw and 4 KiB zlib-compressed.

## Optional projects and vault

The default project registry is `projects.yaml` under the active Hermes home
(`HERMES_HOME`/profile); `HERMES_X_WIDGET_PROJECTS` overrides it. Example:

```yaml
projects:
  sample:
    name: Sample project
    status: active
    obsidian_path: Projects/Sample
```

Set `HERMES_X_WIDGET_VAULT` to the vault directory. If unset, the deck discovers
it only when exactly one vault is marked open in `~/.config/obsidian/obsidian.json`.
For each active project, `Index.md` provides the first unchecked item under a
`## Workstreams`, `## Next actions`, or `## Next steps` heading. Markdown files
in `Bugs/` with YAML frontmatter `status: open` provide bug titles. Missing or
invalid sources are reported, not treated as conclusive absence. Project paths
must be relative and resolve inside the vault. Other deck cards read profile
cron counts, today's session/message counts, and host load/disk usage. Missing
files produce unavailable responses. No project data is required for other cards.
Registry statuses are case-insensitive and whitespace-normalized: `active` is
active; `archived`, `inactive`, `paused`, `completed`, and `on hold` are
inactive (`on-hold` and `on_hold` are also accepted). Malformed entries or
unrecognized statuses make the registry unavailable. An index without a recognized heading
is unclassified, whereas a recognized heading with no unchecked items is empty.
Cron counts distinguish disabled completed jobs from paused jobs; legacy disabled
jobs without a completed state are counted as paused.

## Privacy and limitations

The bot sends the HTML and requested results through Telegram. Project names,
next-action text, and bug titles may contain sensitive information; aggregate
activity, jobs, and host metrics can also reveal private information. Anyone
with access to an authorized Telegram account or its client can see results.
Do not enable this for data you cannot share with Telegram or those users.
Project outputs are truncated; the status card reports disk use for the
filesystem containing the Hermes home, not the size of the profile.

The feature runs no shell commands and provides no write actions. Database
access uses SQLite read-only mode; bounded reads and path checks reduce risk,
but vault checks are not a security sandbox against a local user concurrently
replacing directories/symlinks. Only use trusted local registry, vault and HTML
files. Timeouts do not stop already-running background read threads. Security
assumes authentic updates from Telegram and a secure bot token/webhook; no
live compatibility or end-to-end security audit is implied by the unit tests.

Run the focused tests with `./scripts/run_tests.sh tests/gateway/test_telegram_x_widget.py`.
