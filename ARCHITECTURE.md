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
access.py         who may connect (this computer / allowed addresses), password, sessions
firewall.py       looking at the firewall (read only) and the narrowest command to suggest
sync.py           several computers kept in step through a meeting place (one sealed file each), the pen
places.py         the meeting place: a shared folder or a WebDAV server
tls.py            encrypted connections with the certificate checked (mail, WebDAV)
updates.py        is there a newer version, and installing it when asked
instance.py       one Rota Planner per data folder: the lock, the wake-up door
service.py        the web server, started and stopped on demand
window.py paint.py   the control window (Tk) and its smooth shapes and icon
mail.py           optional: e-mailing each person their schedule with their calendar file
editing.py        set-up changes: people, skills, areas, tasks, regular week,
                  event types and their tasks, print templates
transfer.py       all the data in one file, to move to another computer, and back
people_import.py  adding many people from a pasted list, CSV or Excel
importers/        legacy_workbook.py: the current Google Sheets rota (.xlsx)
xlsx_read.py      reading .xlsx with the standard library
solver.py         prefill (build, repair, improve), rebalance, shortages, ideas
report.py         the Check step: to fix, open, overrides, balance
tips.py           hints shown in the Assistant panel
view.py           the month as the screen needs it (all numbers computed here)
app.py            what the program can do: state in memory, undo, saving
server.py         HTTP server: static files + JSON API, token or password protected
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

## Data format

JSON files in the data folder (`catalog.json`, `people.json`,
`settings.json`, `months/YYYY-MM.json`), each with a `schema_version`. To
change the format, bump `model.SCHEMA_VERSION` and register an upgrade
function in `store.MIGRATIONS`.

## Security and privacy

By default the server listens on 127.0.0.1 only. The coordinator may allow
other computers by address (`access.py`, Settings › Who can use Rota
Planner): the server then listens on the network, answers only this computer
and those addresses, and a password is always required. Who may connect, the
password and the firewall check can only be changed from this computer;
`--reset-access` removes a forgotten password. The connection is plain HTTP:
other computers are meant for the staff network only (the screen says so).

Without a password, API calls need the permanent session token; with one, a
session id given at login, kept in memory only, ended after 8 hours unused,
and tied to the address that logged in. Either travels in the header
`X-Rota-Token`, which other sites cannot add without a CORS pre-flight this
server never grants. A POST naming another origin is refused, and so are
requests for a host name other than 127.0.0.1, localhost, an IP address or
this computer's own name (DNS rebinding). Wrong passwords make the address
wait (doubling, up to 15 minutes). The password is kept as an scrypt hash (PBKDF2 where scrypt is missing) in
`access.json`, which is never exported or synchronised.

`firewall.py` only reads the firewall's state (fixed commands, no shell,
never elevated) and shows the narrowest rule to type: this port, TCP, from
the allowed addresses only. The program never changes the firewall.
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
(`App.forget_comments`), and the sync file is rewritten at once. PDFs are made
by a local browser on a temporary file that is removed afterwards.

E-mail addresses are never stored (`emails.py`): an address read from a card
or an import becomes a slow one-way fingerprint (PBKDF2 with a random salt
kept in `people.json`), and the address is dropped. The screens only learn
whether an address is known. Someone holding the data folder could check a
guessed address; nobody can read one back.

## E-mailing schedules (`mail.py`, optional)

Off until an account is saved in Settings › E-mail **and** a test message
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
`Month.mailed` keeps who was reached for which version. Every mail route is
refused from another computer (`server.local_only`), so the list never
crosses the network.

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
- A computer that left shows as not seen and can be forgotten; stopping (or
  uninstalling with `--delete-data`) removes this computer's own files.
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
the port is given up only when the program ends. When Settings › Access
changes the address it listens again on the same port; if that port was
taken meanwhile, the new one gets a new session token and all sessions end. `--window` shows the Tk control
window (status light, address, Open, Start/Stop, who can use it, the other
computers, data folder); closing it stops the server. SIGTERM ends the program
cleanly (the notes in `data/` are removed). The installer points the icons at
the window when a Python with Tk 8.6 is found (python.org's on a Mac, whose
own Tk is too old), with the icon drawn by `paint.py`.

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

One bash file, run by a person on a Mac or Linux computer; it is not part of
the program (which looks for a newer version by itself and installs it on a
yes: `updates.py`, the same steps as here). It installs
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
icons that name this folder, the deploy key and the program; with
`--delete-data` it also removes this computer's sealed file from the meeting
place (never another computer's). It works on the real folder (a link
is resolved), refuses a linked `data/` or `exports/`, and refuses a copy with
program changes of its own (changed or new files, commits not on GitHub), as
the update does. The python.org installer is checked and installed from a
folder only root can change, and is downloaded over https only.

## Interface

`web/js/state.js` holds the single state object; screens subscribe and
re-render. `web/js/actions.js` is the only place that changes the rota.
Colours, sizes and spacing are tokens at the top of `web/css/app.css`.
Zoom levels are defined once in `web/js/build/range.js`.
