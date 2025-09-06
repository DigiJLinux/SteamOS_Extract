# img2dsk & repack_superimage Tools

These tools allow you to **extract** and **repack** SteamOS Recovery superimages that contain multiple nested filesystem partitions (`rootfs`, `var`, `home`, EFI/ESP).

- `img2dsk` — Extract the Linux filesystem tree (rootfs, /var, /home) from a superimage into a normal directory so you can edit it.
- `repack_superimage` — Repack a new superimage by replacing rootfs, /var, and /home from an edited tree.

Both tools support:
- **Debian/Ubuntu**
- **Fedora/RHEL/CentOS**
- **Arch Linux**
- **GUI mode (Tkinter)** and **CLI mode**


The only issue is that in future recovery images valve may change the partition layout. If they do, the scripts will need to be updated.

Works in WSL

---

## 📦 Dependencies

### Debian / Ubuntu
    sudo apt update
    sudo apt install -y util-linux rsync e2fsprogs file squashfs-tools python3-tk

### Fedora / RHEL / CentOS Stream
    sudo dnf install -y util-linux rsync e2fsprogs file squashfs-tools python3-tkinter

### Arch Linux
    sudo pacman -S --needed util-linux rsync e2fsprogs file squashfs-tools tk

---

## 🔎 Extracting a Superimage (`img2dsk`)

`img2dsk` mounts the partitions of a superimage, detects nested images (`rootfs-A.img`, `var-A.img`, `home.img`, squashfs, etc.), and copies them out into a normal directory tree.  
This allows you to `chroot`, edit configs, rebuild packages, etc.

### CLI Usage
    # Extract full rootfs + var + home
    sudo ./img2dsk.py steamdeck.img /mnt/steamOS

- Rootfs is extracted into `/mnt/steamOS/`
- `/var` into `/mnt/steamOS/var/`
- `/home` into `/mnt/steamOS/home/`

### GUI Usage
    sudo ./img2dsk_arch.py --gui      # Arch version
    sudo ./img2dsk_fedora.py --gui    # Fedora/RHEL version
    sudo ./img2dsk.py --gui           # Generic version (if provided)

In the GUI, just select:
1. The `.img` superimage
2. The output directory
3. Click **Extract**

---

## 🔄 Repacking a Superimage (`repack_superimage`)

`repack_superimage` takes an **old superimage** and an **edited filesystem tree** (from `img2dsk`), and creates a new `.img`.  
It preserves GPT and EFI/ESP partitions, while replacing rootfs, /var, and /home.

### CLI Usage
    # Repack with rootfs, var, and home
    sudo ./repack_superimage.py --old steamdeck.img --root /mnt/steamOS --out new_steamdeck.img

    # Skip /var
    sudo ./repack_superimage.py --old steamdeck.img --root /mnt/steamOS --out new.img --no-var

    # Skip /home
    sudo ./repack_superimage.py --old steamdeck.img --root /mnt/steamOS --out new.img --no-home

### GUI Usage
    sudo ./repack_superimage.py --gui

In the GUI, select:
1. Old/original superimage (`steamdeck.img`)
2. Edited root directory (where `img2dsk` extracted)
3. Output `.img` path
4. Choose whether to include `/var` and `/home`
5. Click **Repack**

---

## ⚠️ Notes & Caveats

- Both tools **must be run as root** (`sudo`) to attach loop devices and mount partitions.
- ESP/EFI partitions are preserved as-is (bootloaders not rebuilt).
- Repack script tries to **auto-detect squashfs vs ext4 rootfs** and rebuild accordingly.
- On **Fedora/RHEL with SELinux**, after booting from the repacked image, run:
      sudo restorecon -R /
  to fix SELinux file contexts.
- If your edited filesystem tree is larger than the existing partition space, the repack will fail with a "not enough space" warning.  
  In that case, you must resize partitions or reduce contents.

---

## 🗂 File Overview

- **`img2dsk.py`**  
  Simple cross-distro extractor (CLI only).
- **`img2dsk_arch.py` / `img2dsk_fedora.py`**  
  Extraction with CLI + GUI for Arch and Fedora/RHEL.
- **`repack_superimage.py`**  
  Repacking tool (CLI + GUI) for all supported distros.

---

## Example Workflow

1. **Extract**
       sudo ./img2dsk.py steamdeck.img /mnt/steamOS

2. **Edit**
       sudo arch-chroot /mnt/steamOS
       # make changes: install packages, edit configs, etc.
       exit

3. **Repack**
       sudo ./repack_superimage.py --old steamdeck.img --root /mnt/steamOS --out steamdeck_custom.img

4. **Write to USB / SD card**
       sudo dd if=steamdeck_custom.img of=/dev/sdX bs=4M status=progress
