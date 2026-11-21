# Architecture

Python standard library only, plus plain HTML/CSS/JavaScript modules. No
build step, nothing to install, nothing loaded from the internet.

```
model.py          records (dataclasses) and their JSON round trip
store.py          data folder: atomic writes, backups, format migrations
demand.py         how many people each task needs each day (day types, closures)
schedule.py       a month's assignments, indexed; a task "as it is on a day"
eligibility.py    the rules: who may do what, and how good a choice they are
checks.py         state of each assignment (ok, warning, conflict, forced, guest)
months.py         regular week, assign, one-day changes
month_setup.py    new month from the previous one; events, closures, notes; hours overview
availability.py   reading the availability form's replies, matching by e-mail fingerprint then name
emails.py         e-mail addresses kept only as one-way fingerprints
sync.py           several computers kept in step through a meeting place (one sealed file each), the pen
places.py         the meeting place: a shared folder or a WebDAV server
tls.py            encrypted connections with the certificate checked (mail, WebDAV)
updates.py        is there a newer version, and installing it when asked
instance.py       one Rota Planner per data folder: the lock, the wake-up door
service.py        the web server, started and stopped on demand
window.py paint.py   the control window (Tk) and its smooth shapes and icon
desktop.py        the program on this computer: uninstalling from the window, the old Update icon
mail.py           optional: e-mailing each person their schedule with their calendar file
editing.py        set-up changes: people, skills, areas, tasks, regular week,
                  event types and their tasks, print templates
transfer.py       all the data in one file, to move to another computer, and back
people_import.py  adding many people from a pasted list, CSV or Excel
xlsx_read.py      reading .xlsx with the standard library
solver.py         prefill (build, repair, improve, fill again; what stays open and why), rebalance, shortages, ideas
report.py         the Check step: to fix, open, overrides, balance
tips.py           hints shown in the Assistant panel
view.py           the month as the screen needs it (all numbers computed here)
app.py            what the program can do: state in memory, undo, saving
history.py        each month's undo and redo: named steps, packed (zlib of JSON), in memory only
server.py         HTTP server: static files + JSON API, this computer only, token protected
exporters/        printed documents: sheet.py (what each cell shows), documents.py
                  (main rota from a print template, event rota, weekly sheets,
                  sign-up sheet), personal.py (each person's rota, message,
                  calendar file), versions.py (what was handed out, changes
                  since), xlsx_rota.py (Excel file), html.py (escaping HTML
                  builder), pdf.py (headless Edge/Chrome/Chromium)
demo.py           demonstration data: an invented centre and invented people
web/              the interface; it only displays what the server computed
```

## Months, configurations and templates

Each month holds its own copy of the areas and tasks ("its configuration"),
so changing December never rewrites November. The catalog's areas and tasks
are the *typical month*: the starting point when there is no previous month,
and the tasks the regular week refers to. A configuration can be saved as a
template and given to any month later (`editing.use_configuration`). A
month can also differ from its configuration on single days: how many people
(`Month.demand_overrides`), other times (`Month.time_changes`), closures
(`Month.markers`). `Schedule.task_on(task, day)` gives the task as it is that
day, and every rule uses it.

## Events

An event can run from one month into the next: each month it touches keeps
a copy (same id), kept in step by `month_setup.sync_item`. An event can have
tasks of its own (`Task.event_id`, needed on the days `Task.event_days` says),
copied from its type (`EventType`, in the catalog). Whether they are on the
main rota (grid and printouts) is decided by the event
(`Event.tasks_on_main_rota`), a task being an exception only if its own
`Task.on_month_rota` is set; `Month.on_main_rota(task)` is the one rule. It
decides what is printed on the main rota; the Build grid still shows the other
event tasks (marked "event rota only") since they must be staffed, unless hidden
with the toolbar. The event's own rota always shows them all.

## Printing

The main rota is printed from a `PrintTemplate` (in the settings): paper,
areas, options. `documents.monthly` emits it as a "poster" with its size in
mm; `web/js/print-page.js` scales the grid to fill it (never so narrow that
names are cut) and cuts A2/A1 posters into overlapping A3 sheets. The same
script goes inside the files turned into PDF.

## Rules

