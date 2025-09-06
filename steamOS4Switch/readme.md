# (WIP) SteamOS (Arch) on Nintendo Switch via Switchroot L4T — Deep-Dive Engineering Guide

> **Status:** Work-in-Progress (WIP). Expect rough edges; contributions welcome.
>
> **Scope changes vs previous draft:** We are **not repacking** into a monolithic superimage. We will deploy the **`rootfs`**, **`home`**, and **`var`** trees directly to the SD card (or target media). This version also adds **ARM (aarch64) support guidance** for running an Arch/SteamOS-style userspace on the Switch (Tegra210). This is just an idea at this point

---

## 0) What we are doing (high level)

- Use your tools to **extract** the SteamOS **Arch** userspace from the SteamOS recovery Image into directories: `rootfs/`, `home/`, `var/`.
- **Inject** the **Switchroot L4T** platform stack (kernel, DTBs, firmware, NVIDIA userspace, udev rules) into that userspace.
- **Deploy** these directories directly onto SD partitions: **FAT32 `/boot`** + **ext4 `/`**.
- Ensure the userspace is either **native aarch64** or provide **ARM support** layers to run x86_64‑centric software (Steam) on ARM:
  - **Preferred:** **Arch Linux ARM** base (native aarch64 userspace) + x86_64 compatibility via **box64**/**FEX‑EMU** for Steam.
  - **Not recommended:** Keep a mostly x86_64 root and emulate everything — boot/userland will be painful. Stick to an **aarch64 root**.

---

## 1) Requirements & Warnings

- **RCM‑capable Switch**, Hekate payload chain, and Switchroot **L4T‑Loader**.
- Linux host (Debian/Ubuntu, Fedora/RHEL, or Arch) with root privileges.
- MicroSD (UHS‑I, ≥64 GB recommended).
- **Switchroot L4T release** (unpacked) for the Switch (contains `/boot/Image`, `/boot/dtb/tegra210-icosa.dtb`, `/lib/modules/<kver>`, firmware, NVIDIA userspace).
- Extractor + injector scripts you already have:
  - `img2dsk.py` (or GUI variant) — to get `rootfs/`, `var/`, `home/`.
  - `steamOS_switch_l4t_inject.sh` — injects L4T stack (firmware, libs, DTBs, kernel if desired).

> ⚠️ You can soft‑brick a Linux SD install if you misconfigure boot. Back up first.

---

## 2) Host dependencies

### Debian/Ubuntu
```
sudo apt update
sudo apt install -y util-linux rsync e2fsprogs file squashfs-tools python3-tk gdisk parted udev qemu-user-static binfmt-support
```

### Fedora/RHEL/CentOS Stream
```
sudo dnf install -y util-linux rsync e2fsprogs file squashfs-tools python3-tkinter gdisk parted udev qemu-user-static
```

### Arch Linux
```
sudo pacman -S --needed util-linux rsync e2fsprogs file squashfs-tools tk gptfdisk parted udev qemu-user-static-binfmt
```

> `qemu-user-static` + binfmt is optional but helpful if you need to **chroot** into an aarch64 tree from an x86_64 host.

---

## 3) Partitioning the SD (no repack)

We are **not** creating a monolithic `.img`. We’ll copy directories straight onto SD partitions.

**Recommended layout (`/dev/sdX`):**

- **p1 (FAT32, 1–2 GB)** → `/boot` (visible to Hekate/L4T‑Loader)
- **p2 (ext4, rest)** → `/` (root filesystem)

Create & format (destructive!):
```
sudo parted /dev/sdX --script \
  mklabel gpt \
  mkpart boot fat32 1MiB 2049MiB \
  set 1 boot on \
  mkpart root ext4 2049MiB 100%
sudo mkfs.vfat -F 32 -n BOOT /dev/sdX1
sudo mkfs.ext4 -L STEAMOS_ROOT /dev/sdX2
```

Mount for staging:
```
sudo mkdir -p /mnt/sd/boot /mnt/sd/root
sudo mount /dev/sdX1 /mnt/sd/boot
sudo mount /dev/sdX2 /mnt/sd/root
```

---

## 4) Extract the SteamOS‑style userspace

Use your extractor:
```
sudo ./img2dsk.py steamdeck.img /tmp/steamOS
```
You should have:
```
/tmp/steamOS/            # root filesystem 
/tmp/steamOS/var/        # var
/tmp/steamOS/home/       # home
```

> If your extractor wrote a **single** tree that already contains `var/` and `home/`, that’s fine — treat `/tmp/steamOS` as rootfs.

---

## 5) ARM support strategy (critical)

### ✅ Preferred: **Native aarch64 rootfs** + **Steam via box64/FEX**

- Start with a **native aarch64** userspace (e.g., **Arch Linux ARM** base) and layer Steam on top via **box64** (and optionally **FEX‑EMU**). This keeps systemd/init, coreutils, shells, etc. native ARM — faster and far simpler to maintain.
- Your extracted SteamOS “look‑and‑feel” can be approximated by **installing the Steam Deck packages** that exist for ARM (gamescope, session glue) and substituting the x86_64 bits with emulation.

#### Convert/align your root to Arch Linux ARM repo
If your extracted tree isn’t already ALARM:
1. Replace pacman repo defs:
   - `/etc/pacman.d/mirrorlist` →
     ```
     Server = http://mirror.archlinuxarm.org/$arch/$repo
     ```
   - `/etc/pacman.conf` → ensure `Architecture = aarch64` and standard `[core] [extra] [community]` sections for ALARM.
2. Initialize keys:
   ```
   sudo arch-chroot /tmp/steamOS pacman-key --init
   sudo arch-chroot /tmp/steamOS pacman-key --populate archlinuxarm
   ```
3. Update base (from host, with binfmt/qemu if needed):
   ```
   sudo arch-chroot /tmp/steamOS pacman -Syu --noconfirm base base-devel
   ```

#### Install graphics/desktop pieces (aarch64)
```
sudo arch-chroot /tmp/steamOS pacman -S --needed \
  linux-firmware mesa-utils glfw-wayland \
  pipewire pipewire-alsa pipewire-pulse wireplumber \
  xorg-server xorg-xinit wayland \
  gamescope mangohud gamemode \
  seatd libinput
```

> The **actual GL/VK** on the Switch comes from **L4T NVIDIA userspace** you’ll inject later — the Mesa bits are mostly for tooling and fallback.

#### Add **box64** and/or **FEX‑EMU**
- **box64** (for x86_64 userspace like Steam):
  - From AUR on-device (yay) or build from source:
    ```
    sudo arch-chroot /tmp/steamOS pacman -S --needed git cmake make gcc
    sudo arch-chroot /tmp/steamOS bash -lc '
      git clone https://github.com/ptitSeb/box64 /opt/box64 && \
      cd /opt/box64 && mkdir build && cd build && \
      cmake .. -DCMAKE_BUILD_TYPE=Release -DARM_DYNAREC=ON && \
      make -j$(nproc) && sudo make install
    '
    ```
- **FEX‑EMU** (faster x86_64 emu JIT, optional):
  - Build or install a package (`fex-emu`), then initialize the rootfs:
    ```
    sudo arch-chroot /tmp/steamOS pacman -S --needed python git cmake ninja gcc glibc
    # Refer to FEX docs for current build steps; package availability varies.
    ```

#### Steam on ARM (concept)
- Use the **Steam Linux Runtime (Soldier)** with **box64/FEX** to launch `steamwebhelper` and Steam client.
- Typical environment (tweak as needed):
  ```
  export BOX64_PATH=/usr/lib/steam:/usr/lib/steam/lib64
  export BOX64_LD_LIBRARY_PATH=/usr/lib:/lib
  gamescope -f -- box64 /usr/lib/steam/bin/steam -gamepadui
  ```
  The exact paths depend on where Steam runtime is installed on your ARM root. Expect iteration.

### 🚫 Not recommended: x86_64 base root on ARM
Running systemd and base userspace via emulation is fragile. Boot, init, and services should be **native aarch64**. Emulate **only** application layer (Steam).

---

## 6) Inject Switchroot L4T into the rootfs

Run the purpose‑built injector against the **extracted tree** (not the SD yet):
```
sudo ./steamOS_switch_l4t_inject.sh \
  --root /tmp/steamOS \
  --switchroot /mnt/switchroot_l4t \
  --take-kernel \
  --write-extlinux
```
This will:
- Copy **firmware** (`/lib/firmware/{nvidia,brcm,rtl*,host1x,tegra*}`).
- Copy **NVIDIA userspace** (`/usr/lib/tegra`, `/usr/lib/nvidia`, relevant GL/VK/CUDA SONAMEs).
- Set up **udev** rules/helpers; blacklist **nouveau**; add `/etc/ld.so.conf.d/tegra-nvidia.conf`; run `ldconfig -r`.
- **Adopt Switchroot kernel** (`/boot/Image` or `Image.gz`) + **DTBs** (`/boot/dtb/tegra210-icosa.dtb`) + `/lib/modules/<kver>`.
- Write **`/boot/extlinux/extlinux.conf`** (edit `root=` later).

> Using the Switchroot kernel is strongly recommended so modules match.

---

## 7) Deploy to SD (copy directories — no repack)

Copy your prepared tree onto mounted SD partitions:

```
# rootfs + var + home (preserve ownership/xattrs/ACLs)
sudo rsync -aAXH --numeric-ids /tmp/steamOS/ /mnt/sd/root/

# boot payloads (kernel, dtb, initramfs, extlinux)
sudo rsync -aH /tmp/steamOS/boot/ /mnt/sd/boot/
```

Confirm `/mnt/sd/boot/extlinux/extlinux.conf` exists and references:
- `LINUX /boot/Image` **or** `/boot/Image.gz`
- `FDT /boot/dtb/tegra210-icosa.dtb`
- `INITRD /boot/initramfs` (if present)
- `APPEND root=LABEL=STEAMOS_ROOT rw rootwait console=tty0 quiet splash`

Unmount:
```
sync
sudo umount /mnt/sd/boot /mnt/sd/root
```

Insert SD into Switch.

---

## 8) First boot & post‑install

- Boot Hekate → **L4T‑Loader** → pick Linux → extlinux entry.
- On first login:
  ```
  sudo ldconfig
  sudo udevadm control --reload
  sudo udevadm trigger
  ```
- If no initramfs was provided by Switchroot, generate one **on‑device**:
  - **mkinitcpio:**
    ```
    sudo mkinitcpio -P
    ```
  - **dracut:**
    ```
    sudo dracut --force
    ```

### Core services
- Enable PipeWire stack:
  ```
  systemctl --user enable --now pipewire pipewire-pulse wireplumber || true
  ```
- Input (Joy‑Cons):
  ```
  sudo pacman -S --needed joycond
  sudo systemctl enable --now joycond
  ```
- Gamescope session sanity:
  ```
  gamescope -f -- glxinfo | head
  vulkaninfo | head
  ```

> Ensure GL/VK pick up **NVIDIA L4T** ICD/GLX providers (not Mesa LLVMPipe).

---

## 9) Steam on ARM quick‑start (experimental)

- Install Steam runtime (paths differ; choose your approach). On Arch ARM, you may pull `steam` from an x86_64 repo and run under **box64/FEX**, or use community recipes that vendor Steam runtime.
- Launch under Gamescope via **box64**:
  ```
  export BOX64_NOBANNER=1
  export BOX64_PATH=/usr/lib/steam:/usr/lib/steam/lib64
  export BOX64_LD_LIBRARY_PATH=/usr/lib:/lib
  gamescope -f -- box64 /usr/lib/steam/bin/steam -gamepadui
  ```
- Expect to iterate on library paths and env vars. Some users prefer **FEX‑EMU** for better performance; configure `FEX_ROOTFS` as per FEX docs.

> This part is still evolving on ARM handhelds — consider community guides for Steam on ARM with box64/FEX.

---

## 10) Troubleshooting

- **Black screen after handoff:** wrong DTB or module mismatch. Confirm `FDT` path and Switchroot kernel used; check `/lib/modules/<kver>` exists.
- **Cannot mount root:** wrong `root=`; prefer `LABEL=STEAMOS_ROOT` or `UUID=<…>`; `blkid` to discover.
- **EGL/Vulkan errors:** verify `/usr/lib/tegra`, `/usr/lib/nvidia`, `ld.so.conf.d/tegra-nvidia.conf`; run `ldconfig`.
- **No Wi‑Fi/BT:** verify `brcm` & BT firmware blobs under `/lib/firmware`; check `dmesg` for `brcmfmac`.
- **No audio:** PipeWire active? UCM profiles present for Tegra? Check `dmesg` codec probe and `pw-cli ls`.
- **Steam fails to launch:** adjust **box64/FEX** env and library paths; try headless first, then under gamescope.

Useful logs:
```
dmesg -T | less
journalctl -b --no-pager | less
cat /proc/cmdline
lsmod
```

---

## 11) Notes on power/perf

- Governor:
  ```
  cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
  ```
- HDMI scaling vs internal 720p panel — let gamescope handle scaling when possible.
- Suspend/resume may be unstable; prefer clean shutdown/halt for now.

---

## 12) References (Switchroot / L4T)

- Switchroot Linux hub: https://wiki.switchroot.org/wiki/linux
- Distributions: https://wiki.switchroot.org/wiki/linux/linux-distributions
- Install guides (Ubuntu/Fedora): 
  - Jammy 22.04: https://wiki.switchroot.org/wiki/linux/l4t-ubuntu-jammy-installation-guide
  - Noble 24.04: https://wiki.switchroot.org/wiki/linux/l4t-ubuntu-noble-installation-guide
  - Fedora 41: https://wiki.switchroot.org/wiki/linux/l4t-fedora-installation-guide-1
- Boot config (hekate/L4T‑Loader keys): https://wiki.switchroot.org/wiki/linux/linux-boot-configuration
- Bootstack docs (L4T‑Loader/UBoot/extlinux): https://wiki.switchroot.org/wiki/linux/linux-bootstack-documentation
- USB/eMMC boot: https://wiki.switchroot.org/wiki/linux/linux-usb-or-emmc-boot

---

## 13) Roadmap / To‑Do (WIP)

- Prebuilt **ALARM + L4T** root tarball for faster bootstrapping.
- Packaged recipes for **box64**, **FEX‑EMU**, **gamescope** tuned for Switch.
- Automate `extlinux.conf` root device discovery.
- Power management tuning (clocks, DVFS) specific to Switch panels/docks.
- End‑to‑end script to prepare SD automatically (partition → copy → boot).

---

**Good luck, and happy hacking.**
