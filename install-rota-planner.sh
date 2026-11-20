#!/bin/bash
# Rota Planner: install it, or bring it up to date, on a Mac or a Linux computer.
#
# One file. Open a Terminal and run:
#
#     bash install-rota-planner.sh
#
# - On a computer without Rota Planner it installs it in "Rota Planner" in your
#   home folder, and adds "Rota Planner" and "Update Rota Planner" to the
#   Applications (Mac) or to the applications menu (Linux).
# - On a computer where it is installed it brings it up to date: the data is
#   kept (a copy is saved in data/backups first) and only the program changes.
#
# The program is downloaded from its GitHub repository. If the repository is
# public, nothing more is needed. If it is private, a key is made for this
# computer that can only read that repository (a "deploy key"); the first
# time, the script shows the key and how to add it on GitHub.
#
# Options:
#   --update          only update an installed copy (what "Update Rota Planner" does)
#   --folder DIR      where Rota Planner is, or goes (default: ~/Rota Planner)
#   --from DIR        bring the data from another copy (for example one installed from a zip)
#   --repo URL        download from another repository (default: the program's own)
#   --wait            wait for Enter before closing (used by the icons)
#   --uninstall       remove Rota Planner: the program, its icons and this computer's key;
#                     the data is kept (moved aside) unless --delete-data is added too
#   --delete-data     with --uninstall: also delete all the data (asks you to type DELETE)
#   --yes             agree to the questions (for use without a Terminal)
#
# What Rota Planner needs (git, Python 3.9+ and, for its window, Tk) is installed
# when missing, after showing how and asking: on Linux with the system's package
# manager (your password is asked by sudo), on a Mac from Apple's developer tools
# and python.org (the download is checked as signed by the Python Software
# Foundation and accepted by the Mac before it is installed).
#
# This script runs outside Rota Planner: the program itself never connects to
# the internet to look for updates (AGENTS.md, rule 1). Keep it working with the
# Mac's own bash 3.2: no associative arrays, no ${x,,}, no mapfile.

set -u

DEFAULT_REPO="git@github.com:davidhello7772/rota-planner.git"
# GitHub's published host key (docs.github.com, "GitHub's SSH key fingerprints"):
# the download only goes ahead if github.com proves it holds it.
GITHUB_HOST_KEY="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
KEY_NAME="rota-planner-deploy"
BUNDLE_ID="local.rotaplanner"

WAIT=0
YES=0          # --yes: agree to every question (for running without a Terminal)
DELETE_DATA=0  # --delete-data: uninstall removes the data too

say() { printf '%s\n' "$*"; }

finish() {  # finish <exit code>
  if [ "$WAIT" = 1 ] && [ -t 0 ]; then
    printf '\nPress Enter to close.'
    read -r _ || true
  fi
  exit "$1"
}

die() {
  say ""
  say "$*"
  finish 1
}

ask_yes() {  # ask_yes "Question?" -> true for yes (the default)
  [ "$YES" = 1 ] && return 0
  [ -t 0 ] || return 1
  printf '%s [Y/n] ' "$1"
  local answer
  read -r answer || return 1
  case "$answer" in [Nn]*) return 1 ;; *) return 0 ;; esac
}

# -- the computer ---------------------------------------------------------

SYSTEM=""
LAUNCHERS=""  # mac or linux: the kind of icons (ROTA_INSTALL_LAUNCHERS lets the tests make the other kind)

check_system() {
  case "$(uname -s)" in
    Darwin) SYSTEM=mac ;;
    Linux) SYSTEM=linux ;;
    *) die "This installer is for a Mac or a Linux computer. On Windows, see README.md." ;;
  esac
  LAUNCHERS="${ROTA_INSTALL_LAUNCHERS:-$SYSTEM}"
  if [ "$(id -u)" = 0 ]; then
    die "Run this as yourself, not with sudo or as root: Rota Planner and its data belong to the person who uses it."
  fi
}

have_tool() {
  local path
  path=$(command -v "$1" 2>/dev/null) || return 1
  if [ "$SYSTEM" = mac ] && [ "$path" = "/usr/bin/$1" ]; then
    # On a Mac, /usr/bin/git and /usr/bin/python3 are only stand-ins until
    # Apple's developer tools are installed.
    xcode-select -p >/dev/null 2>&1 || return 1
  fi
  return 0
}

safe_path() {  # the folder goes into launchers: refuse characters that would need escaping
  case "$1" in
    *'"'* | *'`'* | *'$'* | *'\'* | *"'"* | *'%'* | *'
'*)
      die "Please choose a folder whose name has no quotes, \$, %, \\ or line breaks: $1" ;;
  esac
}

# -- the download key -----------------------------------------------------

SSH_COMMAND=""

github_path() {  # owner/name from git@github.com:owner/name.git, or nothing
  case "$1" in
    git@github.com:*) local path="${1#git@github.com:}"; say "${path%.git}" ;;
  esac
}

public_address() {  # the https address of a GitHub repository, if anyone may read it; else nothing
  local path
  path=$(github_path "$1")
  [ -n "$path" ] || return 0
  # No password is ever asked: a private repository simply does not answer.
  if GIT_TERMINAL_PROMPT=0 git -c credential.helper= ls-remote "https://github.com/$path.git" HEAD >/dev/null 2>&1; then
    say "https://github.com/$path.git"
  fi
}

