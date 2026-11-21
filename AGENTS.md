# Rota Planner — guide for AI agents (and new developers)

Read this before changing anything. `ARCHITECTURE.md` has the module map and
the reasons behind the design; `README.md` is the user's guide.

## What this is

A local program that helps one coordinator prepare the monthly service rota
of a residential centre: ~90 people, ~45 tasks in 9 areas, availability
collected with a form, events and retreats, printouts to pin up. It replaces
a Google Sheet. The user is not a programmer.

## Rules that are not negotiable

1. **Personal data never leaves this computer, except where the coordinator
   chose.** No CDN, no fonts or scripts from outside, no telemetry, and no
   network call other than the three named below. The server listens on
   127.0.0.1 and answers this computer only (`server.is_loopback`, checked
   first on every request): other computers share the data through sync,
   never by connecting to the server. Do not add a way in from the network
   (no password screen, no other addresses). The only data that leaves
   the data folder is the **sealed** sync file (and the pen note), put in the
   place the coordinator chose: a shared folder, or a WebDAV server. There
   are three outgoing connections, all optional. Two go to a server the
   coordinator set up, encrypted with the certificate checked (`tls.py`):
   the e-mail (`mail.py`), only when they press Test or Send (names and
   shifts then pass through that provider); and the WebDAV server
   (`places.py`), talked to in the background by the courier (`sync.py`),
   which only ever receives sealed files. The third asks the program's own
   repository on GitHub whether a newer version exists (`updates.py`: a
   `git fetch`, once a day unless turned off in Settings, or on "Check now");
   it sends nothing about the data, and nothing is ever installed without
   the coordinator's yes. Do not paste real names, phone numbers,
   e-mail addresses or comments into prompts, commits, tests, screenshots or
   issue text.
2. **Never read the real data to develop or test.** `data/` (git-ignored),
   backup copies and any spreadsheet in the user's Downloads folder are real
   people. Use `--demo` data (an invented centre and invented people) or the
   tiny set in `tests/helpers.py`. If you must check an import of a real file,
   print totals only, never rows.
3. **Python standard library only**, Python ≥ 3.9. There is no pip, no venv,
   no node on the user's machine. No build step, no framework, no bundler.
   (3.9 means: no `match`, no `X | Y` unions at runtime, no
   `zip(strict=)`, no `ignore_cleanup_errors`, no `datetime.UTC`.)
4. **All rules and numbers are computed in Python.** The JavaScript only
   displays what the server sends. Never re-implement a rule in JS.
5. **Text is never HTML.** In the interface build DOM with `h()` (`web/js/dom.js`),
   which inserts text nodes; never `innerHTML`, `insertAdjacentHTML`,
   `document.write`, inline `style="…"` or inline scripts (the
   Content-Security-Policy forbids them; set styles through CSSOM
   `el.style.setProperty`). In printed documents use `el()` from
   `exporters/html.py`, which escapes everything.
6. **Notes are for practical matters only, never health.** A note on a
   placement (`Assignment.note`, max 60 characters) is printed with the name;
   a person's note (`Person.note`) is seen by everyone who uses the program.
   The screen says so next to each. Do not add any other free-text field
   about a person without the same warning.
7. **Availability comments are sensitive** (they can mention health). They are
   deleted when the month is over, on request, or (offered, ticked by
   default) when the rota is published for the residents, from the month
   file, its backups and the undo history
   (`App.forget_comments`). Never copy them anywhere else (exports, logs,
   printouts, versions), and never into the sync file once the month is over.
8. **E-mail addresses are never stored.** Only a one-way fingerprint
   (`Person.email_fp`, see `emails.py`) is kept, to recognise a reply. An
   address read from an import is used to compute the fingerprint, then
   dropped: it is never saved, logged, sent to the screen or put in an
   export. For e-mailing schedules the coordinator gives the list each time:
   the addresses live in memory for the sending only, are shown back masked
   (`an•••@example.org`), and never cross the network (this computer only).
9. **Passwords and keys are never sent to the screen** and never leave the
   data folder: the sync key and the WebDAV app password in `sync.json`, the mail account's app password in
   `mail.json` (these must stay readable to log in: private files of this
   computer, never exported or synchronised). The WebDAV password is sent
   only to the server it belongs to: https only, no redirect followed.

