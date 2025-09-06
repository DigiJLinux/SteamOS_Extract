# (WIP) SteamOS (Arch) on Nintendo Switch via L4T / Switchroot – Deep-Dive Engineering Guide

> **Status:** Work-in-Progress (WIP). Expect rough edges. Contributions and testing notes welcome.
>
> **Audience:** Nintendo Switch homebrew engineers interested in bringing an **Arch-based SteamOS userland** to the Switch using **Switchroot L4T** (Linux for Tegra, Tegra210 “icosa”). This document assumes you are comfortable with Linux, initramfs tooling, kernel modules, device trees, partitioning, and bootloaders (Hekate/UBoot/extlinux).

---

## 0) What we are doing (high-level)

We’ll take a **SteamOS (Arch) root filesystem** and marry it with the **Switchroot L4T platform stack** (kernel, DTBs, firmware, NVIDIA userspace, udev rules). The result is an Arch/SteamOS userspace that boots on the Switch’s **Tegra X1 (T210)** using Switchroot’s kernel and boot flow.

**Why this split?**

- Switch requires a **vendor kernel + DTB + firmware** tuned for Tegra210 (icosa). That’s Switchroot L4T.
- SteamOS brings the **gaming-oriented userland** (Arch base, Proton/Steam, PipeWire, etc.).
- We inject L4T-specific drivers and (optionally) replace the kernel/modules so the userland runs on Switch hardware.

---

## 1) Prerequisites, Warnings & Device State

- **RCM-capable Switch only.** You must already know how to boot **Hekate** and load “Linux” payloads. This guide does not cover obtaining or using payloads on devices that cannot legally/technically run them.
- **A Linux host** (Debian/Ubuntu, Fedora/RHEL, or Arch) with root privileges.
- **MicroSD** (UHS-I, ≥64 GB recommended) or **USB** storage.
- **Switchroot L4T release tree** (unpacked) for the Switch (contains `/boot/Image`, `/boot/dtb/tegra210-icosa.dtb`, `/lib/modules/<kver>`, firmware, userspace libraries, etc.).
- **SteamOS (Arch) image** you can extract and edit.
- Basic comfort with **extlinux.conf**, **UBoot**, **initramfs**, and **systemd**.

**Risk:** You can soft-brick your Linux install on SD if misconfigured. Back up first.

---

## 2) Tooling used in this workflow

We provide two families of tools:

1. **Extraction (“flash to directory”)** – turns the multi-partition “superimage” into a working directory:
   - `img2dsk.py` (CLI, cross-distro)
   - `img2dsk_arch.py` (CLI + GUI, Arch)
   - `img2dsk_fedora.py` (CLI + GUI, Fedora/RHEL)

2. **Repack** – replaces `rootfs`, `/var`, `/home` inside the old superimage and writes a new bootable `.img`:
   - `repack_superimage.py` (CLI + GUI, cross-distro)

3. **Switch-specific L4T injection** – injects Switchroot drivers/libs/DTBs and (optionally) kernel:
   - `steamOS_switch_l4t_inject.sh`

Place all scripts in one working directory and `chmod +x` as appropriate.

---

## 3) Install host dependencies

Pick the commands for your host distro (host **only**; we’re not modifying the Switch yet).

### Debian/Ubuntu
```
sudo apt update
sudo apt install -y util-linux rsync e2fsprogs file squashfs-tools python3-tk gdisk parted udev
```

### Fedora/RHEL/CentOS Stream
```
sudo dnf install -y util-linux rsync e2fsprogs file squashfs-tools python3-tkinter gdisk parted udev
```

### Arch Linux
```
sudo pacman -S --needed util-linux rsync e2fsprogs file squashfs-tools tk gptfdisk parted udev
```

---

## 4) Recommended partitioning for SD

Most Switchroot L4T builds use **FAT32 boot** + **ext4 root**. We’ll mirror that so Hekate/L4T-Loader can find kernel/dtb/initrd and the kernel can mount your rootfs.

**Example (SD card `/dev/sdX`):**

- **p1 (FAT32, 1–2 GB)** → `/boot` (Hekate sees this)
- **p2 (ext4, rest of card)** → `/` (root filesystem)