prepare_key() {
  local repo="$1" path key known
  path=$(github_path "$repo")
  [ -n "$path" ] || return 0  # another kind of address (a local copy, https): nothing to prepare
  key="$HOME/.ssh/$KEY_NAME"
  known="$HOME/.ssh/rota-planner-known_hosts"
  safe_path "$HOME"
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" || die "Could not prepare $HOME/.ssh."
  printf '%s\n' "$GITHUB_HOST_KEY" >"$known" || die "Could not write $known."
  if [ ! -f "$key" ]; then
    ssh-keygen -q -t ed25519 -N "" -C "rota-planner $(computer_name)" -f "$key" >/dev/null \
      || die "Could not make a key for this computer (ssh-keygen failed)."
  elif [ ! -s "$key.pub" ]; then
    ssh-keygen -y -f "$key" >"$key.pub" || die "Could not read this computer's key ($key)."
  fi
  # Only this key, never an SSH agent's keys or ~/.ssh/config, and only the real
  # github.com (no other list of known computers is consulted).
  SSH_COMMAND="ssh -F /dev/null -i '$key' -o IdentitiesOnly=yes -o IdentityAgent=none -o UserKnownHostsFile='$known' -o GlobalKnownHostsFile=/dev/null -o HostKeyAlias=github.com -o StrictHostKeyChecking=yes -o BatchMode=yes"

  local errors shown=0 answer
  errors=$(mktemp "${TMPDIR:-/tmp}/rota-planner.XXXXXX") || die "Could not make a temporary file."
  until GIT_SSH_COMMAND="$SSH_COMMAND" git ls-remote "$repo" HEAD >/dev/null 2>"$errors"; do
    if grep -q -i -e "Could not resolve" -e "Network is unreachable" -e "timed out" -e "Connection refused" "$errors"; then
      rm -f "$errors"
      die "GitHub could not be reached: is this computer connected to the internet?"
    fi
    if grep -q -i "Host key verification failed" "$errors"; then
      rm -f "$errors"
      die "The computer that answered as github.com could not prove it is GitHub, so nothing was downloaded.
Try again on another network, and tell whoever looks after the program."
    fi
    if [ "$shown" = 0 ]; then
      show_key_steps "$path" "$key.pub"
      shown=1
    else
      say "GitHub does not accept the key yet."
    fi
    [ -t 0 ] || { rm -f "$errors"; die "Run the installer again once the key is added."; }
    printf 'Press Enter once the key is added on GitHub (or type q to stop): '
    read -r answer || answer=q
    case "$answer" in [Qq]*) rm -f "$errors"; die "Stopped. Run the installer again once the key is added." ;; esac
  done
  rm -f "$errors"
}

computer_name() {
  local name=""
  [ "$SYSTEM" = mac ] && name=$(scutil --get ComputerName 2>/dev/null)
  [ -n "$name" ] || name=$(hostname 2>/dev/null)
  say "${name:-this computer}"
}

show_key_steps() {  # show_key_steps owner/name public-key-file
  local url="https://github.com/$1/settings/keys/new" copied=""
  if [ "$SYSTEM" = mac ]; then
    pbcopy <"$2" 2>/dev/null && copied=" (already copied: just paste it)"
  elif command -v wl-copy >/dev/null 2>&1 && [ -n "${WAYLAND_DISPLAY:-}" ]; then
    wl-copy <"$2" 2>/dev/null && copied=" (already copied: just paste it)"
  elif command -v xclip >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
    xclip -selection clipboard <"$2" 2>/dev/null && copied=" (already copied: just paste it)"
  fi
  say ""
  say "This computer needs permission to download Rota Planner from GitHub."
  say "It has its own key, which can only read the program, never change it."
  say "Add it once, from any computer where you are signed in to GitHub as the"
  say "owner of the repository:"
  say ""
  say "  1. Open $url"
  say "  2. Title: Rota Planner - $(computer_name)"
  say "  3. Key$copied:"
  say ""
  say "     $(cat "$2")"
  say ""
  say "  4. Leave \"Allow write access\" unticked and click \"Add key\"."
  say ""
}

# -- what Rota Planner needs ------------------------------------------------
#
# git (to download and update) and Python 3.9 or newer; for Rota Planner's own
# window also Tk. On Linux they come from the system's packages; on a Mac git
# comes with Apple's developer tools, and a Python with a good Tk from
# python.org. Each installation is shown and agreed to first; the person types
# their own password (sudo); this script never runs as root itself.

PACKAGER=""  # Linux: apt-get, dnf, pacman or zypper
PSF_SIGNER_PATTERN='Developer ID Installer: Python Software Foundation \(BMM5U3QVKW\)'

have_python() {
  have_tool python3 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null
}

find_packager() {
  [ -n "$PACKAGER" ] && return 0
  local tool
  for tool in apt-get dnf pacman zypper; do
    if command -v "$tool" >/dev/null 2>&1; then
      PACKAGER="$tool"
      return 0
    fi
  done
  return 1
}

package_name() {  # package_name git|python|tk: that package's name on this Linux
  case "$PACKAGER:$1" in
    *:git) say git ;;
    pacman:python) say python ;;
    *:python) say python3 ;;
    dnf:tk) say python3-tkinter ;;
    pacman:tk) say tk ;;
    *:tk) say python3-tk ;;
  esac
}