## Run, test, try

```bash
python3 run.py --demo                 # demo data in ./data-demo, opens the browser
python3 run.py --demo --reset-demo    # rebuild the demo data first
python3 run.py --data /some/folder --port 8790 --no-browser   # isolated copy for trials
python3 -m unittest discover -s tests -t .                    # all tests (must stay green)
python3 -W error -m unittest discover -s tests -t .           # also fail on warnings
```

The program prints its address with a session token (`?t=…`); the token is
kept in `<data folder>/.session-token`. To try a change in a browser, start
an isolated copy on another port with a throwaway `--data` folder, never the
user's `data/`. Python changes need a restart; JS and CSS only a page reload.

When you stop a server you started, kill **that PID only**. Never `pkill -f`
or kill by pattern: the user's own browser and tools match more patterns
than you expect, and your own shell command line usually contains the pattern.

## Where things are

```
rotaplanner/
  model.py          records (dataclasses) + JSON round trip; SCHEMA_VERSION
  store.py          the data folder: atomic writes, backups, MIGRATIONS
  demand.py         how many people each task needs each day
  schedule.py       a month's assignments, indexed; a task "as it is on a day"
  eligibility.py    RULES: who may do what, and how good a choice they are
  checks.py         state of each assignment (ok / warn / conflict / forced / guest)
  months.py         assign, clear, regular week, one-day changes
  month_setup.py    new month, events (also over two months), closures, event tasks
  availability.py   reading the form's replies (e-mail fingerprint, then name); comments and their deletion
  emails.py         e-mail addresses as one-way fingerprints only
  sync.py           several computers: one sealed file each, version vectors, the courier, the pen
  places.py         where the computers meet: a shared folder or a WebDAV server (list, read, write, delete)
  tls.py            encrypted connections with the certificate checked (mail, WebDAV)
  mail.py           optional: e-mailing each person their schedule
  updates.py        is there a newer version (git fetch), and installing it on a yes
  desktop.py        the program on this computer: uninstalling from the window, the old Update icon
  editing.py        set-up changes: people, tasks, event types, print templates…
  solver.py         prefill, rebalance, shortages, ideas
  report.py tips.py view.py   what the Check screen, the Assistant and the grid show
  transfer.py       all data in one file (move to another computer)
  exporters/        printed documents, versions, personal rotas, .ics, .xlsx, PDF
  people_import.py xlsx_read.py   a list of people from a spreadsheet; reading .xlsx safely
  app.py            every action of the program (state in memory, undo, saving)
  history.py        each month's undo and redo: named steps, packed, in memory only
  server.py         HTTP: static files, JSON API, print pages
  demo.py           demonstration data
  instance.py       one Rota Planner per data folder: the lock, the wake-up door
  service.py        the web server, started and stopped on demand
  window.py paint.py   the control window (Tk) and its smooth shapes and icon
  web/              index.html, css/, js/ (see below)
tests/              unittest, one file per area; helpers.py is the shared tiny data set
install-rota-planner.sh   Mac/Linux installer and updater (one file, outside the program)
```

