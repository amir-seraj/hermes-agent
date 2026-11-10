# Hermes deck (experimental)

A live Telegram widget for Hermes, built on Bot API 10.4 widgets (`sendWidget`,
`widget_query`, `answerWidgetQuery`). Send `/xdeck` in a private chat with the bot.

**Agent tab**: refreshes every ~6 s while the widget is on screen.
- `WORKING`: Hermes is running in this chat. **Stop** sends `/stop`.
- `YOUR TURN`: a dangerous command is waiting for approval. Shows the command; **Approve** / **Deny** send `/approve` / `/deny`.
- `IDLE`: nothing needs you.
- The three most recently active titled sessions (working / done / idle). Tap twice to `/resume` one.
- The provider's weekly usage window (Codex, Anthropic, or OpenRouter), refreshed every 5 minutes.

**Controls tab**
- Launchers: tap twice to run a saved prompt with `/background`; the reply lands in chat.
- Cron: jobs sorted with failures first; **Run** queues the job for the next scheduler tick (this re-enables a paused job, like `hermes cron run`).
- Ops: `/fast`, `/yolo`, `/restart` (yolo, restart, stop, run, resume and launchers need a second tap).

Every action except cron is sent through the gateway's normal command path as if you had
typed it, so its reply appears in the chat. The widget can only send the fixed action keys
listed above; session and job IDs are checked against the database before use.

## Setup

1. In the gateway's environment, set `HERMES_TELEGRAM_X_WIDGET=1` and
   `TELEGRAM_ALLOWED_USERS` to explicit numeric Telegram user IDs. Wildcards and
   usernames do not grant deck access, and the gateway's own authorization must also
   allow the user. Restart the gateway.
2. Optional launchers in `$HERMES_HOME/xdeck.yaml` (up to 6):

   ```yaml
   launchers:
     - label: Morning brief
       prompt: Give me my morning brief
     - label: Inbox
       prompt: Triage my inbox and list what needs a reply
   ```

3. `HERMES_X_WIDGET_HTML` can point at an alternate widget file. Treat it as trusted
   code; it must fit Telegram's 32 KiB raw / 4 KiB zlib-compressed limit.

## Limits

- Widgets have no keyboard, so anything that needs typing stays in normal chat.
- Actions always target the main private-chat session, not a DM topic thread.
- Uses python-telegram-bot's private `Bot._post` and `Update.api_kwargs`, because PTB has
  no widget types yet; a PTB upgrade can break this.
- Results (session titles, pending commands, job names) pass through Telegram.

Run the tests with `./scripts/run_tests.sh tests/gateway/test_telegram_x_widget.py`.
