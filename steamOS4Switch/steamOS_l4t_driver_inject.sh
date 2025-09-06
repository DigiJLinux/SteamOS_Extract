#!/usr/bin/env bash
set -euo pipefail

# steamOS_l4t_driver_inject.sh
# Inject NVIDIA L4T drivers into an extracted SteamOS root tree.
#
# Usage:
#   sudo ./steamOS_l4t_driver_inject.sh \
#       --root /path/to/steamOS_root \
#       --l4t-root /path/to/L4T_rootfs_or_extracted_BSP \
#       [--take-kernel] [--dry-run]
#
# What it does:
#   - Copies NVIDIA/Tegra firmware →  <root>/lib/firmware
#   - Copies NVIDIA user-space libs → <root>/usr/lib/{tegra, nvidia, ...}
#   - Copies udev rules & helpers
#   - Optionally replaces kernel Image/DTBs and /lib/modules with L4T’s (--take-kernel)
#   - Blacklists nouveau, adds ld.so conf, runs ldconfig (rootfs-targeted)
#
# Notes:
#   • Kernel modules MUST match the kernel. If you don’t use --take-kernel,
#     you need to build matching nvgpu modules for your SteamOS kernel.
#   • L4T is aarch64; your SteamOS target should be aarch64 too.
#   • This script avoids executing target-arch binaries. It uses ldconfig -r if available.

ROOT=""
L4T=""
TAKE_KERNEL=0
DRY_RUN=0

log() { echo -e "[*] $*"; }
warn() { echo -e "[!] $*" >&2; }
die() { echo -e "[x] $*" >&2; exit 1; }

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
  if [[ -e "$src" ]]; then
    log "Copy $desc → $dst"
    rs "$src" "$dst"
  else
    warn "Skip $desc (not found at $src)"
  fi
}

usage() {
  sed -n '1,60p' "$0" | sed -n '2,60p' | grep -E '^(# |Usage:|#   )' | sed 's/^# \{0,1\}//'
  exit 2
}

# --- Parse args ---
while (( "$#" )); do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --l4t-root) L4T="${2:-}"; shift 2 ;;
    --take-kernel) TAKE_KERNEL=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) die "Unknown arg: $1";;
  esac
done

[[ -n "$ROOT" && -n "$L4T" ]] || usage
[[ -d "$ROOT" ]] || die "Root tree not found: $ROOT"
[[ -d "$L4T"  ]] || die "L4T root/BSP dir not found: $L4T"

# --- Check tools ---
need rsync
need find
need grep
need awk
# ldconfig may not exist in minimal containers; we'll handle gracefully.

log "Target SteamOS root: $ROOT"
log "Source L4T tree    : $L4T"
[[ "$TAKE_KERNEL" -eq 1 ]] && log "Kernel adoption    : ENABLED (take L4T kernel)" || log "Kernel adoption    : disabled"

# --- Likely paths inside L4T tree ---
# L4T (Jetson Linux) is typically Ubuntu-based, with libs in /usr/lib/aarch64-linux-gnu/tegra
# and firmware in /lib/firmware/nvidia/tegra* etc.
L4T_FWF="/lib/firmware"
L4T_LIB_TEGRA="/usr/lib/aarch64-linux-gnu/tegra"
L4T_LIB_NVIDIA="/usr/lib/aarch64-linux-gnu/nvidia"
L4T_LIB_MISC="/usr/lib/aarch64-linux-gnu"
L4T_UDEV_RULES1="/lib/udev/rules.d"
L4T_UDEV_RULES2="/etc/udev/rules.d"
L4T_UDEV_LIB="/lib/udev"
L4T_ETC="/etc"

# Destination paths in SteamOS root
DST_FWF="$ROOT/lib/firmware"
DST_LIB="/usr/lib"
DST_UDEV_RULES1="$ROOT/lib/udev/rules.d"
DST_UDEV_RULES2="$ROOT/etc/udev/rules.d"
DST_UDEV_LIB="$ROOT/lib/udev"
DST_ETC="$ROOT/etc"

mkdir -p "$DST_FWF" "$DST_LIB" "$DST_UDEV_RULES1" "$DST_UDEV_RULES2" "$DST_UDEV_LIB" "$DST_ETC"

log "Copy NVIDIA/Tegra firmware…"
# Copy broad NVIDIA firmwares; keep it conservative.
copy_if_exists "$L4T/$L4T_FWF/nvidia" "$DST_FWF/" "firmware/nvidia"
copy_if_exists "$L4T/$L4T_FWF/tegra*" "$DST_FWF/" "firmware/tegra*"
# Some boards use cam/audio firmwares:
copy_if_exists "$L4T/$L4T_FWF/host1x" "$DST_FWF/" "firmware/host1x"
copy_if_exists "$L4T/$L4T_FWF/rtl*" "$DST_FWF/" "wireless firmware (rtl*)"
copy_if_exists "$L4T/$L4T_FWF/brcm" "$DST_FWF/" "wireless firmware (brcm)"

log "Copy NVIDIA user-space libraries…"
# Put tegra libs in a predictable subtree, and also mirror common L4T layout
if [[ -d "$L4T/$L4T_LIB_TEGRA" ]]; then
  mkdir -p "$ROOT/usr/lib/tegra"
  copy_if_exists "$L4T/$L4T_LIB_TEGRA/" "$ROOT/usr/lib/tegra/" "usr/lib/tegra"
