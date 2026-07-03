#!/usr/bin/env bash
# remove-docker-nvidia.sh
# Safely remove Docker (Desktop, Engine, rootless) and NVIDIA Container Toolkit
# Usage: sudo ./remove-docker-nvidia.sh [--purge] [--yes] [--remove-pass]
#   --purge        Remove docker/nvidia data directories (destructive)
#   --yes          Non-interactive, assume yes to confirmations
#   --remove-pass  Also remove 'pass' and ~/.password-store (destructive)

set -euo pipefail

DRY_RUN=0
ASSUME_YES=0
PURGE=0
REMOVE_PASS=0
RUN_ALL=0

# parse args
while [ "$#" -gt 0 ]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --remove-pass) REMOVE_PASS=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --all) RUN_ALL=1; shift ;;
    -h|--help)
      cat <<'USAGE'
remove-docker-nvidia.sh - remove Docker and NVIDIA container toolkit safely

Usage: sudo ./remove-docker-nvidia.sh [--purge] [--yes] [--remove-pass]

Options:
  --purge        Remove data directories (e.g. /var/lib/docker, ~/.docker). Destructive.
  --yes          Non-interactive; proceed without confirmation prompts. (CLI only; use with care)
  --remove-pass  Also remove 'pass' package and ~/.password-store (destructive)
  --dry-run      Print actions without executing them.
  -h, --help     Show this help
  --all          Run ALL steps non-interactively (packages -> repos -> purge if set -> pass if set -> final checks)
USAGE
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

RUN() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "+ $*"
  else
    echo "+ $*" >&2
    eval "$@"
  fi
}

confirm() {
  if [ "$ASSUME_YES" -eq 1 ]; then
    return 0
  fi
  read -r -p "$1 [y/N]: " ans
  case "${ans,,}" in
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

# Detect distro/package manager
detect_pm() {
  if command -v apt-get >/dev/null 2>&1; then
    PM=apt
  elif command -v dnf >/dev/null 2>&1; then
    PM=dnf
  elif command -v yum >/dev/null 2>&1; then
    PM=yum
  elif command -v pacman >/dev/null 2>&1; then
    PM=pacman
  elif command -v zypper >/dev/null 2>&1; then
    PM=zypper
  elif command -v apk >/dev/null 2>&1; then
    PM=apk
  else
    PM=unknown
  fi
}

# If running as root, avoid calling sudo for each command. Use $SUDO variable.
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# Package check helpers
is_installed() {
  pkg="$1"
  case "$PM" in
    apt) dpkg -s "$pkg" >/dev/null 2>&1 ;;
    dnf|yum|zypper) rpm -q "$pkg" >/dev/null 2>&1 ;;
    pacman) pacman -Qi "$pkg" >/dev/null 2>&1 ;;
    apk) apk info | grep -x "$pkg" >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

remove_packages() {
  to_remove=(
    docker-ce docker-ce-cli docker-ce-rootless-extras docker-desktop docker-desktop-cli 
    docker docker-engine docker.io moby-engine containerd.io containerd runc
    docker-compose-plugin docker-buildx-plugin docker-compose
  )
  nvidia=(nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container1 libnvidia-container-tools)

  echo "Detected package manager: $PM"

  case "$PM" in
    apt)
      pkglist=()
      for p in "${to_remove[@]}"; do
        if dpkg -s "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      for p in "${nvidia[@]}"; do
        if dpkg -s "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      if [ ${#pkglist[@]} -gt 0 ]; then
        echo "Packages to remove: ${pkglist[*]}"
        if confirm "Proceed to apt purge these packages?"; then
          RUN $SUDO apt-get purge -y "${pkglist[@]}"
          RUN $SUDO apt-get autoremove -y
          RUN $SUDO apt-get update || true
        fi
      else
        echo "No matching Docker/NVIDIA packages found for apt."
      fi
      ;;
    dnf|yum)
      pkglist=()
      for p in "${to_remove[@]}"; do
        if rpm -q "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      for p in "${nvidia[@]}"; do
        if rpm -q "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      if [ ${#pkglist[@]} -gt 0 ]; then
        echo "Packages to remove: ${pkglist[*]}"
        if confirm "Proceed to remove these packages with $PM?"; then
          RUN $SUDO $PM remove -y "${pkglist[@]}"
          RUN $SUDO $PM autoremove -y || true
        fi
      else
        echo "No matching Docker/NVIDIA packages found for $PM."
      fi
      ;;
    pacman)
      pkglist=()
      for p in "${to_remove[@]}"; do
        if pacman -Qi "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      for p in "${nvidia[@]}"; do
        if pacman -Qi "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      if [ ${#pkglist[@]} -gt 0 ]; then
        echo "Packages to remove: ${pkglist[*]}"
        if confirm "Proceed to remove these packages with pacman?"; then
          RUN $SUDO pacman -Rs --noconfirm "${pkglist[@]}"
        fi
      else
        echo "No matching Docker/NVIDIA packages found for pacman."
      fi
      ;;
    zypper)
      pkglist=()
      for p in "${to_remove[@]}"; do
        if rpm -q "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      for p in "${nvidia[@]}"; do
        if rpm -q "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      if [ ${#pkglist[@]} -gt 0 ]; then
        echo "Packages to remove: ${pkglist[*]}"
        if confirm "Proceed to remove these packages with zypper?"; then
          RUN $SUDO zypper rm -y "${pkglist[@]}"
        fi
      else
        echo "No matching Docker/NVIDIA packages found for zypper."
      fi
      ;;
    apk)
      pkglist=()
      for p in "${to_remove[@]}"; do
        if apk info | grep -x "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      for p in "${nvidia[@]}"; do
        if apk info | grep -x "$p" >/dev/null 2>&1; then pkglist+=("$p"); fi
      done
      if [ ${#pkglist[@]} -gt 0 ]; then
        echo "Packages to remove: ${pkglist[*]}"
        if confirm "Proceed to remove these packages with apk?"; then
          RUN $SUDO apk del "${pkglist[@]}"
        fi
      else
        echo "No matching Docker/NVIDIA packages found for apk."
      fi
      ;;
    *) echo "Unsupported or unknown package manager: $PM" >&2; return 1 ;;
  esac
}