linux_install() {  # linux_install <why> <package>...: shown, agreed to, then installed with sudo
  local why="$1"
  shift
  find_packager || return 1
  [ -t 0 ] || [ "$YES" = 1 ] || return 1
  local command
  case "$PACKAGER" in
    apt-get) command="sudo apt-get install -y $*" ;;
    dnf) command="sudo dnf install -y $*" ;;
    pacman) command="sudo pacman -S --needed --noconfirm $*" ;;
    zypper) command="sudo zypper --non-interactive install $*" ;;
  esac
  say ""
  say "$why"
  say "  $command"
  ask_yes "Install now? Your password may be asked." || return 1
  # $command is split into words on purpose: the package names are fixed words from package_name.
  if ! $command; then
    [ "$PACKAGER" = apt-get ] || return 1
    say "Refreshing the list of packages, then trying again…"
    sudo apt-get update && $command || return 1
  fi
}

mac_developer_tools() {  # Apple's Command Line Developer Tools bring git (and a Python 3.9)
  xcode-select -p >/dev/null 2>&1 && return 0
  xcode-select --install >/dev/null 2>&1 || true
  [ -t 0 ] || return 1
  say ""
  say "A window offers to install Apple's \"Command Line Developer Tools\" (git comes with"
  say "them): click Install. This waits until they are installed (a few minutes)…"
  local waited=0
  until xcode-select -p >/dev/null 2>&1; do
    sleep 5
    waited=$((waited + 5))
    [ "$waited" -ge 3600 ] && return 1
  done
  say "The developer tools are installed."
}

ensure_tools() {  # git and Python 3.9+, installed when missing (with the person's agreement)
  have_tool git && have_python && return 0
  if [ "$SYSTEM" = mac ]; then
    mac_developer_tools || die "This Mac needs Apple's \"Command Line Developer Tools\" (they bring git and Python).
Install them (a window offers it, or run: xcode-select --install), then run this installer again."
  else
    local packages=""
    if find_packager; then
      have_tool git || packages="$(package_name git)"
      have_python || packages="${packages:+$packages }$(package_name python)"
    fi
    # shellcheck disable=SC2086 # the package names are separate words
    [ -n "$packages" ] && linux_install "Rota Planner needs git and Python 3. They can be installed with:" $packages \
      || die "This computer needs git and Python 3.9 or newer. Install them with the software manager
(on Ubuntu or Debian: sudo apt install git python3), then run this installer again."
  fi
  have_tool git || die "git is still missing: install it, then run this installer again."
  have_python || die "Rota Planner needs Python 3.9 or newer; this computer has $(python3 --version 2>&1)."
}

ensure_window() {  # ensure_window <folder>: Rota Planner's own window needs Tk: offered, never forced
  [ "${ROTA_INSTALL_WINDOW:-}" = no ] && return 0
  pick_python
  [ -n "$WINDOW_PYTHON" ] && return 0
  [ -t 0 ] || [ "$YES" = 1 ] || return 0
  local declined="$1/.git/rota-planner-no-window"  # inside .git: never part of the program
  if [ -f "$declined" ] && [ "$YES" != 1 ]; then
    return 0  # asked before and refused: the hint at the end says how, without asking again
  fi
  if [ "$SYSTEM" = mac ]; then
    mac_python || { : >"$declined" 2>/dev/null; return 0; }
  else
    linux_install "Rota Planner's own window (status, Start, Stop) needs Tk for Python. It can be installed with:" \
      "$(find_packager && package_name tk)" || { : >"$declined" 2>/dev/null; return 0; }
  fi
  rm -f -- "$declined"
  PICKED=0
  pick_python
}