fi
if [[ -d "$L4T/$L4T_LIB_NVIDIA" ]]; then
  mkdir -p "$ROOT/usr/lib/nvidia"
  copy_if_exists "$L4T/$L4T_LIB_NVIDIA/" "$ROOT/usr/lib/nvidia/" "usr/lib/nvidia"
fi

# Some libs (EGL/GLES/Vulkan) may also live under aarch64-linux-gnu
# We'll copy a curated set of SONAMEs if they exist.
for gl in libEGL* libGLES* libGLX* libnvidia* libcuda* libvulkan*; do
  mapfile -t CANDS < <(find "$L4T/$L4T_LIB_MISC" -maxdepth 1 -type f -name "$gl" 2>/dev/null || true)
  for f in "${CANDS[@]:-}"; do
    log "Copy $(basename "$f") → $ROOT/usr/lib/"
    rs "$f" "$ROOT/usr/lib/"
  done
done

log "Copy udev rules/helpers…"
copy_if_exists "$L4T/$L4T_UDEV_RULES1/99-nvidia*.rules" "$DST_UDEV_RULES1/" "udev rules (lib)"
copy_if_exists "$L4T/$L4T_UDEV_RULES1/*tegra*.rules"   "$DST_UDEV_RULES1/" "udev rules (lib tegra)"
copy_if_exists "$L4T/$L4T_UDEV_RULES2/99-nvidia*.rules" "$DST_UDEV_RULES2/" "udev rules (etc)"
copy_if_exists "$L4T/$L4T_UDEV_LIB"                    "$DST_UDEV_LIB/"    "udev helper binaries"

# Xorg/Wayland configs: just place tegra snippets if present
if compgen -G "$L4T/$L4T_ETC/X11/xorg.conf.d/*.conf" >/dev/null; then
  mkdir -p "$ROOT/etc/X11/xorg.conf.d"
  copy_if_exists "$L4T/$L4T_ETC/X11/xorg.conf.d/" "$ROOT/etc/X11/xorg.conf.d/" "X11 xorg.conf.d"
fi

# Blacklist nouveau (conflicts with NVIDIA)
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

# ld.so conf so tegra/nvidia libs are in the path
mkdir -p "$ROOT/etc/ld.so.conf.d"
if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Would write ld.so conf entries for /usr/lib/tegra and /usr/lib/nvidia"
else
  cat > "$ROOT/etc/ld.so.conf.d/tegra-nvidia.conf" <<'EOF'
/usr/lib/tegra
/usr/lib/nvidia
EOF
fi

# Optionally adopt the L4T kernel (Image, DTBs, modules)
if [[ "$TAKE_KERNEL" -eq 1 ]]; then
  log "Adopting L4T kernel (Image/DTBs/modules)…"
  # Try common locations
  L4T_BOOT_IMG="$L4T/boot/Image"
  L4T_BOOT_DTBS_DIR="$L4T/boot/dtb"
  [[ -f "$L4T_BOOT_IMG" ]] || L4T_BOOT_IMG="$L4T/boot/Image.gz"
  [[ -d "$L4T_BOOT_DTBS_DIR" ]] || L4T_BOOT_DTBS_DIR="$L4T/boot/dtb-$(uname -r 2>/dev/null || true)"

  mkdir -p "$ROOT/boot" "$ROOT/boot/dtb"
  copy_if_exists "$L4T_BOOT_IMG" "$ROOT/boot/" "kernel Image"
  copy_if_exists "$L4T_BOOT_DTBS_DIR/" "$ROOT/boot/dtb/" "DTBs"

  # Modules: pick the newest version in L4T's /lib/modules
  if [[ -d "$L4T/lib/modules" ]]; then
    L4T_KVER="$(ls -1 "$L4T/lib/modules" | sort -V | tail -n1 || true)"
    if [[ -n "$L4T_KVER" && -d "$L4T/lib/modules/$L4T_KVER" ]]; then
      mkdir -p "$ROOT/lib/modules"
      log "Copy modules $L4T_KVER"
      rs "$L4T/lib/modules/$L4T_KVER" "$ROOT/lib/modules/"
    else
      warn "No kernel version found in $L4T/lib/modules"
    fi
  else
    warn "No $L4T/lib/modules directory found"
  fi

  warn "Remember to regenerate initramfs and update your bootloader inside the SteamOS image."
  warn "  Arch (mkinitcpio): arch-chroot <root> mkinitcpio -P"
  warn "  Dracut (Fedora-like): chroot <root> dracut --force"
else
  warn "Kernel NOT replaced. Ensure your SteamOS kernel has matching NVIDIA L4T modules built."
fi

# ldconfig for the target root (if host ldconfig supports -r)
if command -v ldconfig >/dev/null 2>&1; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Would run: ldconfig -r $ROOT"
  else
    log "Run ldconfig against target root…"
    if ldconfig -V >/dev/null 2>&1; then
      if ldconfig -r "$ROOT" 2>/dev/null; then
        log "ldconfig updated (target root)."
      else
        warn "ldconfig -r failed on this host; you can run it later inside the target."
      fi
    fi
  fi
else
  warn "ldconfig not found on host; ensure ld cache is updated after boot."
fi

log "Done."
if [[ "$DRY_RUN" -eq 1 ]]; then
  warn "This was a DRY RUN (no changes were made). Re-run without --dry-run to apply."
fi
