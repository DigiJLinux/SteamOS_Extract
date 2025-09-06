#!/usr/bin/env bash
set -euo pipefail

# steamOS_switch_l4t_inject.sh
# Inject Switchroot L4T (Nintendo Switch, Tegra210 "icosa") userspace + firmware,
# and optionally kernel+modules+DTB, into an extracted SteamOS root tree.
#
# USAGE:
#   sudo ./steamOS_switch_l4t_inject.sh \
#       --root /path/to/steamOS_root \
#       --switchroot /path/to/unpacked_switchroot_l4t \
#       [--take-kernel] [--write-extlinux] [--dry-run]
#
# EXAMPLES:
#   # Typical: adopt L4T kernel+DTB and write extlinux.conf
#   sudo ./steamOS_switch_l4t_inject.sh \
#     --root /mnt/steamOS \
#     --switchroot /mnt/switchroot_l4t \
#     --take-kernel --write-extlinux
#
# What it does:
#   • Copies NVIDIA/Tegra firmware → <root>/lib/firmware
#   • Copies NVIDIA/tegra user-space libs → <root>/usr/lib/{tegra,nvidia}/ and /usr/lib
#   • Copies udev rules/helpers; blacklists nouveau; adds ld.so conf and runs ldconfig -r
#   • (optional) Replaces /boot/Image(+initramfs) + /boot/dtb/* + /lib/modules/* with
#     Switchroot L4T kernel and modules (--take-kernel)
#   • (optional) Writes /boot/extlinux/extlinux.conf for Switch U-Boot/L4T-loader (--write-extlinux)
#
# Notes:
#   • The Nintendo Switch DTB is typically tegra210-icosa.dtb. Place it in /boot/dtb/.
#   • If you don’t adopt the L4T kernel, you must build L4T-compatible modules for your SteamOS kernel.
#   • This script is host-distro agnostic (Debian/Fedora/Arch OK). It just copies files.

ROOT=""
SWROOT=""
TAKE_KERNEL=0
WRITE_EXTLINUX=0
DRY_RUN=0

log()  { echo -e "[*] $*"; }
warn() { echo -e "[!] $*" >&2; }
die()  { echo -e "[x] $*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "Missing dependency: $1"
}

rs() {
  # rsync wrapper honoring --dry-run
  if [[ "$DRY_RUN" -eq 1 ]]; then
    rsync -avh --dry-run --numeric-ids --no-owner --no-group "$@"
  else
    rsync -aHAX --numeric-ids "$@"
  fi
}

copy_if_exists() {
  local src="$1" dst="$2" desc="$3"
  shopt -s nullglob
  local matches=($src)
  shopt -u nullglob
  if (( ${#matches[@]} )); then
    log "Copy $desc → $dst"
    rs "${matches[@]}" "$dst"
  else
    warn "Skip $desc (not found: $src)"
  fi
}

usage() {
  cat <<'EOF'
Usage:
  sudo ./steamOS_switch_l4t_inject.sh \
    --root /path/to/steamOS_root \
    --switchroot /path/to/unpacked_switchroot_l4t \
    [--take-kernel] [--write-extlinux] [--dry-run]

Args:
  --root         Path to extracted SteamOS root tree (from img2dsk)
  --switchroot   Path to *unpacked* Switchroot L4T filesystem/release (contains boot files, libs, firmware)
  --take-kernel  Replace /boot Image+DTBs and /lib/modules with Switchroot L4T kernel/modules
  --write-extlinux  Write /boot/extlinux/extlinux.conf for U-Boot/L4T-loader on Switch
  --dry-run      Show what would change without copying
EOF
  exit 2
}

while (( "$#" )); do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --switchroot) SWROOT="${2:-}"; shift 2 ;;
    --take-kernel) TAKE_KERNEL=1; shift ;;
    --write-extlinux) WRITE_EXTLINUX=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) die "Unknown arg: $1" ;;
  esac
done

[[ -n "$ROOT" && -n "$SWROOT" ]] || usage
[[ -d "$ROOT" ]] || die "Root tree not found: $ROOT"
[[ -d "$SWROOT" ]] || die "Switchroot L4T dir not found: $SWROOT"

# deps
need rsync
need find

log "Target SteamOS root : $ROOT"
log "Switchroot L4T path : $SWROOT"
[[ "$TAKE_KERNEL" -eq 1 ]]     && log "Kernel adoption     : ENABLED" || log "Kernel adoption     : disabled"
[[ "$WRITE_EXTLINUX" -eq 1 ]]  && log "extlinux.conf write : ENABLED" || log "extlinux.conf write : disabled"

# ------- Likely Switchroot layout hints -------
# Firmware is under /lib/firmware/{nvidia,brcm,rtl*,host1x,tegra*}
# Userspace GL/VK libs under /usr/lib/aarch64-linux-gnu/{tegra,nvidia} and plain /usr/lib
# Kernel Image in /boot/Image (or Image.gz), DTBs in /boot/dtb/* (want tegra210-icosa.dtb)
# Modules in /lib/modules/<kver>
#
# (These align with Switchroot L4T Linux distro docs & boot config.)  (See Switchroot wiki)
# ------------------------------------------------

