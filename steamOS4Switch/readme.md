# (WIP) Debian ARM (aarch64) on Nintendo Switch with L4T + FEX‑Emu + Steam Client Autostart

> **Status:** Work‑in‑Progress (WIP). This is a deep technical build guide for creating an **ARM Debian** distro for the Nintendo Switch (Tegra210 “icosa”) using **Switchroot L4T** kernel/userspace components, **FEX‑Emu** for x86/x86_64 apps, and a **Steam** that **starts automatically on boot**.
>
> **Goal:** Optimized **streaming client appliance** (Moonlight or Steam Link* see note) with controller, audio, and networking ready, on top of Debian **arm64**.
>
> **You are responsible for your device and content. Back up your SD card.**

---

## 0) Why Debian ARM + L4T on Switch?

- **L4T (Linux for Tegra)** from **Switchroot** provides a Switch‑specific **kernel**, **DTBs** (notably `tegra210‑icosa.dtb`), **firmware**, and **NVIDIA userspace** necessary to boot Linux on the Switch and access HW acceleration.
- **Debian ARM64** provides a stable userland with wide package availability.
- **FEX‑Emu** runs **x86 and x86_64** Linux userland apps on ARM (user‑mode emulation + binfmt). Handy for tools or clients that lack native ARM builds.
- **Streaming focus:** We deploy a **Moonlight‑Qt** (recommended) or **Steam Link*** client and auto‑launch it for a console‑like experience.

> *As of writing, **Flathub lists Steam Link as x86_64‑only**. Prefer **Moonlight‑Qt** on ARM; see details below.

---

## 1) References & upstream docs

- Switchroot Linux hub and distro list: https://wiki.switchroot.org/wiki/linux  citeturn0search4  
- L4T distributions page (Ubuntu/Fedora): https://wiki.switchroot.org/wiki/linux/linux-distributions  citeturn0search0  
- FEX‑Emu project: https://github.com/FEX-Emu/FEX  (installer + docs)  citeturn0search1  
- FEX wiki (building/ARM64EC/dev notes): https://wiki.fex-emu.com  citeturn0search9turn0search5  
- Moonlight‑Qt client (ARM builds; Debian packages doc): https://github.com/moonlight-stream/moonlight-qt and install wiki  citeturn2search0turn2search5  
- Flathub Steam Link page (arch availability): https://flathub.org/apps/com.valvesoftware.SteamLink  citeturn1view0  

---

## 2) What you’ll need

- **RCM‑capable Switch**, **Hekate** and **L4T‑Loader** chain already working.
- A Linux **host** (Debian/Ubuntu, Fedora/RHEL, or Arch) with `sudo`.
- **MicroSD** card (≥64 GB recommended).
- A **Switchroot L4T** release unpacked somewhere on the host (we’ll *borrow* kernel/DTBs/firmware/userspace bits). For example, download **L4T Ubuntu Noble** and extract: kernel is under `/boot`, modules under `/lib/modules`, DTBs under `/boot/dtb`.  citeturn0search12
- Networked **PC host** with **Sunshine** (recommended) or Steam Remote Play host.  citeturn2search1turn2search12

---

## 3) Prepare host dependencies

### Debian/Ubuntu host
```bash
sudo apt update
sudo apt install -y debootstrap qemu-user-static binfmt-support \
  util-linux rsync e2fsprogs file gdisk parted udev
```

### Fedora/RHEL host
```bash
sudo dnf install -y debootstrap qemu-user-static util-linux \
  rsync e2fsprogs file gdisk parted udev
```

### Arch host
```bash
sudo pacman -S --needed debootstrap qemu-user-static-binfmt \
  util-linux rsync e2fsprogs file gptfdisk parted udev
```

> `debootstrap` + `qemu-user-static` let you build a **Debian arm64 rootfs** from x86_64 hosts.

---

## 4) Partition & format the SD (destructive!)

**Layout** (example `/dev/sdX`):
- **p1 FAT32 (1–2 GB)** → `/boot` (Hekate/L4T‑Loader reads kernel/DTB/initramfs here)
- **p2 ext4 (rest)** → `/` (Debian root)

```bash
sudo parted /dev/sdX --script \
  mklabel gpt \
  mkpart boot fat32 1MiB 2049MiB \
  set 1 boot on \
  mkpart root ext4 2049MiB 100%

sudo mkfs.vfat -F 32 -n BOOT /dev/sdX1
sudo mkfs.ext4 -L DEBIAN_ROOT /dev/sdX2

sudo mkdir -p /mnt/sd/boot /mnt/sd/root
sudo mount /dev/sdX1 /mnt/sd/boot
sudo mount /dev/sdX2 /mnt/sd/root
```