Front end (`rotaplanner/web/js`): `main.js` (shell, routes, shortcuts),
`state.js` (the single state object; `setUi` = remembered preferences,
`setSession` = this visit, `setMonth`), `actions.js` (every change to the
rota goes through here: toast with Undo, "assign anyway"; undo, redo and
going back, `undoTarget`, `changed`, `afterRestore`), `history.js` (Recent
changes: the list of a month's steps), `api.js`,
`components.js` (inputs, dialog, drawer), `build/` (the rota grid and its
side panel), `pages/` (one module per screen), `eventtask.js`,
`schedules.js`, `mailer.js` / `syncsetup.js` / `backup.js` / `updates.js` (the
e-mail, several computers, backup-copy and version lines of Settings, and
their windows), `sync.js` (other computers: the cloud in the top bar, the
pen, the banners, choosing between versions), `ui.js` (toast, menu, copy,
`settingsLink`), `help.js` +
`help-content.js`, `print-page.js` (runs on the print pages only).

## How a change usually goes

1. Domain function in the right module (pure, no I/O), with a test.
2. A method on `App` (`app.py`): changes to a month go through
   `self._change(month_id, fn, label=…)` so they are undoable, saved, and
   rolled back if `fn` raises. Every step has a name in plain words (`label`:
   a string, or a function called after the change; never a comment, a note
   or an address); `tests/test_undo.py` fails on a step still called
   "Change". Nothing is recorded or saved when the month did not really
   change. Catalog/settings changes save with `_save_setup`.
3. A route in `server.py` (`MONTH_ACTIONS`, `SETUP_ACTIONS` or `build_routes`).
4. The screen: call it from `actions.js` (rota) or the page module, then
   re-render from state. Reuse `components.js`; colours, sizes and spacing
   come from the tokens at the top of `web/css/app.css` (light and dark).
5. Words for the user: plain English, no jargon; add or adjust the help in
   `web/js/help-content.js` when a screen or a concept changes.

Recipes:

- **A new rule** (who may do what): a function in `eligibility.RULES`
  returning a `Finding` (block / warn / note) or `None`; test in
  `tests/test_rules.py`. The picker, the checks and the solver all use it.
- **A new field on a record**: add it to the dataclass with a default. If
  old files must be transformed (not just defaulted), bump
  `model.SCHEMA_VERSION` and add a step to `store.MIGRATIONS` (keyed by the
  version it upgrades *from*), with a test like `OlderFilesTest`.
- **A new printed document**: a function in `exporters/documents.py`
  returning a `Document`, a `kind` in `App._document_body`, styles in
  `web/css/print.css`, a card in `web/js/pages/publish.js`.
- **A new screen**: a module in `web/js/pages/` exporting `mountX(container)`
  that returns `{ render }`; register it in `SCREENS` in `main.js`; add its
  entry to `SCREENS` in `help-content.js`.

## Vocabulary (use these words, in code comments and on screen)

| Word | Meaning |
|---|---|
| area | Kitchen, Dining room, Cafe… (a group of tasks, with a colour) |
| task | something to do at a time of day; needs N people on given days |
| shift | one task on one day; it has one or more *places* (positions) |
| slot key | `"<task id>|<date>|<position>"`, the identity of one place |
| regular | someone who does the same task on the same weekday |
| event / event type | a retreat, festival…; its type holds the tasks such events need |
| event task | a task of one event (`Task.event_id`), needed on some of its days |
| **rota to fill** | the Build screen: always shows every task, event tasks included |
| **printed main rota** | the month grid on paper; an event's tasks are on it only if the event says so (`Month.on_main_rota(task)`) |
| event rota | one event day by day; always shows all its tasks |
| print template | a saved way to print the main rota: paper, areas, options |
| version | the rota as given to someone (`Version.audience`): the manager, to check, or the residents, published; later changes are marked ● |
| phase | where a month stands: draft, with the manager, corrections, published, adjusted (`versions.phase`) |
| highlighted | an open shift marked "to fill when possible"; printed "Sign up" |
| assigned anyway | a rule knowingly overridden, with an optional reason |
| other computers | other copies of the data, kept in step through a **meeting place** |
| meeting place | where each computer puts its sealed file: a WebDAV server or a shared folder |
| pairing code | the code typed on every computer; the sync file's key comes from it |
| unit | what sync keeps whole: People, Tasks & areas, Settings, Saved task templates, each month |
| in sync / to get / sent / both changed | the state of a unit among the computers |
| the pen | the right to change the data: one computer at a time holds it; the others look |
| ask to edit / hand over | another computer asks for the pen; the holder sends its changes, then lets go by itself |
| courier | the background thread that sends this computer's file and fetches the others' |
| backup copy | everything in one file (`transfer.py`), saved and restored from Settings; not the same as several computers |

Never blur "rota to fill" and "printed main rota": that confusion already
cost a bug report.

## Things that are easy to get wrong

- **A month owns its tasks.** `Month.areas/tasks` are copies; the catalog is
  the "typical month". Changing one never rewrites the other. Event tasks
  live in the month, and are copied to the other month of an event that runs
  over two (`month_setup.sync_item`), under the same ids.
- **A shift belongs to the month of its date.** The Event view can show days
  of another month: actions must target `monthOf(date)` (`actions.js`), and
  the other month's view lives in `state.session.otherMonths`.
- **Rules see the nearby days of the months next door.** Wherever rules are
  evaluated, `Schedule(..., around=App._around(month_id))` gets the month
  before and after (read only, never changed); a night on the 31st clashes
  with a morning on the 1st, and a week (Monday to Sunday) counts across two
  months. Those shifts never count in the month's own totals, slots or
  statuses. A new place that evaluates rules must pass `around` too.
- **Parts of the day**: morning before 14:00, afternoon 14:00–17:00, evening
  after 17:00 (`editing.periods_for_times`). A part counts when the task
  spends at least half an hour in it (14:00–17:15 is not an evening task).
  The task form asks the program (`/api/tasks/times`); it does not work this
  out itself. A task can cover several, or be "any time".
- **Undo snapshots are whole months**, packed (zlib of JSON) in
  `App.history` (`history.py`), in memory only. Anything that must really
  disappear (comments) has to be scrubbed from them, through
  `History.scrub` (both lists), and from `backups/` too; a plain text search
  does not see inside a packed snapshot. Undo covers a month's rota only:
  People, Regular week, Settings and the typical month have none, and the
  arrows say so there. A step that accepted suggestions keeps those waiting
  (and what was said beside them), so undoing it shows them again. An
  event or closure over two months: an undo spreads to the other months only
  those its own steps changed (`Step.linked`; any other keeps its current
  form, `App._settle_linked`), and puts back there the placements its spread
  had taken off (`Step.removed`, `month_setup.put_back`), never recording a
  step in the other month.
- **Print pages take the token in the address** (`/print/<month>/<kind>?t=…`)
  because a new tab cannot send a header. API routes take it in the
  `X-Rota-Token` header only.
- **The main rota is fitted to its paper by `print-page.js`**, which also
  cuts A2/A1 posters into A3 sheets. The same script is inlined into the
  stand-alone HTML used for PDF. Test print changes in the print preview.
- **Sync never mixes inside a unit, and never chooses between versions by
  itself.** What another computer has newer, and this one did not touch, is
  taken automatically (`Sync.apply_automatic`, with a safety copy); several
  different versions are always the coordinator's choice. Only the courier
  (`Sync.exchange`) talks to the meeting place, never with a lock on the data
  held; the screens read the local mirror (`data/sync-cache/`) and never wait
  for a server. Lock order: `app.lock`, then `sync.lock`. Any new data file
  must be added to `transfer.TOP_LEVEL` (so it is exported, checked and
  synchronised) or deliberately kept local like `sync.json` and `mail.json`.
- **The 25 % ceiling is the suggestions' own, not a rule.** `solver.prefill`
  takes nobody past `SUGGEST_UP_TO` × their target (`ceiling=`); it is not
  in `eligibility.RULES`, and the picker, the checks, Ideas, Not enough
  people and Rebalance do not use it (by hand the coordinator places whoever
  they want). `solver.prefill` returns three things: `(proposals, left,
  report)`; the report (what stays open and why, every sentence made in
  Python) waits beside the suggestions in `App._suggested` and goes in the
  month view as `"suggested"`. "Suggest for these too" is the same prefill
  with `"beyond": true`: no ceiling, the waiting suggestions `held` (their
  shifts stay filled; the repair may give one to someone else, the
  improvement leaves them alone) and returned again with the new ones.
- **One computer edits at a time (the pen).** Every POST that changes shared
  data passes `server._pen` first (`PEN_FREE` lists the exceptions): a new
  route that changes data needs nothing; one that only looks, or only
  changes this computer's own settings, must be added to `PEN_FREE`.
- **`install-rota-planner.sh` installs and updates** (git clone/fetch from
  GitHub: over https when the repository is public, else with a read-only
  deploy key and GitHub's pinned host key). The program can also update itself
  from Settings (`updates.py`), with the same steps in the same order: keep
  the two in step. Keep it working with the Mac's bash 3.2, keep the
  whole script inside `main` (it may replace itself while running), and keep
  its tests (`tests/test_installer.py`) green. It refuses to update while
  `data/.running` names a live Rota Planner (written by `__main__.mark_running`),
  copies the data with `transfer.keep_safety_copy` first (so that function's
  name and arguments must stay), and goes back if the new version does not
  import. A new start file must be made executable there too. It makes one
  icon only ("Rota Planner"): new versions are offered in the browser and in
  the window, never by a separate icon. It also
  installs what is missing (git, Python, Tk: system packages with `sudo` on
  Linux; Apple's developer tools and a python.org Python, checked as signed by
  the PSF and accepted by `spctl`, on a Mac), always after showing the command
  and asking, never running as root itself. `--uninstall` removes the program,
  only the icons and keys it made for this copy, and moves the data aside
  (`--delete-data`: deleted, after typing DELETE); it refuses while
  `data/.running` names a live Rota Planner. The window's "Uninstall…" runs
  it (`desktop.uninstall`), after stopping everything that could write in the
  data folder. With other computers it first sends the last changes and gives
  the pen back (`Sync.farewell`): this computer's sealed file leaves the
  meeting place only once no other computer is behind, and deleting the data
  is refused while changes made here could not be sent. Uninstalling never
  changes anything on the other computers.
- **The control window (`window.py`) is Tk, from the standard library.**
  Shapes are painted by `paint.py` (Tk's own are jagged on Linux and
  Windows); colours are read from the tokens in `web/css/app.css`
  (`window.palettes`), so the window follows any change there. Its pages (status,
  a new version, Uninstall, uninstalling, done) are built by `Window.show`. Tk is touched only from its own thread: other threads set a
  flag that `Window.tick` picks up. It shows, never decides: wording such as
  the other computers' status comes from Python (`sync.summary`, also used
  by the top bar). Without Tk 8.6 or a screen, `--window` falls back to the
  terminal, or opens one when started from an icon (never runs unseen).
- **One Rota Planner per data folder** (`instance.py`): the lock on
  `data/.lock` is taken before anything else; a second start wakes the first
  through a socket on 127.0.0.1 that only reacts to the secret in the private
  `data/.instance`. `--reset-demo` refuses while it runs.
- **Direct PDF needs Edge, Chrome or Chromium.** Vivaldi and snap browsers
  hang in headless mode and are deliberately not used (`exporters/pdf.py`);
  the fallback is the browser's Print → Save as PDF.
- **Settings stays one calm page of four cards** (Centre and hours,
  Schedules by e-mail, Your data, The program). Values adjusted from time to time are
  plain forms with their own Save (`saveBar`: it turns blue once something
  changed). Anything set up once is **one status line** (`statusLine` in
  `components.js`: name, On/Off badge, one sentence, buttons); its set-up
  opens in a window, with numbered steps and an `explain(…)` fold for each
  technical word. Add to a card rather than adding a card, and send other
  screens to a part with `settingsLink(part, …)` (`ui.js`).
- **Renders must not lose what the user is typing.** Screens re-render on
  every state change: keep inputs alive with `replaceKeepingFocus` and
  `data-focus-id`, or build the form once (see `pages/settings.js`).

## Before you say it is done

- `python3 -W error -m unittest discover -s tests -t .` passes.
- **After any significant change** (a new feature, a new screen, a change to
  rules, data format, saving or more than a few files), **run a code review**
  of the whole change (`/code-review`) and fix what it finds before saying it
  is done.
- **When the server's checks, the session token, keys, the sync file, imports or personal data
  are touched, also run a security review** (`/security-review`) and fix what
  it finds.
- **Update the help** (`web/js/help-content.js`: screen points, tips,
  "How do I…" questions, ⓘ hints) whenever a screen, a button, a word or a
  concept changes, and the README when the user's steps change.
- You tried it in a browser on an isolated demo copy: the changed screen, in
  a narrow and a wide window, light and dark, with the keyboard.
- No real name appears in anything you wrote. No new dependency. No network.
- One focused commit per change, message in plain English saying what the
  user gets. The code is written to be **public**: nothing that identifies
  the centre, its people or its real timetable goes into code, tests, demo
  data, documents or commit messages (the demo centre is invented), and
  commits are made with a private "noreply" e-mail address. Push only when
  the user asks. Only code, tests and documentation go to the repository:
  `data/`, `data-demo/` and `exports/` are git-ignored and must stay so.