# source locations
S_FWF="$SWROOT/lib/firmware"
S_LIB_TEGRA="$SWROOT/usr/lib/aarch64-linux-gnu/tegra"
S_LIB_NVIDIA="$SWROOT/usr/lib/aarch64-linux-gnu/nvidia"
S_LIB_MISC="$SWROOT/usr/lib/aarch64-linux-gnu"
S_LIB_FLAT="$SWROOT/usr/lib"
S_UDEV_RULES1="$SWROOT/lib/udev/rules.d"
S_UDEV_RULES2="$SWROOT/etc/udev/rules.d"
S_UDEV_BIN="$SWROOT/lib/udev"

# dest locations in SteamOS root
D_FWF="$ROOT/lib/firmware"
D_LIB="$ROOT/usr/lib"
D_UDEV_RULES1="$ROOT/lib/udev/rules.d"
D_UDEV_RULES2="$ROOT/etc/udev/rules.d"
D_UDEV_BIN="$ROOT/lib/udev"

mkdir -p "$D_FWF" "$D_LIB" "$D_UDEV_RULES1" "$D_UDEV_RULES2" "$D_UDEV_BIN" "$ROOT/etc"

log "Copy NVIDIA/Tegra firmware…"
copy_if_exists "$S_FWF/nvidia" "$D_FWF/" "firmware/nvidia"
copy_if_exists "$S_FWF/tegra*" "$D_FWF/" "firmware/tegra*"
copy_if_exists "$S_FWF/host1x" "$D_FWF/" "firmware/host1x"
copy_if_exists "$S_FWF/brcm"   "$D_FWF/" "firmware/brcm (Wi-Fi/BT)"
copy_if_exists "$S_FWF/rtl*"   "$D_FWF/" "firmware/rtl* (Wi-Fi/BT)"

log "Copy NVIDIA/tegra userspace libs…"
mkdir -p "$ROOT/usr/lib/tegra" "$ROOT/usr/lib/nvidia"
copy_if_exists "$S_LIB_TEGRA/"  "$ROOT/usr/lib/tegra/"  "usr/lib/tegra"
copy_if_exists "$S_LIB_NVIDIA/" "$ROOT/usr/lib/nvidia/" "usr/lib/nvidia"

# Common GL/VK/CUDA SONAMEs sometimes lie flat in aarch64-linux-gnu or in usr/lib
for gl in libEGL* libGLES* libGLX* libnvidia* libcuda* libvulkan*; do
  mapfile -t C1 < <(find "$S_LIB_MISC" -maxdepth 1 -type f -name "$gl" 2>/dev/null || true)
  mapfile -t C2 < <(find "$S_LIB_FLAT" -maxdepth 1 -type f -name "$gl" 2>/dev/null || true)
  for f in "${C1[@]:-}" "${C2[@]:-}"; do
    log "Copy $(basename "$f") → /usr/lib/"
    rs "$f" "$D_LIB/"
  done
done

log "Copy udev rules/helpers…"
copy_if_exists "$S_UDEV_RULES1/99-nvidia*.rules" "$D_UDEV_RULES1/" "udev rules (lib)"
copy_if_exists "$S_UDEV_RULES1/*tegra*.rules"    "$D_UDEV_RULES1/" "udev rules (lib tegra)"
copy_if_exists "$S_UDEV_RULES2/99-nvidia*.rules" "$D_UDEV_RULES2/" "udev rules (etc)"
copy_if_exists "$S_UDEV_BIN"                     "$D_UDEV_BIN/"    "udev helper binaries"

# Blacklist nouveau; prefer NVIDIA stack
mkdir -p "$ROOT/etc/modprobe.d"
if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Would write nouveau blacklist to $ROOT/etc/modprobe.d/blacklist-nouveau.conf"
else
  cat > "$ROOT/etc/modprobe.d/blacklist-nouveau.conf" <<'EOF'
# Block nouveau to allow NVIDIA L4T driver stack
blacklist nouveau
options nouveau modeset=0
EOF
fi

# ld.so search paths for the injected libs
mkdir -p "$ROOT/etc/ld.so.conf.d"
if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Would write ld.so conf entries for /usr/lib/tegra and /usr/lib/nvidia"
else
  cat > "$ROOT/etc/ld.so.conf.d/tegra-nvidia.conf" <<'EOF'
/usr/lib/tegra
/usr/lib/nvidia
EOF
fi