remove_repos_and_files() {
  echo "Cleaning up repo files, keyrings, and config..."
  # Repos - remove Docker repos and the NVIDIA Container Toolkit repo only.
  # NOTE: We intentionally preserve system NVIDIA driver repos (e.g., rpmfusion-nonfree-nvidia-driver.repo).

  # APT: old .list format + new DEB822 .sources format
  RUN $SUDO rm -f /etc/apt/sources.list.d/docker*.list /etc/apt/sources.list.d/docker*.sources || true
  RUN $SUDO rm -f /etc/apt/sources.list.d/nvidia-container-toolkit*.list /etc/apt/sources.list.d/nvidia-container-toolkit*.sources || true

  # APT keyrings (modern installs store GPG keys here)
  RUN $SUDO rm -f /etc/apt/keyrings/docker.asc /etc/apt/keyrings/docker.gpg || true
  RUN $SUDO rm -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg || true

  # DNF/YUM repos
  RUN $SUDO rm -f /etc/yum.repos.d/docker*.repo /etc/yum.repos.d/nvidia-container-toolkit.repo || true

  # Zypper repos (if added via zypper ar)
  $SUDO zypper rr nvidia-container-toolkit 2>/dev/null || true

  # Remove docker group if empty
  if getent group docker >/dev/null 2>&1; then
    docker_users=$(getent group docker | awk -F: '{print $4}')
    if [ -z "$docker_users" ]; then
      if confirm "Docker group is empty. Remove group 'docker'?"; then
        RUN $SUDO groupdel docker || true
      fi
    else
      echo "Docker group has users: $docker_users; not removing the group."
    fi
  fi

  # Stop & disable services
  echo "Stopping Docker services (system & user), Docker Desktop if present..."
  RUN $SUDO systemctl stop docker || true
  RUN $SUDO systemctl disable docker || true
  RUN $SUDO systemctl stop containerd || true
  RUN $SUDO systemctl disable containerd || true

  RUN systemctl --user stop docker-desktop >/dev/null 2>&1 || true
  RUN systemctl --user disable docker-desktop >/dev/null 2>&1 || true

  # Rootless user service
  if [ -f "$HOME/.config/systemd/user/docker.service" ]; then
    if confirm "Remove rootless user docker service and unit file?"; then
      RUN systemctl --user disable --now docker.service || true
      RUN rm -f "$HOME/.config/systemd/user/docker.service"
    fi
  fi
}

purge_data() {
  echo "Purging Docker / NVIDIA data (destructive)"
  candidates=(/var/lib/docker /var/lib/containerd /etc/docker /opt/docker-desktop)
  for d in "${candidates[@]}"; do
    if [ -e "$d" ]; then
      if confirm "Remove $d ?"; then
        RUN $SUDO rm -rf "$d"
      fi
    fi
  done
  # user data
  if [ -d "$HOME/.docker" ]; then
    if confirm "Remove $HOME/.docker and $HOME/.local/share/docker ?"; then
      RUN rm -rf "$HOME/.docker" "$HOME/.local/share/docker" "$HOME/.local/lib/docker" || true
    fi
  fi
}