Every constraint is one small function in `eligibility.RULES`, returning a
`Finding` (block, warn or note) or `None`. The same list drives the candidate
picker, the checks and the solver. To add a rule: write the function,
add it to `RULES`, add a test in `tests/test_rules.py`.

Rules look past the month's edges: `Schedule(month, people, settings,
around=…)` is given the months just before and after (`App._around`), and
indexes their shifts on the nearby days (the rest of the first and last
weeks, at least the day before and the day after). So a night shift on the
31st clashes with a morning shift on the 1st, and `shifts_in_week` counts a
whole week, Monday to Sunday (`model.week_start`), across two months. Those
shifts are seen by `on_day`, `shifts_in_week`, `task_on` and `hours_of`
(with the other month's own tasks and times) and never count in the month's
hours, weekends, evenings, slots or statuses. Without `around` a schedule
sees its month only.

The personal limits that count shifts (`daily_tasks`, `weekly_shifts`, a
number of shifts and never hours) count everything for a new placement. When
an existing shift is re-checked (`Placement.rechecking`: `ignore` is that
person's own shift), the shifts assigned anyway do not count against it
(`Placement.counted`): one deliberate override is flagged on that shift
alone, instead of turning the person's whole week red.

## Data format

JSON files in the data folder (`catalog.json`, `people.json`,
`settings.json`, `months/YYYY-MM.json`), each with a `schema_version`. To
change the format, bump `model.SCHEMA_VERSION` and register an upgrade
function in `store.MIGRATIONS`.

Format 6 holds targets in hours a week (`Person.target_hours_per_week`,
`Settings.default_target_hours_per_week`); format 5 and before held them in
hours a month (`target_hours`, `default_target_hours`). Its step in
`store.MIGRATIONS` changes nothing but the number, so that an older program
refuses the newer files instead of dropping the weekly targets. The monthly
targets of older files are read where the records are built
(`Person.from_dict`, `Settings.from_dict`, through
`model.weekly_from_monthly`: 15 h a month gives 3.5 h a week), whenever the
old key is there and the weekly one is not, never by the version number and
without changing the document: other computers compare documents by their
content (`sync.rank`), so a document must not change until someone really
saves it. Once saved, the file holds the weekly key only.

## Security and privacy

Rota Planner answers this computer only. The server listens on 127.0.0.1,
and the first thing it does with every request is refuse any that does not
come from this computer (`server.is_loopback`): a second lock that holds even
if the firewall is open or the way it listens changes. There is no password
and no way in from the network; other computers share the data through the
sealed sync file (below). Versions that could allow other addresses kept a
password hash and the list in `access.json`: it is deleted at start
(`__main__.forget_old_access`).

API calls need the session token kept in `data/.session-token`, in the header
`X-Rota-Token`, which other sites cannot add without a CORS pre-flight this
server never grants. A POST naming another origin is refused, and so are
requests for a host name other than 127.0.0.1 or localhost (DNS rebinding).
Print pages open in their own tab, so they take the token in their address
(`/print/<month>/<document>?t=…`), like the program's own address. Pages are
served with a strict Content-Security-Policy; the interface inserts text with
`textContent` only, and printed documents escape every text (`exporters/html.py`).

The data folder and everything in it are made private to the user (mode 700 /
600). The browser is opened through a private `start.html` in the data folder,
so the token never appears on a command line. Workbooks and data files brought
in are size-bounded and checked before anything is replaced (`xlsx_read.py`,
`transfer.read_export`). The token is permanent (so a bookmark keeps working):
on a computer shared with people you do not trust, delete
`<data>/.session-token` to get a new one.

Availability comments can mention health: they are deleted once the month is
over (or on request), from the month file, its backups and the undo history
(`App.forget_comments`, through `History.scrub`: the snapshots are packed), and the sync file is rewritten at once. PDFs are made
by a local browser on a temporary file that is removed afterwards.

E-mail addresses are never stored (`emails.py`): an address read from a card
or an import becomes a slow one-way fingerprint (PBKDF2 with a random salt
kept in `people.json`), and the address is dropped. The screens only learn
whether an address is known. Someone holding the data folder could check a
guessed address; nobody can read one back.

## E-mailing schedules (`mail.py`, optional)

Off until an account is saved in Settings › Schedules by e-mail **and** a test message
went through; until then no code path connects anywhere. The account (address,
app password, server) is in `mail.json`: private to this computer, never
exported or synchronised, the password never sent to the screen. The
connection (`smtplib`) is always encrypted (STARTTLS or SSL) with the
server's certificate checked.

People's addresses are still never stored. The coordinator gives a list when
sending (`mail.read_list` accepts any table or `Name <address>` lines);
`mail.preview` recognises each address by its fingerprint, else by name, and
returns addresses masked. On Send the list is read again, messages are built
with `email.message.EmailMessage` (one recipient each, the schedule text and
the calendar file) and sent by a background thread; `mail.status` reports
counts and names only. Addresses are dropped when the thread ends;
`Month.mailed` keeps who was reached for which version. The server answers
this computer only, so the list never crosses the network.

## Several computers (`sync.py`, `places.py`, `tls.py`)

Each computer keeps its own data folder and puts one sealed file of its own,
`rota-<computer id>.rpsync`, at a **meeting place**. It never writes another
computer's file, so no file has two writers; the programs never connect to
each other.

- **Places** (`places.py`): the same five gestures (`list`, `read`, `write`,
  `delete`, `check`) for a shared folder (`FolderPlace`) and a WebDAV server
  (`WebDavPlace`: https with the certificate checked by `tls.py`, basic
  authentication with an app password, no redirect followed, answers capped,
  only a 207 counts as a listing so a Wi-Fi sign-in page is "offline" and
  never "the files are gone"). Another kind (S3…) would be one more class.
- **Sealing**: the key comes from a pairing code (about 99 bits, PBKDF2),
  typed once on each computer and kept only in `sync.json`. The standard
  library has no cipher, so the file is built from its primitives: a
  SHAKE-256 keystream (key + fresh random nonce) XORed with the compressed
  content, then HMAC-SHA256 over everything, checked before anything is read.
  Home-made from sound parts: it protects the data at rest on the server; it
  is not meant against an attacker who holds the code.
- **Offline first**: only the courier (`Sync.exchange`, a background thread)
  talks to the place, with no lock on the data held. It looks first, fetches
  the files that changed into a local mirror (`data/sync-cache/`, still
  sealed), counts this computer's own changes, then sends its file if it
  changed. `status`, `preview` and `apply` read the mirror only, so the
  screens never wait for a server and everything works without connection;
  when the place does not answer, rounds are spaced out (15 s to 5 min).
- **Units and versions**: the data is cut in units (people, tasks & areas,
  settings, templates, each month), each with a version vector. `rank`
  compares all computers' versions of a unit: versions with the same content
  are one; those no other includes are the candidates. One candidate that is
  elsewhere is taken by itself (`apply_automatic`, under `app.lock`, after
  `transfer.checked` and a safety copy); several candidates are the
  coordinator's choice, and the choice gets a vector that includes them all,
  so it then wins everywhere without another question. Nothing is merged
  inside a unit.
- **What keeps it honest**: the same vectors with another content are never
  "in sync"; a month ending (its comments stop being sent) is not a new
  version; documents are compared after migration; a unit removed here is
  never brought back by itself; a data folder restored from a backup, or
  copied to another computer, is noticed (its own file at the place, or the
  others, remember higher counters or another machine) and gets a new
  identity, with automatic taking paused until the coordinator chooses.
- **The pen**: one computer edits at a time. Each writes only its own note
  (`pen-<id>.rpsync`, sealed): "I ask" then "I hold", repeated every minute.
  Between two that ask together, the note that reached the place first wins
  (the place's clock, not the computers'). A holder whose note lapsed (5 min)
  can be taken over explicitly, and learns it when it is back. A live holder
  can be *asked* ("Ask to edit"): the asking note names it, and at its next
  round the holder lets go before sealing its file (`Sync._hand_over`), so
  that file carries every change; the asker then takes what is newer (even
  with automatic taking off) and the pen (`Sync.follow_up`). While asked, the
  holder cannot take the pen back. No answer within 10 minutes from a holder
  still there (an older version): the asking stops, and says so. Every POST
  that changes shared data goes through `server._pen` (`PEN_FREE` lists the
  rest): it takes the pen if free, answers 423 in plain words if not, and
  409 when newer data was taken on the way. Without connection there is no
  pen: looking only, unless "Work offline anyway" is chosen.
- A computer that left shows as not seen and can be forgotten; stopping
  removes this computer's own files. Uninstalling (`Sync.farewell`, run by the
  installer through `python3 -m rotaplanner.sync leave`) first sends what
  changed and gives the pen back, then removes them only if no other computer
  is behind (`Sync.behind`: one that has not taken a unit this computer sent,
  or any unit whose versions differ): otherwise they stay, so that computer
  still gets the changes, and the coordinator forgets this one there later.
  Deleting the data is refused while changes made here could not be sent.
- **Whoever can write at the place, without the key, can do nothing but be
  ignored.** What a file is (data or pen note), whose it is and, for a pen
  note, when it was written are inside the seal, and checked on every read:
  a copied, renamed, replayed or made-up file holds nothing. Files that do
  not open with the key are said ("still arriving", "another code") but never
  kept; a bounded number of computers and bytes is looked at per round; every
  request to the server has an overall time limit.

## Starting, stopping, one copy (`instance.py`, `service.py`, `window.py`)

`run.py` takes an OS lock on `data/.lock` first (fcntl/msvcrt, released by
the system even after a crash). If it is taken, the start wakes the program
holding it: a tiny socket on 127.0.0.1 whose port and secret are in the
private `data/.instance` (the secret means only someone who can read the data
folder can wake it, and waking only brings the window forward); without a
window, the browser opens on the running one. `Service` starts and stops the
HTTP server while `App` stays loaded. *Stop* keeps the port and answers every
request with a fixed "stopped" page before any token is looked at (giving the
port up would let another account take it and receive the open tab's token);
the port is given up only when the program ends. If the usual port was
taken meanwhile when it starts again, the new one gets a new session token. `--window` shows the Tk control
window (status light, address, Open, Start/Stop, several computers, the
version); closing it stops the server. It has pages: a new version (the same
`Updates.install` as the browser's, with the version it listed) and, for a
copy installed by the installer with its usual data (`desktop.supported`),
Uninstall. What Uninstall says first comes from `desktop.advice` (a fresh
`Sync.check_now`); then, from a thread: the server stops, the update loop
stops (`Updates.stop`: nothing is written again), `Sync.goodbye` then
`Sync.detach` (nothing is ever sent again from this process), the lock and
`.running` go (`__main__.clear_running`), and `desktop.uninstall` runs the
installer's `--uninstall --yes [--delete-data] --result FILE`. The window
stays to show the outcome; the program's folder is already gone, so nothing
is imported after that point, and `run` returns `UNINSTALLED`. SIGTERM ends the program
cleanly (the notes in `data/` are removed). The installer points the icons at
the window when a Python with Tk 8.6 is found (python.org's on a Mac, whose
own Tk is too old), with the icon drawn by `paint.py`. On Linux the taskbar
finds that icon through the window's class (`window.TK_CLASS`, which Tk
writes "Rota-planner") and the launcher's `StartupWMClass`: the two must say
the same, and only the window's launcher names it.

## A newer version (`updates.py`)

For a copy installed with git. *Looking* is a `git fetch` of the program's
own repository (once a day unless turned off, or on "Check now"), then a
count and the subjects of the commits this copy does not have: commit
subjects are written for the user, so they are the "what is new" list.
`info()` never touches the network. *Installing* happens only on a yes, on
the computer where the program runs, with the installer's steps in the
installer's order: refuse hand-changed program files, a history that is not
a fast-forward, and a version that holds files in the data folders (names
compared without their case); copy the data (`before-update-…zip`, the only
moment `app.lock` is held); `merge --ff-only`; compile (in memory) and import
the new version in another process, and `reset --hard` back if it does not
load, removing every `__pycache__` so no compiled file of the version taken
back is trusted. The page sends the version it listed (`target`): if the
repository moved on meanwhile, nothing is installed and the list is shown
again. Then `Updates.restart` is set: the terminal loop or the window ends a
second later, the pen is given back, and `__main__.start_again` replaces the
process (`os.execv`; a new process on Windows). The command is rebuilt from
the parsed arguments (`restart_command`): the same data folder by its full
path, the port in use, the window if it had one, `--no-browser`, and never a
`--reset-…`. `--after-update` tells the new process which port the open page
expects: if it had to take another one, the session token is renewed (the
port was free for a moment, and whoever took it may have received the
token). The page waits for `boot` (a random name for each run of the
program) to change, then reloads.

## Installing and updating (`install-rota-planner.sh`)

One bash file, run by a person on a Mac or Linux computer, or by the window's
Uninstall; it is not part of the program (which looks for a newer version by
itself and installs it on a yes: `updates.py`, the same steps as here). It
makes one icon, "Rota Planner": new versions are offered in the program
itself, and the "Update Rota Planner" icon older versions made is removed
(by `make_launchers`, and at the start of a version updated from inside the
program, by `desktop.tidy_old_update_icon`), only when it names this copy. It installs
with `git clone` and updates with `git fetch` + `merge --ff-only`, from the
GitHub repository: over https when the repository is public; otherwise
through a deploy key made for that computer (read only) and a pinned GitHub
host key (`~/.ssh/rota-planner-*`; it ignores any SSH agent and
`~/.ssh/config`). Before an update it refuses if `data/.running`
names a live process, lists the new commits (their messages are written for
the user), keeps a copy of the data with `transfer.keep_safety_copy`
(`before-update-…zip`), and after the update goes back to the previous
commit if the new code does not compile and import. Data in `data/` is
git-ignored, so neither clone nor update touch it; because git treats ignored
files as disposable, a version that contains anything under `data/`,
`data-demo/` or `exports/` is refused. Data brought from an older copy
(`--from`) is made private to the user (0700/0600). It also writes the
launchers: `~/Applications/*.app` (a script that opens the `.command` file in
Terminal) on a Mac, `.desktop` entries on Linux. `tests/test_installer.py`
runs it against a local repository and a temporary home folder (with a
pretend `sudo` for the packages).

Missing tools are installed only after the command is shown and agreed to:
Linux packages through `sudo` and the package manager found (apt-get, dnf,
pacman, zypper); on a Mac, `xcode-select --install` (waited for) and the
newest python.org 3.13 installer, used only if `pkgutil` shows the Python
Software Foundation's Developer ID and `spctl` accepts it. `--uninstall`
moves `data/` and `exports/` to *~/Rota Planner data (kept <date>)* before
removing anything (so a failure leaves everything in place), then removes the
icons that name this folder, the deploy key and the program. Before that, a
copy that shares with other computers says goodbye (`sync leave`, see
above); with `--delete-data` it stops there if changes made here could not
be sent. `--result FILE` writes `status=`, `kept=` and `sync=` lines for the
window. It works on the real folder (a link
is resolved), refuses a linked `data/` or `exports/`, and refuses a copy with
program changes of its own (changed or new files, commits not on GitHub), as
the update does. The python.org installer is checked and installed from a
folder only root can change, and is downloaded over https only.

## Interface

`web/js/state.js` holds the single state object; screens subscribe and
re-render. `web/js/actions.js` is the only place that changes the rota.

Undo is per month (a month is the document, and the unit of sync), in memory
only: `history.py` keeps, for each month, the steps that can be undone and
redone, each with a name made by `App` when the change succeeded ("Ana B. →
Lunch cook, Tue 1 Dec"), the month as it was (packed: about 10 KB instead of
300 KB), and, for accepted suggestions, the suggestions that were waiting.
Step ids grow across the program, so the newest change in two months on
screen (an event's Event view) is the one with the highest id. Going back k
steps (`App.history_go`) is k undos with one save. The screen is told what was
done (`did`: text, and the step id that reverses it), and a message's Undo
names its step (`expect`), so it never undoes something changed since. An
event or closure that runs into other months follows an undo there only when
a step crossed changed it itself (`Step.linked`); one changed from another
month since keeps its current form, so an older undo never silently reverts
it. When a change reaches another month and takes placements off it (the
event's tasks gone, regular shifts on a closed day), its step keeps them
(`Step.removed`) and crossing it the other way puts each back where its task
exists again and the place is free (`month_setup.put_back`). People,
Regular week, Settings and the typical month have no undo: they are saved as
they are changed.
Colours, sizes and spacing are tokens at the top of `web/css/app.css`.
Zoom levels are defined once in `web/js/build/range.js`.