mac_python() {  # the newest Python 3.13 from python.org, checked before it is installed
  say ""
  say "Rota Planner's own window needs Python from python.org (Apple's own Python draws"
  say "windows badly). It is free, about 70 MB, and does not change Apple's Python."
  ask_yes "Download it from python.org and install it? Your password will be asked." || return 1
  local listing version url="" folder ok
  local web="--proto =https --proto-redir =https"  # never anything but https, even after a redirect
  # shellcheck disable=SC2086
  listing=$(curl $web -fsSL https://www.python.org/ftp/python/) || { say "python.org could not be reached."; return 1; }
  for version in $(printf '%s\n' "$listing" | grep -o 'href="3\.13\.[0-9]*/"' | grep -o '3\.13\.[0-9]*' | sort -t. -k3 -n -r); do
    # The newest version that has its final Mac installer (a new folder may not have one yet).
    # shellcheck disable=SC2086
    if curl $web -fsIL "https://www.python.org/ftp/python/$version/python-$version-macos11.pkg" >/dev/null 2>&1; then
      url="https://www.python.org/ftp/python/$version/python-$version-macos11.pkg"
      break
    fi
  done
  [ -n "$url" ] || { say "No Python installer was found on python.org."; return 1; }
  folder=$(mktemp -d "${TMPDIR:-/tmp}/rota-planner-python.XXXXXX") || return 1
  say "Downloading Python $version…"
  # shellcheck disable=SC2086
  if ! curl $web -fL --progress-bar -o "$folder/python.pkg" "$url"; then
    rm -rf -- "$folder"
    say "The download failed."
    return 1
  fi
  # Checked, then installed, from a folder only the system can change (nothing can swap
  # the file between the check and the installation).
  local safe
  say "Your password is asked to check and install it."
  safe=$(sudo mktemp -d /tmp/rota-planner-python.XXXXXX) && sudo cp "$folder/python.pkg" "$safe/python.pkg" \
    || { rm -rf -- "$folder"; say "The installer could not be prepared."; return 1; }
  rm -rf -- "$folder"
  # Only a package whose own signature (the first certificate of the chain) is the Python
  # Software Foundation's, and that the Mac's own check (notarised by Apple) accepts.
  if ! sudo pkgutil --check-signature "$safe/python.pkg" 2>/dev/null | grep -qxE " *1\. $PSF_SIGNER_PATTERN" \
    || ! sudo spctl --assess --type install "$safe/python.pkg" >/dev/null 2>&1; then
    sudo rm -rf -- "$safe"
    say "The downloaded installer is not signed by the Python Software Foundation, so it was not used."
    return 1
  fi
  sudo installer -pkg "$safe/python.pkg" -target /
  ok=$?
  sudo rm -rf -- "$safe"
  [ "$ok" = 0 ]
}

# -- uninstall ----------------------------------------------------------------

leave_place() {  # leave_place <folder>: this computer's sealed files leave the server or shared folder
  have_python || return 1
  (cd "$1" && python3 -I -c 'import sys; sys.path.insert(0, "."); from rotaplanner.sync import main; sys.exit(main(["leave", "data"]))') >/dev/null 2>&1
}

ours() {  # ours <launcher file> <folder>: made by this installer for exactly this copy?
  grep -qxF "Path=$2" "$1" 2>/dev/null  # the whole line: /x/Rota is not /x/Rota Planner 2
}

OTHER_COPY=0  # set by remove_launchers: the icons belong to another installed copy

remove_launchers() {  # remove_launchers <folder>: only the icons made for this copy
  local name app share
  if [ "$LAUNCHERS" = mac ]; then
    for name in "Rota Planner" "Update Rota Planner"; do
      app="$HOME/Applications/$name.app"
      [ -d "$app" ] || continue
      grep -q "$BUNDLE_ID" "$app/Contents/Info.plist" 2>/dev/null || continue
      if grep -rqF "\"$1/" "$app/Contents/MacOS" "$app/Contents/Resources" 2>/dev/null; then  # "<folder>/…"
        rm -rf -- "$app"
      else
        OTHER_COPY=1
      fi
    done
  else
    share="${XDG_DATA_HOME:-$HOME/.local/share}"
    for name in rota-planner.desktop rota-planner-update.desktop; do
      [ -f "$share/applications/$name" ] || continue
      if ours "$share/applications/$name" "$1"; then
        rm -f -- "$share/applications/$name"
      else
        OTHER_COPY=1
      fi
    done
    # The icon picture is shared by the copy the icons open: only removed with them.
    [ "$OTHER_COPY" = 1 ] || rm -f -- "$share/icons/rota-planner.png"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$share/applications" 2>/dev/null
  fi
  return 0
}

uninstall() {  # uninstall <folder>
  local folder="$1"
  installed "$folder" || die "Rota Planner is not installed in $folder, so nothing was removed."
  # The real folder, not a link to it: removing a link would remove nothing.
  folder=$(cd "$folder" 2>/dev/null && pwd -P) || die "$1 cannot be opened. Nothing was removed."
  case "$folder" in "$(cd "$HOME" && pwd -P)" | / | "") die "Refusing to remove $folder." ;; esac
  if [ -L "$folder/data" ] || [ -L "$folder/exports" ]; then
    die "The data folder of $folder is a link to another place. Move it or delete it by hand
first, then uninstall again. Nothing was removed."
  fi
  # A copy with work of its own (changed or new program files, changes not on GitHub) is
  # someone's work in progress, not an installation to throw away. update refuses it too.
  if [ -n "$(git -C "$folder" status --porcelain 2>/dev/null)" ] \
    || ! git -C "$folder" rev-parse '@{u}' >/dev/null 2>&1 \
    || [ "$(git -C "$folder" rev-list --count '@{u}..HEAD' 2>/dev/null)" != 0 ]; then
    die "$folder has program changes of its own (or is not a copy installed by this script),
so it was not removed. Nothing was removed."
  fi
  if running "$folder/data"; then
    die "Rota Planner is open. Close its window first, then uninstall again. Nothing was removed."
  fi
  local data="$folder/data" exports="$folder/exports" has_data=0 keep="" sync_file=""
  local name
  for name in catalog.json people.json settings.json templates.json months backups access.json sync.json mail.json; do
    [ -e "$data/$name" ] && has_data=1
  done
  [ -n "$(ls -A "$exports" 2>/dev/null)" ] && has_data=1
  [ -f "$data/sync.json" ] && sync_file=paired  # this computer takes part with other computers

  say "Uninstalling Rota Planner removes:"
  say "  - the program: $folder"
  say "  - its icons, \"Rota Planner\" and \"Update Rota Planner\""
  [ -f "$HOME/.ssh/$KEY_NAME" ] && say "  - this computer's download key ($HOME/.ssh/$KEY_NAME)"
  if [ "$has_data" = 1 ]; then
    if [ "$DELETE_DATA" = 1 ]; then
      say "  - ALL ITS DATA: people, rotas, settings, backups and saved documents."
      [ -n "$sync_file" ] && say "  - this computer's sealed file at the meeting place (the server or shared folder)"
      say "    This cannot be undone."
    else
      keep="$HOME/Rota Planner data (kept $(date +%Y-%m-%d))"
      local n=2 base="$keep"
      while [ -e "$keep" ]; do keep="$base $n"; n=$((n + 1)); done
      say ""
      say "Your data (people, rotas, settings, backups, saved documents) is kept: it is moved to"
      say "  $keep"
      say "To bring it back later: install again, then use --from \"$keep\"."
      say "(To delete the data as well, run the uninstall with --delete-data.)"
    fi
  fi
  say ""
  if [ "$YES" != 1 ]; then
    [ -t 0 ] || die "Run this in a Terminal to confirm (or add --yes). Nothing was removed."
    local answer
    if [ "$DELETE_DATA" = 1 ] && [ "$has_data" = 1 ]; then
      printf 'Type DELETE to remove Rota Planner and all its data: '
      read -r answer || answer=""
      [ "$answer" = DELETE ] || die "Nothing was removed."
    else
      printf 'Uninstall Rota Planner? [y/N] '
      read -r answer || answer=""
      case "$answer" in [Yy]*) ;; *) die "Nothing was removed." ;; esac
    fi
  fi

  cd "$HOME" || cd / || die "Could not leave the program's folder. Nothing was removed."
  if [ "$has_data" = 1 ] && [ -n "$keep" ]; then
    # The data first: if it cannot be put aside, nothing else is removed.
    (umask 077 && mkdir "$keep") || die "Could not make $keep. Nothing was removed."
    chmod 700 "$keep"
    if [ -d "$data" ] && ! mv "$data" "$keep/data"; then
      die "Could not move the data to $keep. Nothing was removed."
    fi
    if [ -d "$exports" ] && ! mv "$exports" "$keep/exports"; then
      die "Could not move the saved documents to $keep. The data is there; the program was not removed."
    fi
  elif [ "$DELETE_DATA" = 1 ] && [ -n "$sync_file" ]; then
    leave_place "$folder" || say "This computer's file could not be removed from the meeting place (no connection?):
