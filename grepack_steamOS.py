#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repack_superimage.py
GUI + CLI tool to repack for SteamOS recovery superimage by replacing
rootfs, /var, and /home from a filesystem tree extracted with img2dsk.

Works on Debian/Ubuntu, Fedora/RHEL/CentOS, and Arch Linux.

Dependencies:
  # Debian/Ubuntu
  sudo apt install -y util-linux rsync e2fsprogs file squashfs-tools python3-tk
  # Fedora/RHEL/CentOS Stream
  sudo dnf install -y util-linux rsync e2fsprogs file squashfs-tools python3-tkinter
  # Arch
  sudo pacman -S --needed util-linux rsync e2fsprogs file squashfs-tools tk

CLI Usage:
  sudo ./grepack_steamOS.py --old steamdeck.img --root /mnt/steamOS --out new_steamdeck.img
  sudo ./grepack_steamOS.py --old steamdeck.img --root /mnt/steamOS --out new.img --no-var
  sudo ./grepack_steamOS.py --old steamdeck.img --root /mnt/steamOS --out new.img --no-home

GUI:
  sudo ./grepack_steamOS.py --gui
"""

import argparse
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass

# ---------------- shell helpers ----------------

def sh(cmd, check=True, capture=True):
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def is_squashfs(path: Path) -> bool:
    try:
        out = sh(["file", "-b", str(path)]).stdout.lower()
        return "squashfs" in out
    except Exception:
        return False

def is_ext_image(path: Path) -> bool:
    try:
        out = sh(["file", "-b", str(path)]).stdout.lower()
        return any(t in out for t in ("ext2", "ext3", "ext4"))
    except Exception:
        return False

def mount_rw(dev_or_img: str, target: Path, fstype: str | None = None, loop: bool = False):
    ensure_dir(target)
    opts = "rw,loop" if loop else "rw"
    cmd = ["mount", "-o", opts]
    if fstype:
        cmd += ["-t", fstype]
    cmd += [dev_or_img, str(target)]
    sh(cmd)

def umount(target: Path):
    subprocess.run(["umount", str(target)], check=False)

def du_bytes(path: Path) -> int:
    try:
        out = sh(["du", "-sb", str(path)]).stdout.strip().split()[0]
        return int(out)
    except Exception:
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except Exception:
                    pass
        return total

def round_up(n: int, block: int) -> int:
    return ((n + block - 1) // block) * block

# ---------------- image builders ----------------

def build_squashfs(src_dir: Path, out_file: Path, log=print):
    log(f"    mksquashfs {src_dir} → {out_file.name}")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(out_file.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    sh(["mksquashfs", str(src_dir), str(tmp), "-comp", "xz", "-noappend"])
    tmp.rename(out_file)

def build_ext4_image(src_dir: Path, out_file: Path, label: str = "", log=print):
    data = du_bytes(src_dir)
    size = data + data // 6 + (64 << 20)     # +~16% overhead + 64MiB pad
    size = max(size, 256 << 20)              # at least 256MiB
    size = round_up(size, 64 << 20)          # round to 64MiB
    log(f"    mkfs.ext4 {out_file.name} size≈{size/(1<<20):.0f}MiB (label={label})")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(out_file.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    with open(tmp, "wb") as f:
        f.truncate(size)

    mkfs_cmd = ["mkfs.ext4", "-F", "-E", "lazy_itable_init=0,lazy_journal_init=0"]
    if label:
        mkfs_cmd += ["-L", label]
    mkfs_cmd.append(str(tmp))
    sh(mkfs_cmd)
    sh(["tune2fs", "-m", "0", str(tmp)], check=False)

    mnt = Path(tempfile.mkdtemp(prefix="extimg_"))
    try:
        mount_rw(str(tmp), mnt, loop=True)
        sh(["rsync", "-aAXH", "--numeric-ids", f"{src_dir}/", f"{mnt}/"])
    finally:
        umount(mnt)
        shutil.rmtree(mnt, ignore_errors=True)

    tmp.rename(out_file)

# ---------------- replacement helpers ----------------

def replace_nested_image_in_partition(part_dev: str, name_candidates: list[str],
                                      new_inner: Path, log=print) -> None:
    mnt = Path(tempfile.mkdtemp(prefix="partmnt_"))
    try:
        mount_rw(part_dev, mnt)
        target_path = None
        for nm in name_candidates:
            cand = mnt / nm
            if cand.exists() and cand.is_file():
                target_path = cand
                break
        if target_path is None:
            files = [p for p in mnt.iterdir() if p.is_file()]
            if files:
                target_path = max(files, key=lambda p: p.stat().st_size)
            else:
                target_path = mnt / name_candidates[0]

        try:
            st = os.statvfs(mnt)
            free_bytes = st.f_bavail * st.f_frsize
            need = new_inner.stat().st_size
            old_sz = target_path.stat().st_size if target_path.exists() else 0
            if need > free_bytes + old_sz:
                raise RuntimeError(f"Not enough free space in {part_dev} for {new_inner.name}")
        except Exception:
            pass

        tmp = target_path.with_suffix(target_path.suffix + ".tmp")
        if tmp.exists():
            tmp.unlink()
        log(f"    copy {new_inner.name} → {target_path.name}")
        shutil.copy2(new_inner, tmp)
        os.replace(tmp, target_path)
        sh(["sync"], check=False)
    finally:
        umount(mnt)
        shutil.rmtree(mnt, ignore_errors=True)

def wipe_and_fill_partition_direct(part_dev: str, src_dir: Path, log=print):
    mnt = Path(tempfile.mkdtemp(prefix="partmnt_"))
    try:
        mount_rw(part_dev, mnt)
        for child in mnt.iterdir():
            if child.name == "lost+found":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try: child.unlink()
                except Exception: pass
        log(f"    rsync → {part_dev} (direct filesystem)")
        sh(["rsync", "-aAXH", "--numeric-ids", f"{src_dir}/", f"{mnt}/"])
        sh(["sync"], check=False)
    finally:
        umount(mnt)
        shutil.rmtree(mnt, ignore_errors=True)

def detect_existing_inner(part_dev: str, preferred_names: list[str]) -> tuple[Path | None, bool]:
    mnt = Path(tempfile.mkdtemp(prefix="detmnt_"))
    path = None
    sq = False
    try:
        mount_rw(part_dev, mnt)
        for nm in preferred_names:
            cand = mnt / nm
            if cand.exists() and cand.is_file():
                path = cand
                sq = is_squashfs(cand)
                return (path, sq)
        files = [p for p in mnt.iterdir() if p.is_file()]
        if files:
            cand = max(files, key=lambda p: p.stat().st_size)
            if is_squashfs(cand) or is_ext_image(cand):
                path = cand
                sq = is_squashfs(cand)
                return (path, sq)
        return (None, False)
    finally:
        umount(mnt)
        shutil.rmtree(mnt, ignore_errors=True)

# ---------------- repack core ----------------

@dataclass
class RepackOptions:
    include_var: bool = True
    include_home: bool = True

def repack(old_img: Path, root_tree: Path, out_img: Path, opts: RepackOptions, log=print, set_progress=lambda _p: None):
    if not old_img.exists():
        raise FileNotFoundError(f"Old superimage not found: {old_img}")
    if not root_tree.exists():
        raise FileNotFoundError(f"Root tree not found: {root_tree}")

    workdir = Path(tempfile.mkdtemp(prefix="repack_"))
    mounts = []
    loops = []

    def cleanup():
        for m in reversed(mounts):
            try: umount(m)
            except Exception: pass
        for ld in reversed(loops):
            try: sh(["losetup", "-d", ld], check=False)
            except Exception: pass
        shutil.rmtree(workdir, ignore_errors=True)
    atexit.register(cleanup)

    set_progress(2); log(f"[+] Copy base image:\n    {old_img} → {out_img}")
    shutil.copy2(old_img, out_img)

    set_progress(5); log("[+] Attach output image (loop + partitions)")
    loopdev = sh(["losetup", "--find", "--show", "-P", str(out_img)]).stdout.strip()
    if not loopdev:
        raise RuntimeError("Failed to attach loop device for output image")
    loops.append(loopdev)

    # Partition mapping from prior context:
    p3 = f"{loopdev}p3"  # rootfs-A
    p4 = f"{loopdev}p4"  # var-A
    p5 = f"{loopdev}p5"  # home

    # ---- ROOTFS ----
    set_progress(15); log("[+] Replace rootfs …")
    preferred_root_names = ["rootfs-A.img", "rootfs.img", "rootfs.squashfs", "filesystem.squashfs", "arch.squashfs"]
    exists_path, was_squashfs = detect_existing_inner(p3, preferred_root_names)

    tmpdir = workdir / "build"
    ensure_dir(tmpdir)

    # choose output filename
    root_out = tmpdir / ((exists_path.name if exists_path else "rootfs.squashfs"))
    if was_squashfs or (exists_path and is_squashfs(exists_path)):
        log("    - Detected squashfs root; building squashfs …")
        build_squashfs(root_tree, root_out, log=log)
    else:
        if exists_path and is_ext_image(exists_path):
            root_out = tmpdir / exists_path.name
        else:
            root_out = tmpdir / "rootfs-A.img"
        log("    - Using ext4 image for root; building ext4 …")
        build_ext4_image(root_tree, root_out, label="rootfs-A", log=log)

    if exists_path:
        replace_nested_image_in_partition(p3, preferred_root_names, root_out, log=log)
    else:
        log("    - Partition appears to be direct rootfs; replacing contents …")
        wipe_and_fill_partition_direct(p3, root_tree, log=log)

    # ---- VAR ----
    if opts.include_var:
        set_progress(55); log("[+] Replace /var …")
        var_src = root_tree / "var"
        if var_src.exists():
            preferred_var_names = ["var-A.img", "var.img"]
            v_exists, _ = detect_existing_inner(p4, preferred_var_names)
            var_out = tmpdir / ((v_exists.name if v_exists else "var-A.img"))
            build_ext4_image(var_src, var_out, label="var-A", log=log)
            if v_exists:
                replace_nested_image_in_partition(p4, preferred_var_names, var_out, log=log)
            else:
                wipe_and_fill_partition_direct(p4, var_src, log=log)
        else:
            log("    - WARNING: /var not found in root tree; skipped")
    else:
        log("[+] Skipping /var (per flag)")

    # ---- HOME ----
    if opts.include_home:
        set_progress(75); log("[+] Replace /home …")
        home_src = root_tree / "home"
        if home_src.exists():
            preferred_home_names = ["home.img"]
            h_exists, _ = detect_existing_inner(p5, preferred_home_names)
            home_out = tmpdir / ((h_exists.name if h_exists else "home.img"))
            build_ext4_image(home_src, home_out, label="home", log=log)
            if h_exists:
                replace_nested_image_in_partition(p5, preferred_home_names, home_out, log=log)
            else:
                wipe_and_fill_partition_direct(p5, home_src, log=log)
        else:
            log("    - WARNING: /home not found in root tree; skipped")
    else:
        log("[+] Skipping /home (per flag)")

    set_progress(95); log("[+] Sync & detach …")
    sh(["sync"], check=False)
    set_progress(100); log(f"[✓] Repack complete:\n    {out_img}")
    log("    (ESP/EFI partitions preserved from the old image.)")

# ---------------- CLI ----------------

def run_cli(args):
    if os.geteuid() != 0:
        print("Please run as root (sudo).", file=sys.stderr)
        sys.exit(1)
    old_img = Path(args.old).resolve()
    root_tree = Path(args.root).resolve()
    out_img = Path(args.out).resolve()
    opts = RepackOptions(include_var=not args.no_var, include_home=not args.no_home)
    try:
        repack(old_img, root_tree, out_img, opts)
    except subprocess.CalledProcessError as e:
        sys.stderr.write((e.stderr or e.stdout or str(e)) + "\n")
        sys.exit(e.returncode)
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)

# ---------------- GUI ----------------

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, N, S, E, W
    from tkinter.ttk import Frame, Label, Entry, Button, Checkbutton, Progressbar, Separator
    import threading

    class App:
        def __init__(self, root: tk.Tk):
            root.title("repack_superimage — Repack superimage from edited root (rootfs + var + home)")
            root.minsize(820, 340)

            self.old_img = tk.StringVar()
            self.root_dir = tk.StringVar()
            self.out_img = tk.StringVar()
            self.include_var = tk.BooleanVar(value=True)
            self.include_home = tk.BooleanVar(value=True)
            self.log_var = tk.StringVar(value="")

            frm = Frame(root, padding=12)
            frm.grid(row=0, column=0, sticky=N+S+E+W)
            for i in range(3): frm.columnconfigure(i, weight=1 if i==1 else 0)

            r = 0
            Label(frm, text="Old superimage (.img):").grid(row=r, column=0, sticky=E, padx=6, pady=6)
            Entry(frm, textvariable=self.old_img).grid(row=r, column=1, sticky=E+W, padx=6, pady=6)
            Button(frm, text="Browse", command=self.pick_old).grid(row=r, column=2, sticky=W, padx=6, pady=6)

            r += 1
            Label(frm, text="Edited root directory:").grid(row=r, column=0, sticky=E, padx=6, pady=6)
            Entry(frm, textvariable=self.root_dir).grid(row=r, column=1, sticky=E+W, padx=6, pady=6)
            Button(frm, text="Browse", command=self.pick_root).grid(row=r, column=2, sticky=W, padx=6, pady=6)

            r += 1
            Label(frm, text="Output superimage (.img):").grid(row=r, column=0, sticky=E, padx=6, pady=6)
            Entry(frm, textvariable=self.out_img).grid(row=r, column=1, sticky=E+W, padx=6, pady=6)
            Button(frm, text="Save As", command=self.pick_out).grid(row=r, column=2, sticky=W, padx=6, pady=6)

            r += 1
            Separator(frm).grid(row=r, column=0, columnspan=3, sticky=E+W, pady=6)

            r += 1
            Checkbutton(frm, text="Include /var", variable=self.include_var).grid(row=r, column=1, sticky=W, padx=6, pady=2)
            r += 1
            Checkbutton(frm, text="Include /home", variable=self.include_home).grid(row=r, column=1, sticky=W, padx=6, pady=2)

            r += 1
            Separator(frm).grid(row=r, column=0, columnspan=3, sticky=E+W, pady=6)

            r += 1
            Button(frm, text="Repack", command=self.start).grid(row=r, column=0, sticky=E+W, padx=6, pady=8)
            Button(frm, text="Quit", command=root.quit).grid(row=r, column=2, sticky=E+W, padx=6, pady=8)

            r += 1
            self.pb = Progressbar(frm, maximum=100)
            self.pb.grid(row=r, column=0, columnspan=3, sticky=E+W, padx=6, pady=6)

            r += 1
            Label(frm, text="Log:").grid(row=r, column=0, sticky=W, padx=6)
            r += 1
            self.log_label = Label(frm, textvariable=self.log_var, anchor="w", justify="left")
            self.log_label.grid(row=r, column=0, columnspan=3, sticky=E+W, padx=6)

            self.root = root
            self.worker = None

            if os.geteuid() != 0:
                messagebox.showerror("Root required", "Please run this program with sudo (root).")

        def pick_old(self):
            p = filedialog.askopenfilename(title="Select old superimage (.img)",
                                           filetypes=[("Disk images","*.img"),("All files","*.*")])
            if p: self.old_img.set(p)

        def pick_root(self):
            p = filedialog.askdirectory(title="Select edited root directory")
            if p: self.root_dir.set(p)

        def pick_out(self):
            p = filedialog.asksaveasfilename(title="Save new superimage as",
                                             defaultextension=".img",
                                             filetypes=[("Disk images","*.img"),("All files","*.*")])
            if p: self.out_img.set(p)

        def append_log(self, line: str):
            prev = self.log_var.get()
            new = (prev + ("\n" if prev else "") + line)[-25000:]
            self.log_var.set(new)
            self.root.update_idletasks()

        def set_progress(self, pct: int):
            self.pb["value"] = max(0, min(100, pct))
            self.root.update_idletasks()

        def start(self):
            old_img = Path(self.old_img.get().strip()) if self.old_img.get().strip() else None
            root_dir = Path(self.root_dir.get().strip()) if self.root_dir.get().strip() else None
            out_img = Path(self.out_img.get().strip()) if self.out_img.get().strip() else None
            if not old_img or not old_img.exists():
                messagebox.showerror("Missing", "Please choose a valid old superimage (.img).")
                return
            if not root_dir or not root_dir.exists():
                messagebox.showerror("Missing", "Please choose a valid edited root directory.")
                return
            if not out_img:
                messagebox.showerror("Missing", "Please choose an output .img path.")
                return

            include_var = bool(self.include_var.get())
            include_home = bool(self.include_home.get())

            def task():
                try:
                    opts = RepackOptions(include_var=include_var, include_home=include_home)
                    repack(old_img, root_dir, out_img, opts, log=self.append_log, set_progress=self.set_progress)
                    self.append_log(f"DONE → {out_img}")
                    from tkinter import messagebox as mb
                    mb.showinfo("Done", f"Repack complete:\n{out_img}")
                except subprocess.CalledProcessError as e:
                    err = e.stderr or e.stdout or str(e)
                    self.append_log(err.strip())
                    from tkinter import messagebox as mb
                    mb.showerror("Error", err)
                except Exception as e:
                    self.append_log(str(e))
                    from tkinter import messagebox as mb
                    mb.showerror("Error", str(e))

            import threading
            self.worker = threading.Thread(target=task, daemon=True)
            self.worker.start()

    root = tk.Tk()
    App(root)
    root.mainloop()

# ---------------- entry ----------------

def main():
    parser = argparse.ArgumentParser(description="Repack a superimage (.img) with new rootfs/var/home (GUI + CLI).")
    parser.add_argument("--gui", action="store_true", help="Launch GUI")
    parser.add_argument("--old", type=str, help="Path to old/original superimage (.img)")
    parser.add_argument("--root", type=str, help="Path to edited filesystem tree (from img2dsk)")
    parser.add_argument("--out", type=str, help="Path for new superimage (.img)")
    # Optional skips
    try:
        parser.add_argument("--no-var", action=argparse.BooleanOptionalAction, default=False,
                            help="Skip /var replacement (default: include)")
        parser.add_argument("--no-home", action=argparse.BooleanOptionalAction, default=False,
                            help="Skip /home replacement (default: include)")
    except AttributeError:
        parser.add_argument("--no-var", action="store_true", help="Skip /var replacement")
        parser.add_argument("--no-home", action="store_true", help="Skip /home replacement")
    args = parser.parse_args()

    if args.gui:
        run_gui()
        return

    # CLI mode
    if not (args.old and args.root and args.out):
        print("Usage (CLI): sudo ./repack_superimage.py --old steamdeck.img --root /mnt/steamOS --out new_steamdeck.img [--no-var] [--no-home]", file=sys.stderr)
        print("Or use GUI:  sudo ./repack_superimage.py --gui", file=sys.stderr)
        sys.exit(2)

    if os.geteuid() != 0:
        print("Please run as root (sudo).", file=sys.stderr)
        sys.exit(1)

    opts = RepackOptions(include_var=not args.no_var, include_home=not args.no_home)
    run_cli(args)

if __name__ == "__main__":
    main()
