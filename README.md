# Rota Planner

A local helper for preparing the monthly service rota. It runs on your own
computer (macOS, Linux or Windows) and needs only Python 3.9 or newer.
Residents' details stay on this machine; the internet is used for three
things only, each optional (see *Privacy* below).

## Start

Double-click the start file for your computer, or the *Rota Planner* icon
made by the installer:

- Windows: `Rota Planner.bat`
- macOS: `Rota Planner.command` (or *Rota Planner* in Applications)
- Linux: `rota-planner.sh` (or *Rota Planner* in the applications menu)

From the installer's icon, and on Windows, **Rota Planner's window** opens:
whether it is running (the green light), its address with *Copy*, **Open
Rota Planner** (the browser), **Stop** and **Start**, who can use it, the
other computers and where the data is. Closing the window stops Rota
Planner. Started from the start file on a Mac or Linux, it runs in a
Terminal window instead (Ctrl+C stops it). Either way the browser opens on
the rota.

Only one Rota Planner runs with the same data: starting it again brings its
window to the front (or opens the browser on it) instead of starting a
second copy, which could overwrite the first one's changes.

The window needs Tk, which comes with Python on Windows and with the
python.org Python on a Mac; on Linux install the package `python3-tk`.
Without it, Rota Planner runs in a terminal window as before. From a
terminal:

```
python3 run.py            # your data, kept in ./data; this terminal shows the address
python3 run.py --window   # with its window
python3 run.py --demo     # demonstration data with invented names
```

In a terminal, keep it open while you work; close it (or press Ctrl+C) to
stop.

*Stop* keeps Rota Planner's address but answers only "Rota Planner is
stopped" until *Start*, so no other program can take the address meanwhile.
On Windows, keep the program in your own user folder (not, for example,
`C:\RotaPlanner`), so other accounts of the computer cannot read its `data`
folder.

**Help is built in**: the **?** at the top right (or the `?` key) explains the
screen you are on and answers "How do I…" questions; the first time you open
a screen, *Show me* gives a short guided tour.

## Install or update (Mac and Linux)

`install-rota-planner.sh` is the only file needed. Open a Terminal and run
these two lines (the first downloads the file, the second runs it):

```
curl -fsSLO https://raw.githubusercontent.com/davidhello7772/rota-planner/master/install-rota-planner.sh
bash install-rota-planner.sh
```

(Or bring the file on a USB stick.)

- **The first time** it installs Rota Planner in *Rota Planner* in your home
  folder, and adds *Rota Planner* and *Update Rota Planner* to the
  Applications (Mac) or the applications menu (Linux). If the repository on
  GitHub is private, it makes a key for this computer that can only *read*
  the program, shows it, and says where to add it (GitHub › the repository ›
  Settings › Deploy keys › Add deploy key, "Allow write access" left
  unticked); then press Enter. A public repository needs no key.
  If Rota Planner was already used from another folder (a copy unpacked from
  a zip), drag that folder into the Terminal when asked: its data is copied
  over, and the old copy is left as it was.
- **To update**, close Rota Planner and open *Update Rota Planner* (or run the
  same command again). It lists what is new, saves a copy of the data in
  `data/backups/before-update-…zip`, then changes the program only. It does
  nothing while Rota Planner is open, if program files were changed by hand,
  or, if the new version does not start, puts the previous one back.
- With other computers, update them all on the same day.
- **What it needs is installed for you**, after it shows how and you agree:
  git and Python 3.9+, and Tk for Rota Planner's window. On Linux this uses
  the system's package manager (apt, dnf, pacman or zypper; `sudo` asks your
  password). On a Mac, Apple's *Command Line Developer Tools* (a window offers
  them) and Python from python.org, checked as signed by the Python Software
  Foundation and accepted by the Mac before it is installed.
- **To uninstall**, close Rota Planner, then run:

  ```
  bash "$HOME/Rota Planner/install-rota-planner.sh" --uninstall
  ```

  It removes the program, its icons and this computer's key. Your data is
  kept, moved to *Rota Planner data (kept <date>)* in your home folder (bring
  it back with `--from` after installing again). Add `--delete-data` to delete
  the data too: you are asked to type DELETE. git, Python and Tk stay
  installed. On GitHub, also remove the computer's key (Settings › Deploy
  keys).

**From inside Rota Planner**: *Settings › Version and updates* says whether
a new version exists (it looks once a day, unless you turn that off, and when
you press *Check now*), lists what is new, and installs it when you say so:
a copy of the data is saved first, and Rota Planner starts again by itself.
Looking only asks the program's page on GitHub what is new; nothing about
your data is sent, and nothing is installed without your yes.

**On Windows** there is no installer yet: on the repository's page choose
*Code › Download ZIP*, unpack it in your own user folder, and start
`Rota Planner.bat` (Python from python.org is needed). To update, download
the ZIP again and copy its files over the program's, keeping the `data`
folder.

## First start: bring in the current Google Sheet