on another computer, Settings › Other computers › Forget this computer removes it."
  fi
  remove_launchers "$folder"
  local had_key=0
  [ -f "$HOME/.ssh/$KEY_NAME" ] && had_key=1  # only made when the repository is private
  if [ "$OTHER_COPY" = 1 ]; then
    say "Another copy of Rota Planner is installed (the icons open it): its icons are kept for it."
    had_key=0
  else
    rm -f -- "$HOME/.ssh/$KEY_NAME" "$HOME/.ssh/$KEY_NAME.pub" "$HOME/.ssh/rota-planner-known_hosts"
  fi
  rm -rf -- "$folder"
  [ ! -e "$folder" ] || die "Some files of $folder could not be removed: delete the folder by hand."
  say "Rota Planner is uninstalled."
  [ -n "$keep" ] && say "Your data is in: $keep"
  if [ -n "$sync_file" ] && [ -n "$keep" ]; then
    say "This computer's sealed file stays at the meeting place. If it will not be used with"
    say "Rota Planner again: on another computer, Settings › Other computers › Forget this computer."
  fi
  say ""
  if [ "$had_key" = 1 ]; then
    say "On GitHub, you can also remove this computer's key: the repository › Settings ›"
    say "Deploy keys › \"Rota Planner - $(computer_name)\"."
  fi
  say "git, Python and Tk stay installed: other programs may use them."
  finish 0
}

# -- install --------------------------------------------------------------

installed() {  # installed <folder>
  [ -d "$1/.git" ] && [ -f "$1/run.py" ] && [ -d "$1/rotaplanner" ]
}

install() {  # install <folder> <repo> <from>
  local folder="$1" repo="$2" from="$3"
  if [ -e "$folder" ] && [ -n "$(ls -A "$folder" 2>/dev/null)" ]; then
    die "The folder $folder already exists and is not a Rota Planner installed by this script.
Move it away, or choose another folder with --folder."
  fi
  say "Installing Rota Planner in $folder"
  local open_repo
  open_repo=$(public_address "$repo")
  if [ -n "$open_repo" ]; then
    repo="$open_repo"  # a public repository: anyone may read it, no key to make
  else
    prepare_key "$repo"
  fi
  say "Downloading the program…"
  local existed=0
  [ -e "$folder" ] && existed=1
  local how=""
  [ -n "$SSH_COMMAND" ] && how="core.sshCommand=$SSH_COMMAND"
  if ! git ${how:+-c "$how"} clone --quiet "$repo" "$folder"; then
    [ "$existed" = 0 ] && [ -d "$folder" ] && rm -rf -- "$folder"
    die "The download failed (see the message above). Nothing was installed."
  fi
  if [ -n "$SSH_COMMAND" ]; then
    git -C "$folder" config core.sshCommand "$SSH_COMMAND"
  fi
  git -C "$folder" config pull.ff only

  if [ -z "$from" ] && [ -t 0 ]; then
    say ""
    say "Is Rota Planner already used on this computer from another folder (for"
    say "example a copy unpacked from a zip)? Then its data can be brought here."
    printf 'Drag that folder here and press Enter, or just press Enter to start empty: '
    read -r from || from=""
  fi
  from=$(clean_path "$from")

  ensure_window "$folder"
  make_launchers "$folder"
  if [ -n "$from" ] && ! bring_data "$from" "$folder/data"; then
    say "Rota Planner is installed, without data. To bring the data later, close both copies and run:"
    say "  bash \"$folder/install-rota-planner.sh\" --from \"OLD FOLDER\""
  fi
  window_hint
  say ""
  say "Rota Planner is installed."
  say "  Start it:   $(start_hint)"
  say "  Update it:  $(update_hint)"
  say ""
  if [ -t 0 ] && [ "$YES" != 1 ] && ask_yes "Start Rota Planner now?"; then  # never by --yes
    cd "$folder" || finish 1
    exec python3 run.py
  fi
  finish 0
}