remove_passstore() {
  if [ $REMOVE_PASS -eq 1 ]; then
    echo "Removing pass package and ~/.password-store"
    case "$PM" in
      apt) RUN $SUDO apt-get purge -y pass || true ;;
      dnf|yum) RUN $SUDO $PM remove -y pass || true ;;
      pacman) RUN $SUDO pacman -Rs --noconfirm pass || true ;;
      zypper) RUN $SUDO zypper rm -y pass || true ;;
      apk) RUN $SUDO apk del pass || true ;;
      *) echo "Package manager unknown; please remove 'pass' manually if desired" ;;
    esac
    if [ -d "$HOME/.password-store" ]; then
      if confirm "Also remove $HOME/.password-store (contains encrypted credentials)?"; then
        RUN rm -rf "$HOME/.password-store"
      fi
      # Also remove Docker credential helper store if present
      if [ -d "$HOME/.password-store/docker-credential-helpers" ]; then
        if confirm "Remove $HOME/.password-store/docker-credential-helpers (docker credential helpers)?"; then
          RUN rm -rf "$HOME/.password-store/docker-credential-helpers"
        fi
      fi
    fi
  fi
}

final_checks() {
  echo "Final verification: listing remaining docker/nvidia packages and units"
  RUN rpm -qa | grep -Ei 'docker|docker-desktop|containerd|nvidia-container|libnvidia' || true
  RUN dpkg -l 2>/dev/null | grep -Ei 'docker|containerd|nvidia-container' || true
  RUN systemctl --user list-units --type=service | grep -Ei 'docker|desktop' || true
  echo "Done. If you removed packages, consider rebooting or logging out/in to clear groups/sockets."
}

show_status() {
  echo "\n=== Current settings ==="
  echo "Package manager: $PM"
  echo "Assume yes: $ASSUME_YES"
  echo "Dry run: $DRY_RUN"
  echo "Purge on run: $PURGE"
  echo "Remove pass: $REMOVE_PASS"
  echo "========================\n"
}

pause() {
  # Read from the controlling tty to avoid stdin issues when run via sudo/non-interactive
  read -r -p "Press Enter to continue..." _dummy </dev/tty || true
}

main_menu() {
  detect_pm
  echo "Detected package manager: $PM"

  while true; do
    cat <<'MENU'

Select an action:
  1) Remove Docker/NVIDIA packages
  2) Remove repo files, stop/disable services, clean units
  3) Purge Docker/NVIDIA data directories (destructive)
  4) Remove 'pass' and ~/.password-store (contains credentials)
  5) Final verification checks
  a) Run ALL steps above sequentially (with prompts)
  b) Run ALL steps non-interactively (auto-yes, includes purge + pass)
  d) Toggle dry-run mode
  s) Show current status
  q) Quit
MENU

    read -r -p "Choice: " choice
    case "${choice,,}" in
      1)
        remove_packages
        pause
        ;;
      2)
        remove_repos_and_files
        pause
        ;;
      3)
        if confirm "This will remove data directories (destructive). Continue?"; then
          purge_data
        else
          echo "Purge skipped."
        fi
        pause
        ;;
      4)
        if confirm "Remove 'pass' package and ~/.password-store? (encrypted credentials will be deleted)"; then
          REMOVE_PASS=1
          remove_passstore
        else
          echo "Pass removal skipped."
        fi
        pause
        ;;
      5)
        final_checks
        pause
        ;;
      a)
        if confirm "Run ALL steps (packages -> repos -> purge optional -> pass -> final checks)?"; then
          remove_packages
          remove_repos_and_files
          if confirm "Purge data directories as part of ALL?"; then
            purge_data
          fi
          if confirm "Remove pass and credential helpers as part of ALL?"; then
            REMOVE_PASS=1
            remove_passstore
          fi
          final_checks
        else
          echo "ALL aborted."
        fi
        pause
        ;;
      b)
        echo "Running ALL steps with --yes (non-interactive, includes purge + pass removal)..."
        local PREV_YES=$ASSUME_YES
        ASSUME_YES=1
        REMOVE_PASS=1
        remove_packages
        remove_repos_and_files
        purge_data
        remove_passstore
        final_checks
        ASSUME_YES=$PREV_YES
        echo "ALL steps finished."
        pause
        ;;
      t)
        echo "Interactive toggle 'assume-yes' is disabled. Use '--yes' on the command line for non-interactive runs." 
        pause
        ;;
      d)
        DRY_RUN=$((1-DRY_RUN))
        echo "Dry-run set to: $DRY_RUN"
        pause
        ;;
      s)
        show_status
        pause
        ;;
      q)
        echo "Exit."; break
        ;;
      *) echo "Invalid choice."; pause ;;
    esac
  done
}

if [ "$RUN_ALL" -eq 1 ]; then
  echo "Running ALL steps non-interactively"
  detect_pm
  remove_packages
  remove_repos_and_files
  if [ "$PURGE" -eq 1 ]; then
    purge_data
  fi
  if [ "$REMOVE_PASS" -eq 1 ]; then
    remove_passstore
  fi
  final_checks
  exit 0
fi

main_menu