> If you already have a multi-boot setup (Atmosphere/Android/Linux) follow your established partition map. This guide assumes a minimal two-part layout.

**Create partitions (destructive!):**
```
sudo parted /dev/sdX --script \
  mklabel gpt \
  mkpart boot fat32 1MiB 2049MiB \
  set 1 boot on \
  mkpart root ext4 2049MiB 100%
sudo mkfs.vfat -F 32 -n BOOT /dev/sdX1
sudo mkfs.ext4 -L STEAMOS_ROOT /dev/sdX2
```

Mount them for staging:
```
sudo mkdir -p /mnt/sd/boot /mnt/sd/root
sudo mount /dev/sdX1 /mnt/sd/boot
sudo mount /dev/sdX2 /mnt/sd/root
```

---

## 5) Extract the SteamOS (Arch) superimage

Use the extraction tool to “flash to directory”:

```
sudo ./img2dsk.py steamdeck.img /mnt/steamOS
# or GUI:
sudo ./img2dsk_arch.py --gui
```

This yields:
```
/mnt/steamOS/        # rootfs (Arch/SteamOS userland)
/mnt/steamOS/var/    # var
/mnt/steamOS/home/   # home
```

> If your image contained nested `rootfs-A.img`, `var-A.img`, etc., the extractor handles that automatically.

---

## 6) Inject Switchroot L4T stack into SteamOS root

Unpack your **Switchroot L4T** release somewhere (e.g., `/mnt/switchroot_l4t`). Then run the injector:

```
sudo ./steamOS_switch_l4t_inject.sh \
  --root /mnt/steamOS \
  --switchroot /mnt/switchroot_l4t \
  --take-kernel \
  --write-extlinux
```

What this does:

- Copies **firmware**: `/lib/firmware/{nvidia,brcm,rtl*,host1x,tegra*}`
- Copies **userspace**: `/usr/lib/tegra`, `/usr/lib/nvidia`, GL/VK/CUDA SONAMEs (to `/usr/lib`)
- Installs **udev rules/helpers** and writes **nouveau blacklist**
- Adds `ld.so` search paths for tegra/nvidia and runs `ldconfig -r` on the target root
- **(Recommended)** Replaces `/boot/Image(,gz)`, `/boot/dtb/*` (notably **`tegra210-icosa.dtb`**) and `/lib/modules/<kver>` with those from Switchroot L4T
- **Writes** `/boot/extlinux/extlinux.conf` (adjust `root=` after).

> If you omit `--take-kernel`, you must build matching NVIDIA/L4T kernel modules against your SteamOS kernel. For most setups, using the Switchroot kernel is simpler and far less error-prone.

---

## 7) extlinux.conf (example)

The injector writes a sane default. Review/tweak it:

`/boot/extlinux/extlinux.conf`
```
TIMEOUT  30
DEFAULT  steamOS

LABEL steamOS
  MENU LABEL steamOS (Switch L4T)
  LINUX /boot/Image
  FDT /boot/dtb/tegra210-icosa.dtb
  INITRD /boot/initramfs    # (if present; or initrd*.img)
  APPEND root=LABEL=STEAMOS_ROOT rw rootwait console=tty0 quiet splash loglevel=3
```

Common adjustments:
- `root=` — use `LABEL=STEAMOS_ROOT`, `UUID=<uuid>`, or `/dev/mmcblk0p2` depending on your layout.
- If your kernel is **Image.gz**, set `LINUX /boot/Image.gz`.
- Add `init=` (rare) or `fsck.repair=yes` if desired.

> Boot flow is **Hekate → L4T-Loader → (ATF) → U-Boot → extlinux**; the keys supported by L4T/Hekate are documented in the Switchroot wiki. You can also boot via **hekate boot entries (`*.ini`)** that pass bootargs to U-Boot/extlinux.

---

## 8) Populate SD root and boot

Copy your prepared tree to the SD card mounts:

```
# Copy rootfs (preserve xattrs/ACLs/ids)
sudo rsync -aAXH --numeric-ids /mnt/steamOS/ /mnt/sd/root/

# Copy boot (kernel, dtbs, initramfs, extlinux)
sudo rsync -aH /mnt/steamOS/boot/ /mnt/sd/boot/
# If your injector wrote into /mnt/steamOS/boot, this picks it up.
```