clean_path() {  # a folder dragged into the Terminal: quotes, "\ " and spaces around it
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  case "$value" in
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
  esac
  value=$(printf '%s' "$value" | sed 's/\\\(.\)/\1/g')
  case "$value" in "~/"*) value="$HOME/${value#"~/"}" ;; esac
  say "$value"
}

bring_data() {  # bring_data <old copy or its data folder> <new data folder>: says why when it cannot
  local source="$1" target="$2"
  [ -f "$source/data/catalog.json" ] && source="$source/data"
  if [ ! -f "$source/catalog.json" ]; then
    say "No Rota Planner data in $1 (no catalog.json there or in its data folder)."
    return 1
  fi
  local name
  for name in catalog.json people.json settings.json access.json sync.json mail.json months; do
    if [ -e "$target/$name" ]; then
      say "This copy already has data or settings, so nothing was brought from $1."
      return 1
    fi
  done
  if running "$source"; then
    say "The copy in $1 is open: close it (its Terminal window) first."
    return 1
  fi
  mkdir -p "$target" || { say "Could not make $target."; return 1; }
  # Residents' details: readable by this user only, whatever the old copy allowed.
  if ! (umask 077 && cp -R "$source/." "$target/"); then
    say "Copying the data failed."
    return 1
  fi
  chmod -R go-rwx "$target" && chmod 700 "$target"
  rm -f "$target/.running"
  say "The data was copied from $source."
  say "From now on use only this new copy: the old one is left as it was, but it"
  say "has the same settings (password, second computer), so do not open it again."
  say "Delete it once all is well."
}

# -- update ---------------------------------------------------------------

running() {  # running <data folder>: is Rota Planner using it right now?
  local marker="$1/.running" line pid
  [ -f "$marker" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do  # one line per running copy
    pid=$(printf '%s' "$line" | tr -cd '0-9')
    [ -n "$pid" ] || continue
    kill -0 "$pid" 2>/dev/null || continue
    ps -p "$pid" -o command= 2>/dev/null | grep -q -e "run.py" -e "rotaplanner" && return 0
  done <"$marker"
  return 1
}

update() {  # update <folder> <from>
  local folder="$1" from="$2"
  if running "$folder/data"; then
    die "Rota Planner is open. Close it first (in its Terminal window press Ctrl+C,
or close the window), then update again."
  fi
  if [ -n "$from" ]; then
    bring_data "$(clean_path "$from")" "$folder/data" || die "Nothing was changed."
  fi

  local upstream
  upstream=$(git -C "$folder" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) \
    || die "This copy does not follow a branch on GitHub, so it cannot be updated by this script."
  say "Looking for a new version…"
  local answer
  if ! answer=$(git -C "$folder" fetch --quiet 2>&1); then
    say "$answer" | sed 's/^/  /'
    die "GitHub could not be reached (is the internet connected?), or it no longer accepts
this computer's key (GitHub › the repository › Settings › Deploy keys). Nothing was changed."
  fi

  local here there
  here=$(git -C "$folder" rev-parse HEAD) || die "This copy's history cannot be read."
  there=$(git -C "$folder" rev-parse '@{u}') || die "The new version cannot be read."
  if [ "$here" = "$there" ]; then
    make_launchers "$folder" >/dev/null 2>&1  # icons as this version makes them
    say "Rota Planner is up to date (version of $(git -C "$folder" log -1 --format=%cd --date=short))."
    window_hint
    finish 0
  fi
  if [ -n "$(git -C "$folder" status --porcelain --untracked-files=no)" ]; then
    say "Files of the program were changed on this computer:"
    git -C "$folder" status --short --untracked-files=no | sed 's/^/  /'
    die "So it was not updated (your changes are kept). Ask whoever looks after the program."
  fi
  if ! git -C "$folder" merge-base --is-ancestor HEAD '@{u}'; then
    die "This copy has changes of its own that are not on GitHub, so it was not updated.
Ask whoever looks after the program."
  fi

  # data/, data-demo/ and exports/ are ignored by git, and git treats ignored
  # files as disposable: a version that (by mistake) contains anything there
  # could replace or delete the data. Never apply one.
  if [ -n "$(git -C "$folder" ls-tree -r --name-only '@{u}' -- data data-demo exports)" ]; then
    die "The new version contains files in the data folder, which could replace your data,
so it was not applied. Tell whoever looks after the program."
  fi

  local count
  count=$(git -C "$folder" rev-list --no-merges --count HEAD..'@{u}')
  say ""
  say "What is new ($count change$( [ "$count" = 1 ] || printf s )):"
  git -C "$folder" log --no-merges --format='  - %s' -n 15 HEAD..'@{u}'
  [ "$count" -gt 15 ] && say "  … and $((count - 15)) more"
  say ""

  local copy
  copy=$(safety_copy "$folder") || die "The copy of the data could not be made, so nothing was updated."
  [ -n "$copy" ] && say "Your data was copied first to data/backups/$copy"

  if ! git -C "$folder" merge --quiet --ff-only "$upstream"; then
    die "The update could not be applied; the program was not changed."
  fi
  if ! works "$folder"; then
    git -C "$folder" reset --quiet --hard "$here"
    die "The new version does not start on this computer (see above), so the previous
version was put back. Tell whoever looks after the program."
  fi
  make_launchers "$folder" >/dev/null 2>&1
  say "Rota Planner is up to date (version of $(git -C "$folder" log -1 --format=%cd --date=short))."
  window_hint
  if [ -f "$folder/data/sync.json" ]; then
    say ""
    say "This computer is paired with a second computer: update that one today too."
  fi
  finish 0
}

