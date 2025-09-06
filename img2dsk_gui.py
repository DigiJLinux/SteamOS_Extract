#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
img2dsk_gui.py
Extract a SteamOS superimage into a normal
directory tree — including rootfs, /var, and /home.

Usage:
  sudo ./img2dsk_gui.py

Features:
  • Pick the superimage and an output folder
  • One-click “Extract” (rootfs + var + home)
  • Live log + progress bar
  • Automatic loop-device attach/detach and mount cleanup
  • Handles nested ext image files and squashfs rootfs

Requirements (Debian/Ubuntu):
  sudo apt install util-linux rsync squashfs-tools file python3-tk
"""

import os
import sys
import atexit
import shutil
import tempfile
import threading
import subprocess
from pathlib import Path
from tkinter import Tk, StringVar, filedialog, messagebox, N, S, E, W
from tkinter.ttk import Frame, Label, Entry, Button, Progressbar, Separator

# ---------------- helpers ----------------

def run(cmd, check=True, capture=True):
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def is_squashfs(path: Path) -> bool:
    try:
        out = run(["file", "-b", str(path)]).stdout.lower()
        return "squashfs" in out
    except Exception:
        return False

def is_ext_image(path: Path) -> bool:
    try:
        out = run(["file", "-b", str(path)]).stdout.lower()
        return any(x in out for x in ("ext2", "ext3", "ext4"))
    except Exception:
        return False

def mount_ro(dev_or_img: str, target: Path, fstype: str|None=None, loop: bool=False):
    ensure_dir(target)
    opts = "ro,loop" if loop else "ro"
    cmd = ["mount", "-o", opts]
    if fstype:
        cmd += ["-t", fstype]
    cmd += [dev_or_img, str(target)]
    run(cmd)

def umount(path: Path):
    subprocess.run(["umount", str(path)], check=False)

# ---------------- core logic ----------------

class Extractor:
    def __init__(self, log_fn, prog_fn):
        self.log = log_fn
        self.set_progress = prog_fn
        self.mounts = []
        self.loops = []
        self.work = Path(tempfile.mkdtemp(prefix="img2dsk_gui_"))
        atexit.register(self.cleanup)

    def cleanup(self):
        # unmount in reverse
        for m in reversed(self.mounts):
            try: umount(m)
            except Exception: pass
        # detach loops in reverse
        for ld in reversed(self.loops):
            try: subprocess.run(["losetup", "-d", ld], check=False)
            except Exception: pass
        try:
            shutil.rmtree(self.work, ignore_errors=True)
        except Exception:
            pass

    def _extract_into_subdir(self, dev: str, outdir: Path, sub: str):
        subdir = outdir / sub
        ensure_dir(subdir)
        part_mnt = self.work / f"{sub}_part"
        mount_ro(dev, part_mnt)
        self.mounts.append(part_mnt)

        files = sorted([p for p in part_mnt.iterdir() if p.is_file()],
                       key=lambda x: x.stat().st_size, reverse=True)
        if files and is_ext_image(files[0]):
            inner = files[0]
            inner_mnt = self.work / f"{sub}_inner"
            mount_ro(str(inner), inner_mnt, loop=True)
            self.mounts.append(inner_mnt)
            self.log(f"  rsync → {subdir} (from {inner.name})")
            run(["rsync", "-aAXH", "--numeric-ids", f"{inner_mnt}/", f"{subdir}/"], check=True)
        else:
            self.log(f"  rsync → {subdir} (from partition)")
            run(["rsync", "-aAXH", "--numeric-ids", f"{part_mnt}/", f"{subdir}/"], check=True)

    def extract_all(self, superimg: Path, outdir: Path):
        self.set_progress(0); self.log(f"[+] Attaching superimage: {superimg}")
        loopdev = run(["losetup", "--find", "--show", "-P", str(superimg)]).stdout.strip()
        if not loopdev:
            raise RuntimeError("Failed to attach loop device for superimage")
        self.loops.append(loopdev)

        # Partitions by known layout:
        p3 = f"{loopdev}p3"  # rootfs-A
        p4 = f"{loopdev}p4"  # var-A
        p5 = f"{loopdev}p5"  # home

        ensure_dir(outdir)

        # ----- ROOTFS -----
        self.set_progress(5); self.log("[+] Extracting root filesystem…")
        root_part_mnt = self.work / "root_part"
        mount_ro(p3, root_part_mnt)
        self.mounts.append(root_part_mnt)

        preferred = ["rootfs-A.img", "rootfs.img", "rootfs.squashfs", "filesystem.squashfs", "arch.squashfs"]
        inner_root = None
        for name in preferred:
            c = root_part_mnt / name
            if c.is_file():
                inner_root = c
                break
        if inner_root is None:
            files = [p for p in root_part_mnt.iterdir() if p.is_file()]
            inner_root = max(files, key=lambda x: x.stat().st_size) if files else None

        if inner_root and is_squashfs(inner_root):
            self.log(f"  unsquashfs → {outdir} (from {inner_root.name})")
            run(["unsquashfs", "-f", "-d", str(outdir), str(inner_root)], check=True)
        elif inner_root and is_ext_image(inner_root):
            root_inner_mnt = self.work / "root_inner"
            mount_ro(str(inner_root), root_inner_mnt, loop=True)
            self.mounts.append(root_inner_mnt)
            self.log(f"  rsync → {outdir} (from {inner_root.name})")
            run(["rsync", "-aAXH", "--numeric-ids", f"{root_inner_mnt}/", f"{outdir}/"], check=True)
        else:
            self.log("  rsync → root (from partition)")
            run(["rsync", "-aAXH", "--numeric-ids", f"{root_part_mnt}/", f"{outdir}/"], check=True)

        # ----- VAR -----
        self.set_progress(55); self.log("[+] Extracting /var …")
        self._extract_into_subdir(p4, outdir, "var")

        # ----- HOME -----
        self.set_progress(80); self.log("[+] Extracting /home …")
        self._extract_into_subdir(p5, outdir, "home")

        self.set_progress(100); self.log(f"[✓] Done. Files extracted into: {outdir}")
        self.log("    (EFI partitions ignored by design.)")

# ---------------- GUI ----------------

class App:
    def __init__(self, root: Tk):
        root.title("img2dsk — Superimage → Directory (rootfs + var + home)")
        root.minsize(720, 260)

        self.image_path = StringVar()
        self.output_dir = StringVar()
        self.progress = 0

        frm = Frame(root, padding=12)
        frm.grid(row=0, column=0, sticky=N+S+E+W)
        for i in range(3): frm.columnconfigure(i, weight=1 if i==1 else 0)

        r = 0
        Label(frm, text="Superimage (.img):").grid(row=r, column=0, sticky=E, padx=6, pady=6)
        Entry(frm, textvariable=self.image_path).grid(row=r, column=1, sticky=E+W, padx=6, pady=6)
        Button(frm, text="Browse", command=self.pick_img).grid(row=r, column=2, sticky=W, padx=6, pady=6)

        r += 1
        Label(frm, text="Output directory:").grid(row=r, column=0, sticky=E, padx=6, pady=6)
        Entry(frm, textvariable=self.output_dir).grid(row=r, column=1, sticky=E+W, padx=6, pady=6)
        Button(frm, text="Browse", command=self.pick_out).grid(row=r, column=2, sticky=W, padx=6, pady=6)

        r += 1
        Separator(frm).grid(row=r, column=0, columnspan=3, sticky=E+W, pady=6)

        r += 1
        Button(frm, text="Extract", command=self.start).grid(row=r, column=0, sticky=E+W, padx=6, pady=8)
        Button(frm, text="Quit", command=root.quit).grid(row=r, column=2, sticky=E+W, padx=6, pady=8)

        r += 1
        self.pb = Progressbar(frm, maximum=100)
        self.pb.grid(row=r, column=0, columnspan=3, sticky=E+W, padx=6, pady=6)

        r += 1
        Label(frm, text="Log:").grid(row=r, column=0, sticky=W, padx=6)
        r += 1
        # Minimal rolling log using Label (simple); for long logs you might swap to a ScrolledText widget
        self.log_var = StringVar(value="")
        self.log_label = Label(frm, textvariable=self.log_var, anchor="w", justify="left")
        self.log_label.grid(row=r, column=0, columnspan=3, sticky=E+W, padx=6)

        self.root = root
        self.extractor = None
        self.worker = None

        if os.geteuid() != 0:
            messagebox.showerror("Root required", "Please run this program with sudo (root).")
    
    # ------------- UI helpers -------------

    def pick_img(self):
        p = filedialog.askopenfilename(title="Select superimage (.img)",
                                       filetypes=[("Disk images","*.img"),("All files","*.*")])
        if p: self.image_path.set(p)

    def pick_out(self):
        p = filedialog.askdirectory(title="Select output directory")
        if p: self.output_dir.set(p)

    def append_log(self, line: str):
        prev = self.log_var.get()
        new = (prev + ("\n" if prev else "") + line)[:20000]  # keep small
        self.log_var.set(new)
        self.root.update_idletasks()

    def set_progress(self, pct: int):
        self.pb['value'] = max(0, min(100, pct))
        self.root.update_idletasks()

    # ------------- Actions -------------

    def start(self):
        img = Path(self.image_path.get().strip()) if self.image_path.get().strip() else None
        out = Path(self.output_dir.get().strip()) if self.output_dir.get().strip() else None
        if not img or not img.exists():
            messagebox.showerror("Missing image", "Please choose a valid .img file.")
            return
        if not out:
            messagebox.showerror("Missing output", "Please choose an output directory.")
            return

        self.extractor = Extractor(self.append_log, self.set_progress)
        self.worker = threading.Thread(target=self._do_extract, args=(img, out), daemon=True)
        self.worker.start()

    def _do_extract(self, img: Path, out: Path):
        try:
            self.extractor.extract_all(img, out)
            messagebox.showinfo("Done", f"Extraction complete:\n{out}")
        except subprocess.CalledProcessError as e:
            err = e.stderr or e.stdout or str(e)
            self.append_log(err.strip())
            messagebox.showerror("Error", err)
        except Exception as e:
            self.append_log(str(e))
            messagebox.showerror("Error", str(e))

# ---------------- main ----------------

def main():
    root = Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