In Google Sheets choose *File › Download › Microsoft Excel*, then in Rota
Planner open *Settings › Import the current workbook*. *Check the file* shows
what will be imported (people, skills, tasks, regular week, monthly rotas,
availability, preferences) before anything changes. "Replace names with
invented ones" keeps the import anonymous.

## Setting things up

- **People**: everyone with skills, hours and constraints. *Skills grid* ticks
  skills for many people at once; *Import people* adds a pasted list or a file.
- **Tasks & Areas**: each month has its own tasks. Pick the month at the top;
  *Use the configuration of…* takes another month's tasks or a template, *Save
  as template…* keeps the current ones for later. Drag cards to reorder them.
- **Regular week**: who does the same task on the same weekday (every week,
  odd or even weeks, or the 1st…last of the month).

## Each month (around the 10th, for the month after)

1. **Set up**: *Create January 2027* — one click when nothing changes: the
   tasks of the previous month (or a template) and the regular week are
   already in place. Then add what is different: events and retreats, closed
   days (with the tasks still needed, such as security), notes. Click a day on
   the calendar to treat it as an event day or a usual day.
2. **Availability**: paste the form's replies (or choose its CSV / Excel
   file), check the doubtful names, save. See who has not replied (*Copy names*
   for a reminder), tick the comments once dealt with, click anyone to change
   their days. *Free them* takes regulars off the days they are away.
3. **Build**: the rota itself. The *Event* view shows an event day by day,
   grouped by area.
   - *Prefill the rota…* (Assistant panel) suggests someone for every open
     shift — or only the days on screen, or one area. Suggestions appear
     dashed; accept them all, or click one to choose someone else.
   - *Rebalance hours* suggests moves from the busiest people to the least
     busy, one by one.
   - *Not enough people* lists the days where open shifts outnumber the people
     who could take them, and the skills most often missing.
   - When nobody can take a shift, its picker shows *Ideas*: an exchange that
     frees someone (done in one click), someone who has not replied, a comment
     about that day, a regular who is away, someone who could be trained.
4. **Check**: what breaks a rule, the open shifts (essential first), the rules
   overridden and why, and the balance of hours, weekends and evenings, with
   the two previous months.
5. **Publish**: the rota goes through phases, shown as steps and written
   under the month's name on every screen: **Draft** → **Manager's check**
   (*Give to the manager to check*: printouts say "Version 1 to check") →
   **Corrections** (what you change after the check is marked ●; give it
   again if needed) → **Published** (*Publish for the residents*: the version
   to print and put up). Adjustments after printing show as "Published · 2
   changes" until you *Publish an updated version*. The manager's check
   can be skipped. Then print, from three tabs (*For the wall*, *For each
   person*, *Files*):
   - **Main rota**, from your own templates: paper (A3; A2 or A1 on A3 sheets
     to tape together), areas (for example Kitchen + Dining room + Cafe), options;
   - an **event's own rota**, day by day; **weekly sheets** per area in large
     print; the **shifts to sign up for**; **personal rotas**; the **Excel
     file** for Google Sheets and a calendar file per person.
   - *Send each person their schedule*: type a few letters, Enter copies the
     text for your e-mail (also with the `S` key from anywhere).
   Each document opens ready to print; in the print window, *Save as PDF*
   makes a file. With Edge, Chrome or Chromium installed, *Save PDF* does it
   in one click.

## Events

An event (retreat, festival…) makes its days "event days", on which usual
tasks use their *Event day* number of people. It can have **tasks of its
own**: choose its **type** when adding it and it gets the tasks of that type
(set once in *Tasks & Areas › Event types*). Every task is always on the
rota to fill; *Print these tasks on the main rota too* decides the printed
main rota. An event can run from the end of a month into the next: it stays
one event, planned and printed whole.

## Working on the rota

- **Click a shift** to see who could take it, best choice first, with the reasons.
  People who are away, busy or not trained are listed last; you can still
  *Assign anyway* and note why.
- **Zoom** with the − / + buttons or Ctrl + mouse wheel. *Fit* chooses the
  largest level that fits the screen but always keeps names readable
  ("Quinn J."); zoom out by hand for initials or coloured bars. Views: Month,
  2 weeks, Week, Day. *Times* shows or hides the hours under each task.
- **One day is different**: from a shift, *◆ Highlight* (or the H key) marks it
  to fill when possible; *Not needed this day*, *One fewer* and *One more
  needed* change the number of people for that day; *Change the time this day*
  handles a special meal. In *All tasks* mode, a "+" asks for a shift on a day
  the task is not usually needed.
- **People panel**: click a name to see their shifts and where they could still
  help; *Paint shifts* gives them shifts in a few clicks.
- **Keyboard**: arrows move between shifts, Enter opens the choice, Delete empties
  a shift, H highlights an empty one, Ctrl+Z / Ctrl+Y undo and redo, F gives the
  whole screen to the rota.

Every change is saved immediately. Previous versions of each file are kept in
`data/backups/`.