safety_copy() {  # a copy of all the data in data/backups/, made by the version in place now
  python3 -I - "$1" <<'PYTHON'
import os
import sys
from pathlib import Path

os.umask(0o077)
program = Path(sys.argv[1])
sys.path.insert(0, str(program))
from rotaplanner.store import Store
from rotaplanner.transfer import keep_safety_copy

store = Store(program / "data")
print("" if store.is_empty() else keep_safety_copy(store, "before-update"))
PYTHON
}

works() {  # does the new version load on this computer's Python?
  python3 -I -m compileall -q "$1/rotaplanner" || return 1  # -q: prints only the errors
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import rotaplanner.app, rotaplanner.server, rotaplanner.__main__' "$1"
}

# -- icons ----------------------------------------------------------------

start_hint() {
  if [ "$LAUNCHERS" = mac ]; then say "\"Rota Planner\" in Applications (or Spotlight)"
  else say "\"Rota Planner\" in the applications menu"; fi
}

update_hint() {
  if [ "$LAUNCHERS" = mac ]; then say "\"Update Rota Planner\" in Applications"
  else say "\"Update Rota Planner\" in the applications menu"; fi
}

WINDOW_PYTHON=""  # a Python that can show Rota Planner's window; empty: Terminal launchers
PICKED=0

pick_python() {
  [ "$PICKED" = 1 ] && return 0
  PICKED=1
  [ "${ROTA_INSTALL_WINDOW:-}" = no ] && return 0  # (the tests try both kinds)
  [ "${ROTA_INSTALL_PRETEND_NO_TK:-}" = 1 ] && return 0  # (the tests: as on a computer without Tk)
  local candidate version
  set --
  if [ "$SYSTEM" = mac ]; then
    # Apple's own Python has an old Tk that draws windows badly; python.org's has a good one.
    for version in 3.14 3.13 3.12 3.11 3.10 3.9; do
      set -- "$@" "/Library/Frameworks/Python.framework/Versions/$version/bin/python3"
    done
    set -- "$@" /opt/homebrew/bin/python3 /usr/local/bin/python3
  fi
  candidate=$(command -v python3 2>/dev/null) && set -- "$@" "$candidate"
  for candidate in "$@"; do
    [ -x "$candidate" ] || continue
    case "$candidate" in *[!A-Za-z0-9/._+-]*) continue ;; esac  # it goes into the launchers
    owned_safely "$candidate" || continue
    if "$candidate" -I -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) and tkinter.TkVersion >= 8.6 else 1)' >/dev/null 2>&1; then
      WINDOW_PYTHON="$candidate"
      return 0
    fi
  done
  return 0
}

owned_safely() {  # the icons run it directly: only a Python that the system or this person owns
  local owner
  owner=$(ls -lnL "$1" 2>/dev/null | awk '{print $3}')
  [ "$owner" = 0 ] || [ "$owner" = "$(id -u)" ]
}

window_hint() {  # said when the icons cannot open the window
  [ -n "$WINDOW_PYTHON" ] && return 0
  [ "${ROTA_INSTALL_WINDOW:-}" = no ] && return 0
  say ""
  if [ "$SYSTEM" = mac ]; then
    say "Rota Planner will open in a Terminal window. For its own window (status, Start, Stop),"
    say "install Python from https://www.python.org/downloads/ then run Update Rota Planner."
  else
    say "Rota Planner will open in a terminal. For its own window (status, Start, Stop), install"
    say "the package python3-tk (sudo apt install python3-tk), then run Update Rota Planner."
  fi
}

paint() {  # paint <folder> icon <file.png> <size> | iconset <folder>: drawn by the program (paint.py)
  local folder="$1"
  shift
  (cd "$folder" && python3 -I -c 'import sys; sys.path.insert(0, "."); from rotaplanner.paint import main; sys.exit(main(sys.argv[1:]))' "$@") >/dev/null 2>&1
}