Ensure `extlinux.conf` is present in `/mnt/sd/boot/extlinux/` and points to the correct filenames.

Unmount:
```
sync
sudo umount /mnt/sd/boot /mnt/sd/root
```

Insert the SD into the Switch.

---

## 9) First boot checklist (on Switch)

- In Hekate, choose your **Linux** entry / **L4T-Loader** → select the boot entry pointing at your `/boot` partition.
- First login over attached keyboard or SSH (if enabled). Then execute inside the target:
  ```
  sudo ldconfig
  sudo udevadm control --reload
  sudo udevadm trigger
  ```
- If you didn’t copy an initramfs from Switchroot, **generate one** (choose the tool your SteamOS image uses):
  - **mkinitcpio (Arch-like):**
    ```
    sudo mkinitcpio -P
    ```
  - **dracut (Fedora-like):**
    ```
    sudo dracut --force
    ```

- **NVIDIA userspace sanity:**
  - Confirm loaders:
    ```
    ls -l /usr/lib/libEGL* /usr/lib/libGLES* /usr/lib/libnvidia* /usr/lib/tegra /usr/lib/nvidia
    ```
  - Ensure GLVND picks NVIDIA where needed; optionally set environment for Steam session:
    ```
    export __GLX_VENDOR_LIBRARY_NAME=nvidia
    export __NV_PRIME_RENDER_OFFLOAD=1
    export __VK_LAYER_NV_optimus=N/A
    ```

---

## 10) Input, Display, Audio, Networking

### Joy‑Cons & Controllers
- Kernel driver: `hid-nintendo` (usually built-in on Switchroot kernels).
- Userspace: `joycond` pairs left/right joycons into a single device.
  - On Arch:
    ```
    sudo pacman -S --needed joycond
    sudo systemctl enable --now joycond
    ```

### Display
- Internal panel is **1280×720**; HDMI out is supported with L4T stack.
- If you see a black screen but TTY is alive, check KMS modes, DTB path, and ensure no conflicting Xorg confs.

### Audio
- Switch audio routes through Tegra/Maxwell codecs; on Arch/SteamOS this is easiest with **PipeWire**.
  - Ensure pipewire, pipewire‑alsa, pipewire‑pulse are installed and enabled.
  - UCM profiles come via L4T/Switchroot; if missing, install/copy appropriate `alsa-ucm` snippets.

### Wi‑Fi / Bluetooth
- Firmware typically under `brcm` (`brcmfmac4356-sdio` family) + BT blobs.
- Confirm presence in `/lib/firmware/brcm/` and BT firmware in appropriate path.
- Bring up with NetworkManager or connman.

---

## 11) Performance & Power Management

- **CPU governor:** `schedutil` is a good default. Check with:
  ```
  cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
  ```
- **GPU clocks:** L4T may manage clocks; for tuning use Switchroot-provided tools if any.
- **Suspend/Resume:** Historically finicky on Switch; test on your build. Consider deep sleep disable if unstable.

---

## 12) Steam session tips on Switch

- Prefer **Gamescope** session if you target handheld UX:
  - Install `gamescope`, `steam`, required runtime drivers.
  - Launch Steam under gamescope; test with `gamescope -f -- steam -gamepadui`.
- Consider **MangoHud**, **gamemode**.
- Vulkan ICDs: ensure NVIDIA ICD exists; `ls /usr/share/vulkan/icd.d` and `vulkaninfo` sanity check.

---

## 13) Repack into a portable superimage (optional)

Once your `/mnt/steamOS` is functional, you can rebuild a **monolithic superimage** suitable for dd/etchers:

```
sudo ./repack_superimage.py \
  --old steamdeck.img \
  --root /mnt/steamOS \
  --out steamdeck_switch_l4t.img
```

Flags `--no-var` / `--no-home` allow skipping those partitions if not desired.

---

## 14) Troubleshooting