# ---- OPTIONAL: adopt Switchroot kernel/DTB/modules ----
if [[ "$TAKE_KERNEL" -eq 1 ]]; then
  log "Adopting Switchroot L4T kernel/DTBs/modules for Nintendo Switch…"
  mkdir -p "$ROOT/boot" "$ROOT/boot/dtb"
  # Kernel (Image or Image.gz)
  if [[ -f "$SWROOT/boot/Image" ]]; then
    copy_if_exists "$SWROOT/boot/Image" "$ROOT/boot/" "kernel Image"
  elif [[ -f "$SWROOT/boot/Image.gz" ]]; then
    copy_if_exists "$SWROOT/boot/Image.gz" "$ROOT/boot/" "kernel Image.gz"
  else
    warn "No kernel Image found in $SWROOT/boot"
  fi

  # DTBs (want tegra210-icosa.dtb specifically)
  if [[ -d "$SWROOT/boot/dtb" ]]; then
    copy_if_exists "$SWROOT/boot/dtb/" "$ROOT/boot/dtb/" "DTBs"
  else
    warn "No DTB directory at $SWROOT/boot/dtb"
  fi

  # initramfs (if provided by Switchroot build)
  if [[ -f "$SWROOT/boot/initramfs" ]]; then
    copy_if_exists "$SWROOT/boot/initramfs" "$ROOT/boot/" "initramfs"
  elif compgen -G "$SWROOT/boot/initrd*.img" >/dev/null; then
    copy_if_exists "$SWROOT/boot/initrd*.img" "$ROOT/boot/" "initrd"
  else
    warn "No initramfs/initrd found in $SWROOT/boot (you may need to build one in SteamOS)"
  fi

  # Kernel modules (take newest kver present)
  if [[ -d "$SWROOT/lib/modules" ]]; then
    KVER="$(ls -1 "$SWROOT/lib/modules" | sort -V | tail -n1 || true)"
    if [[ -n "$KVER" && -d "$SWROOT/lib/modules/$KVER" ]]; then
      mkdir -p "$ROOT/lib/modules"
      log "Copy modules $KVER"
      rs "$SWROOT/lib/modules/$KVER" "$ROOT/lib/modules/"
    else
      warn "No kernel version found in $SWROOT/lib/modules"
    fi
  else
    warn "No $SWROOT/lib/modules directory found"
  fi
else
  warn "Kernel NOT replaced. Ensure SteamOS kernel has Switchroot L4T-compatible modules."
fi

# ---- OPTIONAL: write /boot/extlinux/extlinux.conf (U-Boot/L4T-loader) ----
if [[ "$WRITE_EXTLINUX" -eq 1 ]]; then
  mkdir -p "$ROOT/boot/extlinux" "$ROOT/boot/dtb"
  # Try to pick a DTB; prefer tegra210-icosa.dtb
  DTB_NAME="tegra210-icosa.dtb"
  if [[ ! -f "$ROOT/boot/dtb/$DTB_NAME" ]]; then
    # pick any tegra210*.dtb if icosa is missing
    cand="$(ls -1 "$ROOT/boot/dtb"/tegra210*.dtb 2>/dev/null | head -n1 || true)"
    [[ -n "$cand" ]] && DTB_NAME="$(basename "$cand")"
  fi

  # Best-effort root= path (commonly an ext4 root partition label or device)
  # If you know your real root=, adjust after writing.
  ROOT_ARGS="root=/dev/mmcblk0p2 rw rootwait"
  IMG_PATH="/boot/Image"
  INITRD_PATH=""
  [[ -f "$ROOT/boot/initramfs" ]] && INITRD_PATH="/boot/initramfs"
  for k in "$ROOT"/boot/initrd*.img; do
    [[ -f "$k" ]] && INITRD_PATH="/boot/$(basename "$k")"
  done

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Would write $ROOT/boot/extlinux/extlinux.conf with FDT $DTB_NAME"
  else
    cat > "$ROOT/boot/extlinux/extlinux.conf" <<EOF
# extlinux.conf for Nintendo Switch (Switchroot L4T loader)
TIMEOUT  30
DEFAULT  steamOS

LABEL steamOS
  MENU LABEL steamOS (Switch L4T)
  LINUX $IMG_PATH
  FDT /boot/dtb/$DTB_NAME
$( [[ -n "$INITRD_PATH" ]] && echo "  INITRD $INITRD_PATH" )
  APPEND $ROOT_ARGS console=tty0 quiet splash
EOF
  fi

  log "Wrote extlinux.conf (adjust 'root=' to your actual root device/label)."
  # For hekate boot entry keys and Linux boot specifics, see Switchroot boot docs.
  # (This mirrors their extlinux/U-Boot flow.) 
fi

# ldconfig for target root (if host has it)
if command -v ldconfig >/dev/null 2>&1; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Would run ldconfig -r $ROOT"
  else
    log "Run ldconfig against target root…"
    ldconfig -r "$ROOT" || warn "ldconfig -r failed; run inside chroot later."
  fi
else
  warn "Host lacks ldconfig; update linker cache after boot."
fi

log "Done."
if [[ "$DRY_RUN" -eq 1 ]]; then
  warn "DRY RUN only. Re-run without --dry-run to apply."
fi