make_launchers() {  # make_launchers <folder>
  safe_path "$1"
  chmod +x "$1/install-rota-planner.sh" "$1/rota-planner.sh" "$1/Rota Planner.command" 2>/dev/null
  pick_python
  local start
  if [ "$LAUNCHERS" = mac ]; then
    if [ -n "$WINDOW_PYTHON" ]; then
      start=$(printf 'exec "%s" "%s/run.py" --window' "$WINDOW_PYTHON" "$1")
    else
      # A Terminal window, like the program's own start file: closing it stops Rota Planner.
      start=$(printf 'exec /usr/bin/open "%s/Rota Planner.command"' "$1")
    fi
    mac_app "Rota Planner" "start" "$start" "$1" \
      || say "Note: $HOME/Applications/Rota Planner.app was not made by this installer and was left as it is."
    mac_app "Update Rota Planner" "update" "" "$1" \
      || say "Note: $HOME/Applications/Update Rota Planner.app was not made by this installer and was left as it is."
  else
    local share="${XDG_DATA_HOME:-$HOME/.local/share}"
    local apps="$share/applications" icon="$share/icons/rota-planner.png"
    mkdir -p "$apps" "$share/icons" || return 1
    paint "$1" icon "$icon" 256 || icon="x-office-calendar"
    if [ -n "$WINDOW_PYTHON" ]; then
      desktop_entry "$apps/rota-planner.desktop" "Rota Planner" "Prepare the monthly service rota" \
        "\"$WINDOW_PYTHON\" \"$1/run.py\" --window" "$1" false "$icon"
    else
      desktop_entry "$apps/rota-planner.desktop" "Rota Planner" "Prepare the monthly service rota" \
        "\"$1/rota-planner.sh\"" "$1" true "$icon"
    fi
    desktop_entry "$apps/rota-planner-update.desktop" "Update Rota Planner" \
      "Bring Rota Planner up to date (your data is kept)" \
      "bash \"$1/install-rota-planner.sh\" --update --wait --folder \"$1\"" "$1" true "$icon"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$apps" 2>/dev/null
  fi
  return 0
}

desktop_entry() {  # desktop_entry <file> <name> <comment> <command> <folder> <terminal> <icon>
  cat >"$1" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Comment=$3
Exec=$4
Path=$5
Terminal=$6
Icon=$7
StartupWMClass=RotaPlanner
Categories=Office;
EOF
}

mac_app() {  # mac_app <name> <role> <start command, or empty for the updater> <folder>
  local app="$HOME/Applications/$1.app"
  mkdir -p "$HOME/Applications" || return 1
  if [ -e "$app" ]; then
    # Only ever replace an app this script made.
    grep -q "$BUNDLE_ID" "$app/Contents/Info.plist" 2>/dev/null || return 1
    rm -rf -- "$app"
  fi
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" || return 1
  cat >"$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID.$2</string>
  <key>CFBundleName</key><string>$1</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF
  mac_icon "$4" "$app/Contents/Resources/icon.icns"
  local start="$3"
  if [ -z "$start" ]; then
    # The update runs in a Terminal window, so what it says can be read.
    local command="$app/Contents/Resources/Update Rota Planner.command"
    printf '#!/bin/sh\nexec /bin/bash "%s/install-rota-planner.sh" --update --wait --folder "%s"\n' "$4" "$4" >"$command"
    chmod +x "$command"
    start=$(printf 'exec /usr/bin/open "%s"' "$command")
  fi
  printf '#!/bin/sh\n%s\n' "$start" >"$app/Contents/MacOS/launch"
  chmod +x "$app/Contents/MacOS/launch"
  touch "$app"
}

mac_icon() {  # mac_icon <folder> <icon.icns>: best effort (iconutil comes with macOS)
  command -v iconutil >/dev/null 2>&1 || return 0
  local set size
  set=$(mktemp -d "${TMPDIR:-/tmp}/rota-planner-icon.XXXXXX") || return 0
  mkdir "$set/icon.iconset"
  paint "$1" iconset "$set/icon.iconset" && iconutil -c icns "$set/icon.iconset" -o "$2" >/dev/null 2>&1
  rm -rf -- "$set"
}

# -- main -----------------------------------------------------------------

main() {
  local folder="" repo="$DEFAULT_REPO" from="" only_update=0 remove=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --update) only_update=1 ;;
      --uninstall) remove=1 ;;
      --delete-data) DELETE_DATA=1 ;;
      --yes) YES=1 ;;
      --wait) WAIT=1 ;;
      --folder) [ $# -ge 2 ] || die "--folder needs a folder."; folder="$2"; shift ;;
      --from) [ $# -ge 2 ] || die "--from needs a folder."; from="$2"; shift ;;
      --repo) [ $# -ge 2 ] || die "--repo needs an address."; repo="$2"; shift ;;
      -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
      *) die "Unknown option: $1 (see --help)" ;;
    esac
    shift
  done
  check_system
  if [ "$remove" = 1 ] && [ -n "$from" ]; then
    die "--uninstall and --from cannot go together."
  fi
  [ "$remove" = 1 ] || ensure_tools
  # The settings saved with the copy (core.sshCommand) decide how to reach GitHub,
  # not whatever the Terminal happens to have.
  unset GIT_SSH_COMMAND GIT_SSH GIT_DIR GIT_WORK_TREE GIT_CONFIG_PARAMETERS
  case "$repo" in -*) die "Not a repository address: $repo" ;; esac

  if [ -z "$folder" ]; then
    # The copy of this script inside an installed Rota Planner updates that one.
    local here
    here=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
    if [ -n "$here" ] && installed "$here"; then folder="$here"; else folder="$HOME/Rota Planner"; fi
  fi
  folder=$(clean_path "$folder")
  case "$folder" in /*) ;; *) folder="$(pwd)/$folder" ;; esac
  safe_path "$folder"

  if [ "$remove" = 1 ]; then
    uninstall "$folder"
  elif installed "$folder"; then
    if [ "$only_update" != 1 ] && ! running "$folder/data"; then
      ensure_window "$folder"  # run by hand: offer the window if it is missing
    fi
    update "$folder" "$from"
  elif [ "$only_update" = 1 ]; then
    die "Rota Planner is not installed in $folder."
  else
    install "$folder" "$repo" "$from"
  fi
}

# All of the script is read before anything runs: an update may replace this
# very file while it is running.
main "$@"; exit $?