---

## 5) Bootstrap a minimal **Debian arm64** rootfs

Use **bookworm** (stable) as a baseline:

```bash
sudo debootstrap --arch=arm64 --foreign bookworm /mnt/sd/root http://deb.debian.org/debian
sudo cp /usr/bin/qemu-aarch64-static /mnt/sd/root/usr/bin/

# Second stage inside the target root
sudo chroot /mnt/sd/root /debootstrap/debootstrap --second-stage

# Basic fstab
sudo tee /mnt/sd/root/etc/fstab >/dev/null <<'EOF'
LABEL=DEBIAN_ROOT  /     ext4   defaults,noatime  0 1
LABEL=BOOT         /boot vfat   umask=0077        0 2
EOF

# Networking & admin basics
sudo chroot /mnt/sd/root bash -lc '
apt-get update &&
apt-get install -y systemd-sysv locales less vim sudo net-tools iproute2 \
  network-manager ca-certificates dbus udev rsync ssh curl wget \
  seatd libinput-bin plymouth
dpkg-reconfigure locales
systemctl enable NetworkManager
'
```

Create a user (for kiosk/auto‑login) and set a password:
```bash
sudo chroot /mnt/sd/root bash -lc '
useradd -m -G sudo,video,input,render,audio,netdev,games kiosk
echo "kiosk:kiosk" | chpasswd
'
```

---

## 6) Inject **L4T** kernel, DTBs, firmware, NVIDIA userspace

From your **unpacked Switchroot L4T** tree (e.g., L4T Ubuntu Noble), copy into the Debian root:

```bash
# Assume L4T tree at /opt/l4t (contains boot/Image, boot/dtb/, lib/modules/, lib/firmware/, usr/lib/** tegra/nvidia bits)

# Kernel + DTBs + initramfs (if provided)
sudo rsync -aH /opt/l4t/boot/Image*   /mnt/sd/boot/
sudo rsync -aH /opt/l4t/boot/dtb/    /mnt/sd/boot/dtb/
# Optional initramfs from L4T:
[ -f /opt/l4t/boot/initramfs ] && sudo rsync -aH /opt/l4t/boot/initramfs /mnt/sd/boot/

# Kernel modules
sudo rsync -aH /opt/l4t/lib/modules/ /mnt/sd/root/lib/modules/

# Firmware (wifi/bt/nvidia/tegra/host1x)
sudo rsync -aH /opt/l4t/lib/firmware/ /mnt/sd/root/lib/firmware/

# NVIDIA/tegra userspace (GL/VK/Multimedia)
sudo mkdir -p /mnt/sd/root/usr/lib/tegra /mnt/sd/root/usr/lib/nvidia
sudo rsync -aH /opt/l4t/usr/lib/aarch64-linux-gnu/tegra/  /mnt/sd/root/usr/lib/tegra/ || true
sudo rsync -aH /opt/l4t/usr/lib/aarch64-linux-gnu/nvidia/ /mnt/sd/root/usr/lib/nvidia/ || true

# Selected GL/VK libs commonly shipped under aarch64-linux-gnu
for n in libEGL* libGLES* libGLX* libnvidia* libcuda* libvulkan*; do
  for d in /opt/l4t/usr/lib/aarch64-linux-gnu /opt/l4t/usr/lib; do
    sudo rsync -aH "$d/$n" /mnt/sd/root/usr/lib/ 2>/dev/null || true
  done
done

# Udev rules/helpers
sudo rsync -aH /opt/l4t/lib/udev/ /mnt/sd/root/lib/udev/ 2>/dev/null || true
sudo rsync -aH /opt/l4t/lib/udev/rules.d/ /mnt/sd/root/lib/udev/rules.d/ 2>/dev/null || true
sudo rsync -aH /opt/l4t/etc/udev/rules.d/ /mnt/sd/root/etc/udev/rules.d/ 2>/dev/null || true

# ld.so path entries for injected libs
sudo tee /mnt/sd/root/etc/ld.so.conf.d/tegra-nvidia.conf >/dev/null <<'EOF'
/usr/lib/tegra
/usr/lib/nvidia
EOF

# Blacklist nouveau (use NVIDIA L4T stack)
sudo tee /mnt/sd/root/etc/modprobe.d/blacklist-nouveau.conf >/dev/null <<'EOF'
blacklist nouveau
options nouveau modeset=0
EOF
```