- **Black screen after bootloader:** wrong DTB or `extlinux.conf` path; verify `FDT /boot/dtb/tegra210-icosa.dtb` and matching kernel modules.
- **Kernel panic “cannot mount root”:** incorrect `root=`; use `root=LABEL=STEAMOS_ROOT` or `root=UUID=<…>`; ensure filesystem/UUID exists.
- **X/Wayland fails with EGL errors:** missing NVIDIA userspace or GLVND selecting the wrong vendor; confirm `/usr/lib/tegra` and `/usr/lib/nvidia` in `ld.so.conf.d`, run `ldconfig`.
- **Wi‑Fi/BT missing:** check that `brcm` and BT firmware blobs were copied; verify dmesg for `brcmfmac`/BT errors.
- **No audio:** verify PipeWire is active; check `alsa-ucm` profiles; inspect `dmesg` for codec probe.
- **Joy‑Cons flaky:** ensure `joycond` service running; fall back to wired USB controller for validation.

Collect logs:
```
dmesg -T | less
journalctl -b --no-pager | less
cat /proc/cmdline
```

---

## 15) Security & Legal

- This guide assumes you already have a device capable of loading custom payloads in a manner compliant with your local laws and terms. You are responsible for the state of your device and any warranties voided.
- Do not redistribute proprietary NVIDIA components or Switchroot assets unless permitted by their licenses.

---

## 16) References (switchroot / L4T docs)

- **Switchroot Linux hub**: https://wiki.switchroot.org/wiki/linux
- **Distributions**: https://wiki.switchroot.org/wiki/linux/linux-distributions
- **Install guides (Ubuntu/Fedora)**: 
  - Jammy 22.04: https://wiki.switchroot.org/wiki/linux/l4t-ubuntu-jammy-installation-guide
  - Noble 24.04: https://wiki.switchroot.org/wiki/linux/l4t-ubuntu-noble-installation-guide
  - Fedora 41: https://wiki.switchroot.org/wiki/linux/l4t-fedora-installation-guide-1
- **Boot config (hekate/L4T‑Loader keys)**: https://wiki.switchroot.org/wiki/linux/linux-boot-configuration
- **Bootstack docs (L4T‑Loader/UBoot/extlinux)**: https://wiki.switchroot.org/wiki/linux/linux-bootstack-documentation
- **USB/eMMC boot addenda**: https://wiki.switchroot.org/wiki/linux/linux-usb-or-emmc-boot
- **DTB (tegra210‑icosa.dtb) context**: eMMC boot notes mention the DTB explicitly.

---

## 17) Appendix – Manual hekate boot entry (ini) example (optional)

If you prefer to drive everything from hekate’s `bootloader/ini` entry (instead of extlinux), consult the Boot Configuration page above. A *conceptual* example (keys vary by release; prefer extlinux unless you know what you’re doing):

```
[SteamOS (Switch L4T)]
payload=bootloader/payloads/u-boot.bin
fdtfile=/boot/dtb/tegra210-icosa.dtb
kernel=/boot/Image
initrd=/boot/initramfs
rootdev=mmcblk0
rootpart=2
bootargs=root=LABEL=STEAMOS_ROOT rw rootwait console=tty0 quiet splash loglevel=3
```

> Note: The supported keys/semantics change per L4T‑Loader/hekate version. Always check the **Linux Boot Configuration** page for exact key names and defaults.

---

## 18) Appendix – Filemaps changed by the injector

- `/lib/firmware/{nvidia,tegra*,host1x,brcm,rtl*}`
- `/usr/lib/tegra`, `/usr/lib/nvidia`, selected GL/VK/CUDA libraries in `/usr/lib`
- `/lib/udev/rules.d/*nvidia*.rules`, `/etc/udev/rules.d`
- `/etc/modprobe.d/blacklist-nouveau.conf`
- `/etc/ld.so.conf.d/tegra-nvidia.conf`
- `/boot/Image(,gz)`, `/boot/dtb/tegra210-icosa.dtb`, optional `initramfs`
- `/lib/modules/<kver>` (if `--take-kernel`)

---

## 19) Roadmap / To‑Do (WIP)

- Validate a **Gamescope session** tuned for Switch (720p panel, proper scaling).
- Package **joycond** and Switch‑specific udev snippets into a meta package.
- Optional **NVENC/NVDEC** validation for Steam Remote Play.
- Automate `extlinux.conf` root UUID discovery.
- Optional conversion to a **single superimage** with repacker for “dd” deployment.
- Add CI harness to boot‑test kernel + userspace changes with automated dmesg scrapes.

---

**Good luck, and happy hacking.**


