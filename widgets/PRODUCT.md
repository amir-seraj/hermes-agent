# Product

## Register

product

## Users

One person: the owner of a self-hosted Hermes agent, using the deck inside their private Telegram chat with the bot, on a phone. They open it in passing, often mid-something-else: between meetings, on the couch, late at night after kicking off a long run. The jobs: know in one glance whether Hermes needs them, answer an approval, stop a run that went wrong, start a routine without typing, and see whether scheduled jobs are healthy.

## Product Purpose

A live control surface for Hermes that lives in a single Telegram message. It replaces typing slash commands for the things done most often. Success: the state is readable in under a second, the one action that matters right now is obvious and reachable with a thumb, and nothing about the surface feels like a web page pasted into a chat.

## Brand Personality

Calm, precise, native. It should feel like a piece of the phone (an iOS Live Activity, a watch complication) built with the care of Linear or Raycast: quiet until something needs attention, then unambiguous. Confidence through restraint, not decoration. Friendly in tone, never cute.

## Anti-references

- Retro or novelty aesthetics: dot-matrix and LED lettering, pixel mascots, terminal cosplay, CRT effects.
- Wireframe look: plain outlined boxes and buttons that read as unfinished.
- Flat hierarchy: every row, label and button at the same weight.
- Web-page-in-a-chat: anything that ignores Telegram's theme, fights the bubble, or looks foreign next to native messages.
- SaaS dashboard tropes: hero metrics, gradient accents, card grids.

## Design Principles

1. **One glance, one answer.** The top of the deck says whether Hermes needs you, in words and color, before anything else.
2. **The right action is the obvious action.** At most one primary action per state, thumb-reachable; everything else is quieter.
3. **Belong to the host.** Follow Telegram's theme and platform conventions so the deck feels native in light and dark chats.
4. **Quiet by default, loud on need.** Idle is calm and low-contrast; approvals and failures earn color and emphasis.
5. **Destructive taps are deliberate.** Stopping, restarting and auto-approval need a clear second step; approvals stay one tap.

## Accessibility & Inclusion

WCAG AA contrast in both themes. Touch targets at least 44px. Color is never the only signal: states also carry words or shape. Respect reduced motion. Text must survive long session titles and long commands without breaking layout.