Set up **extlinux** for U‑Boot/L4T‑Loader:
```bash
sudo mkdir -p /mnt/sd/boot/extlinux
sudo tee /mnt/sd/boot/extlinux/extlinux.conf >/dev/null <<'EOF'
TIMEOUT  30
DEFAULT  debian

LABEL debian
  MENU LABEL Debian (L4T Switch)
  LINUX /boot/Image
  FDT /boot/dtb/tegra210-icosa.dtb
# If your kernel is Image.gz, use: LINUX /boot/Image.gz
# If you copied an initramfs:
#  INITRD /boot/initramfs
  APPEND root=LABEL=DEBIAN_ROOT rw rootwait console=tty0 quiet splash
EOF
```

> Switchroot L4T distros and boot flow: see wiki for current expectations and keys.  citeturn0search4turn2search10

Run `ldconfig` against the target (on host):
```bash
sudo chroot /mnt/sd/root ldconfig || true
```

---

## 7) Desktop/graphics/audio stack

Inside the Debian root:
```bash
sudo chroot /mnt/sd/root bash -lc '
apt-get update &&
apt-get install -y \
  mesa-utils wayland-protocols xwayland \
  pipewire pipewire-audio wireplumber \
  libva-drm2 libvulkan1 vulkan-tools \
  xorg xinit \
  libinput-tools evtest \
  flatpak
systemctl --global enable wireplumber.service || true
'
```

> L4T’s NVIDIA userspace provides the actual GL/VK on Switch; Mesa packages above are for tooling/fallback. Run `vulkaninfo`/`glxinfo` for sanity checks.

**Joy‑Con/Controller:**
Switchroot provides docs for pairing. For classic USB/Bluetooth controllers, Debian’s default BlueZ + SDL2 usually suffice. Switch‑specific nuances: see “Linux Features” on Switchroot wiki.  citeturn2search19

---

## 8) Install **Moonlight‑Qt** (recommended client)

Moonlight has **aarch64** builds and **Debian packages** for ARM SBCs.  citeturn2search5

### Option A: Flatpak (often available, but not always accelerated on ARM)
```bash
sudo chroot /mnt/sd/root bash -lc '
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo || true
flatpak install -y flathub com.moonlight_stream.Moonlight
'
```
(If Flatpak isn’t suitable, use Option B.)

### Option B: Native Debian package from Moonlight docs
Follow Moonlight’s guide to install **aarch64 .deb** releases (v5.0+).  citeturn2search5

---

## 9) (Optional) Steam Link note

As of now, **Flathub lists Steam Link for x86_64**; ARM availability varies. Prefer **Moonlight‑Qt** + **Sunshine** for ARM streaming.  citeturn1view0

---

## 10) Install **FEX‑Emu** for x86/x64 apps on ARM

FEX provides an installer for Ubuntu‑like systems; for **other distros** (Debian), follow the wiki build/installation steps.  citeturn0search1turn0search9

### Quick path (installer script – may work on Debian bookworm)
```bash
sudo chroot /mnt/sd/root bash -lc '
curl --silent https://raw.githubusercontent.com/FEX-Emu/FEX/main/Scripts/InstallFEX.py | python3
'
```
This sets up FEX, its rootfs, and **binfmt_misc** so `ELF x86/x86_64` executables run transparently under FEX.

> If the installer doesn’t support your exact Debian release, use the **source build** method from the FEX wiki and run `sudo ninja install` and `sudo ninja binfmt_misc` to register handlers.  citeturn0search9

Sanity check after chroot:
```bash
fex --version || echo "FEX not in PATH"
update-binfmts --display | grep -E 'x86-64|i386' || true
```

---

## 11) Autologin + Autostart streaming client (kiosk)

Create **TTY1 autologin** for user `kiosk`:
```bash
sudo tee /mnt/sd/root/etc/systemd/system/getty@tty1.service.d/override.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin kiosk --noclear %I $TERM
Type=idle
EOF

sudo chroot /mnt/sd/root systemctl daemon-reload
sudo chroot /mnt/sd/root systemctl enable getty@tty1.service
```

**Autostart Moonlight** in a minimal Wayland session (sway or gamescope). For simplicity, use **sway**:

```bash
sudo chroot /mnt/sd/root apt-get install -y sway grim slurp jq

# User session autostart
sudo -u kiosk mkdir -p /mnt/sd/root/home/kiosk/.config/systemd/user
sudo tee /mnt/sd/root/home/kiosk/.config/systemd/user/moonlight-kiosk.service >/dev/null <<'EOF'
[Unit]
Description=Moonlight Kiosk (Sway)

[Service]
Type=simple
Environment=MOONLIGHT_ARGS=-fullscreen
ExecStart=/usr/bin/sway --unsupported-gpu --config /home/kiosk/.config/sway/kiosk.conf
Restart=on-failure

[Install]
WantedBy=default.target
EOF

sudo -u kiosk mkdir -p /mnt/sd/root/home/kiosk/.config/sway
sudo tee /mnt/sd/root/home/kiosk/.config/sway/kiosk.conf >/dev/null <<'EOF'
# Minimal kiosk sway configuration
exec "moonlight %MOONLIGHT_ARGS%"
input type:keyboard repeat_delay 300 repeat_rate 30
bindsym Mod4+Shift+e exec "systemctl --user stop moonlight-kiosk.service"
output * bg #000000 solid_color
exec 'seatd -g video -u kiosk || true'
EOF

# Enable user service on login
sudo chroot /mnt/sd/root loginctl enable-linger kiosk
sudo chroot /mnt/sd/root sudo -u kiosk dbus-launch --exit-with-session systemctl --user enable moonlight-kiosk.service || true
```

> You can replace sway with **gamescope** directly if desired. Sway is a minimal Wayland compositor with good kiosk behavior.

---

## 12) Performance / power tuning

Inside Debian on the Switch (after first boot):
```bash
# Governor
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# Disable screen blanking on TTY
sudo sed -i 's/^#TTYVTDisallocate.*/TTYVTDisallocate=no/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind

# PipeWire (ensure active)
systemctl --user enable --now wireplumber.service || true
```

Moonlight settings to try:
- 720p or 1080p (dock) @ 60 Hz; tweak bitrate to your WLAN/LAN.
- Prefer **wired** USB‑C Ethernet when possible.
- Enable **low‑latency** modes on Sunshine host; test HEVC vs H.264.  citeturn2search15

---

## 13) First boot procedure

1. Eject SD → Insert to Switch.
2. Boot **Hekate → L4T‑Loader → Linux** → extlinux menu → **Debian**.
3. On first login (autologin `kiosk` on TTY1), sway should start and then **Moonlight**.
4. Pair Moonlight with your **Sunshine** host; then launch a stream.

Diagnostics:
```bash
dmesg -T | less
journalctl -b --no-pager | less
glxinfo -B
vulkaninfo | head
```
If graphics fall back to llvmpipe, recheck **L4T NVIDIA userspace** and library paths (`/usr/lib/tegra`, `/usr/lib/nvidia`, `ld.so.conf.d`).

---

## 14) Troubleshooting

- **Black screen after bootloader:** confirm `FDT /boot/dtb/tegra210-icosa.dtb` and kernel/modules match.
- **Cannot mount root:** adjust `root=` in extlinux; use `LABEL=DEBIAN_ROOT` or `UUID=…` (get via `blkid`).
- **Wi‑Fi/BT absent:** validate firmware in `/lib/firmware/brcm` and dmesg for `brcmfmac`/BT errors.
- **No audio:** ensure PipeWire stack is running; check ALSA UCM profiles in L4T build.
- **Moonlight stutter:** try H.264 vs HEVC, lower bitrate/resolution, prefer wired.
- **Need x86 helper tools:** verify **FEX** binfmt registration; `update-binfmts --display`.

---

## 15) Notes on “Steam for ARM”

There is **no widely available native Linux ARM Steam client** today. For streaming on ARM, prefer **Moonlight‑Qt** without emulating Steam itself. The **Steam Link** Flatpak page currently lists **x86_64** availability; ARM builds may not be available on Flathub at this time.  citeturn1view0

---

## 16) What’s next (WIP roadmap)

- Provide a pre‑baked **Debian arm64 rootfs tarball** with L4T bits pre‑injected.
- Optional **gamescope‑kiosk** session instead of sway.
- Automate SD provisioning (partition → debootstrap → inject L4T → kiosk).
- Jetson/NVV4L2 decoder verification in Moonlight build flags for best HW decode on T210.
- Controller UX: auto‑pair Joy‑Cons; map ABXY for Steam‑style prompts.

---

**Happy hacking & streaming!**