## Password and other computers

*Settings › Who can use Rota Planner*:

- **Password**: once set, it is asked each time Rota Planner is opened, on
  this computer too. *Log out* is at the bottom left. Forgotten? On the
  computer where it runs, start it from a terminal with
  `python3 run.py --reset-access`: the password is removed and only that
  computer can open it (the data is not touched).
- **Other computers**: with a password set, choose *Also other computers* and
  type their addresses (192.168.1.57) or a range (192.168.1.0/24). The card
  shows the address to open on them. *Check the firewall* says whether the
  port must be opened, with the exact command limited to those addresses.
  Rota Planner never changes the firewall itself. The connection is not
  encrypted: use this on the staff network only, never on a public Wi-Fi.

## Sending the schedules by e-mail (optional)

By default you copy each person's schedule into your own e-mail (*Publish ›
For each person*). Rota Planner can also send them itself, each with the
person's calendar file, from a Gmail or Google Workspace account:

1. The account needs 2-Step Verification. Signed in to it, open
   `myaccount.google.com/apppasswords` and create an **app password** (a
   16-letter code). If Google does not offer that page, the organisation does
   not allow app passwords, and this stays off.
2. *Settings › E-mail (optional)*: the address, the code, *Save and send a
   test*. A test message arrives in the account's own inbox.
3. *Publish › For each person › Send by e-mail…*: paste a list of names and
   addresses (the availability form's reply sheet works as it is), check who
   was recognised, choose who to write to (everyone, only where shifts
   changed, only those not e-mailed yet), send.

Addresses are still never kept: you give the list each time. The app password
stays in a private file on this computer; *Remove* turns e-mail off again.
This is done on the computer where Rota Planner runs, not from another one.

## Several computers, here or elsewhere

*Settings › Other computers* lets two or more computers use the same rota,
each with its own copy, from anywhere:

1. **On the first computer**: choose where they meet, *Test the connection*,
   give the computer a name, *Start*: it shows a **pairing code**.
   - *A server on the internet* (nothing to install): a WebDAV folder, for
     example at Koofr (10 GB free): the folder's address
     (`https://app.koofr.net/dav/Koofr/Rota Planner/`), the account's e-mail,
     and an **app password** made in Koofr › Preferences › Password.
   - or *A shared folder* already on every computer (Google Drive, Synology
     Drive, a network folder).
2. **On each other computer**: *Join the others*, the same address, and the
   pairing code. A computer with no data yet offers this on its first screen
   (*Connect to existing data*); everything arrives at once.

Then:

- **Changes travel by themselves.** The cloud in the top bar says *In sync*,
  *sent*, or *Offline*. Nothing is ever mixed inside a month or a list.
- **One computer edits at a time.** The first change takes *the pen* (*You
  are editing*); *I have finished*, closing Rota Planner, or 20 minutes
  without a change gives it back. The others can look; what the editor
  changes arrives on their screen.
- **Left it open on another computer?** Press *Ask to edit*: that computer
  sends its changes and hands over by itself, usually within a minute (both
  need this version of Rota Planner). If it is switched off or asleep,
  *Take over* is offered after a few minutes instead.
- **Without connection** everything keeps working. With the pen, go on: it is
  sent when the connection is back. Without it you can look, or *Work offline
  anyway*, knowingly.
- **If the same month or list was changed on two computers** (after working
  offline, or after *Take over* from a computer that went away), you are
  asked which to keep; the choice then reaches every computer. A copy of the
  data is kept first in `data/backups/` (`before-sync-…`, `before-choice-…`).
- A computer that no longer takes part can be *forgotten* from another one
  (Settings › Other computers, *Forget this computer* next to its name);
  *Stop synchronising* removes this computer's file from the meeting place.

What sits on the server is sealed with the pairing code: the service cannot
read names or rotas. Keep the code private (never on the server, never by
e-mail).

## Privacy

Everything stays in the `data` folder on this computer. Rota Planner uses
the internet for three things only, each optional: looking once a day
whether a newer version exists (nothing about your data is sent; it can be
turned off in Settings › Version and updates); the e-mail if you set it up
(that mail server only, when you press Send); and other computers if you set
them up (a sealed copy goes to the server or shared folder you chose, which
cannot read it; Rota Planner talks to that server in the background, and to
nothing else; comments of a finished month never go there). E-mail
addresses are never kept: only a fingerprint that recognises an address in
the form's replies but cannot be read back. Notes on a person and on a shift
are for practical matters only, never health. Comments left with availability replies can be private:
they are deleted when the month is over, or earlier with *Delete all*.
*Settings › Move to another computer* puts everything in one file (carry it
on a USB stick rather than by e-mail) and brings it in on the other side.

## For developers

```
python3 -m unittest discover -s tests -t .
```

See `ARCHITECTURE.md` for how the code is organised, and `AGENTS.md` before
changing it (with or without an AI assistant): rules, recipes and vocabulary.
