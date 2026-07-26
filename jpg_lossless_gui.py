#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片压缩工具（GUI）
===========================
输出选项：
  · 保存位置：原文件夹(覆盖) / 自定义文件夹
  · 目标格式：原格式(无损) / WebP(无损) / PNG(无损) / JPG(有损, 质量可调)
  · 文件名后缀：自定义输出文件名附加词（留空=覆盖或原名）
  · 保持原目录结构：自定义文件夹输出时保留子目录层级
  · JPG 质量滑块：转 JPG 时生效（1-100，默认 95）
  · 拖入区：可批量拖入图片（图形框），点击缩略图可选中
  · 对比框：原图 | 结果，可“预览选中对比”在输出前查看前后效果
  · 处理完成后自动打开输出文件夹
  · 记住上次设置（config.json 持久化）
  · 支持把文件/文件夹直接拖入窗口
引擎：ect（自动下载，JPEG/PNG 原地无损最优）；转格式用 Pillow(libwebp)

运行：  python jpg_lossless_gui.py
打包：  pyinstaller --onefile --noconsole --name JpgLossless --distpath . ^
        --add-binary "bin/ect.exe;bin" jpg_lossless_gui.py
"""
import os
import sys
import json
import shutil
import queue
import threading
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import urllib.request
import zipfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(APP_DIR, "bin")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

ENGINE_ORDER = {"ect": 0, "jpegtran": 1, "jpegoptim": 2}
IMG_EXTS = (".jpg", ".jpeg", ".png", ".jpe", ".jfif")
FMT_EXT = {"WebP": ".webp", "PNG": ".png", "JPG": ".jpg"}

# 视觉主题：克制的深石板头 + 蓝绿强调，刻意避开“奶油色+衬线”等 AI 模板套路
C_BG = "#F4F6F9"
C_SURFACE = "#FFFFFF"
C_TEXT = "#1F2733"
C_MUTED = "#707A89"
C_BORDER = "#DCE2EC"
C_ACCENT = "#2F6FED"
C_ACCENT_HOVER = "#1E5BD6"
C_HEADER = "#1E2A3A"
C_SUBTLE = "#AEBED4"
C_SUCCESS = "#14935A"
C_WARN = "#B7791F"
C_ERROR = "#C0392B"
FONT_UI = "Segoe UI"


def engine_key(path):
    base = os.path.basename(path).lower().replace(".exe", "")
    return ENGINE_ORDER.get(base, 9)


def find_engine():
    cands = []
    search_dirs = []
    if getattr(sys, "_MEIPASS", None):
        search_dirs.append(os.path.join(sys._MEIPASS, "bin"))
    search_dirs.append(BIN_DIR)
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for name in ("ect.exe", "jpegtran.exe", "jpegoptim.exe"):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                cands.append(p)
    for name in ("ect", "jpegtran", "jpegoptim"):
        loc = shutil.which(name)
        if loc:
            cands.append(loc)
    if cands:
        cands.sort(key=engine_key)
        return cands[0]
    return None


def download_ect():
    os.makedirs(BIN_DIR, exist_ok=True)
    api = "https://api.github.com/repos/fhanau/Efficient-Compression-Tool/releases/latest"
    req = urllib.request.Request(api, headers={"User-Agent": "jpg-lossless"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    asset = None
    for a in data.get("assets", []):
        n = (a.get("name") or "").lower()
        if "win" in n and n.endswith(".zip"):
            asset = a
            break
    if not asset:
        raise RuntimeError("未找到 ect 的 Windows 发布包")
    url = asset["browser_download_url"]
    zip_path = os.path.join(BIN_DIR, "ect.zip")
    urllib.request.urlretrieve(url, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.filename.lower().endswith("ect.exe"):
                z.extract(info, BIN_DIR)
                src = os.path.join(BIN_DIR, info.filename)
                dst = os.path.join(BIN_DIR, "ect.exe")
                if os.path.abspath(src) != os.path.abspath(dst):
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.move(src, dst)
                break
    os.remove(zip_path)
    return os.path.join(BIN_DIR, "ect.exe")


def collect_files(paths):
    out = []
    for p in paths:
        if os.path.isfile(p) and p.lower().endswith(IMG_EXTS):
            out.append(p)
        elif os.path.isdir(p):
            for root, _, files in os.walk(p):
                for f in files:
                    if f.lower().endswith(IMG_EXTS):
                        out.append(os.path.join(root, f))
    return list(dict.fromkeys(out))


def run_engine(engine, src, dst):
    """原地无损：JPEG 优化霍夫曼表+删元数据；PNG 重压缩(无损)。dst 必须是新文件。"""
    base = os.path.basename(engine).lower()
    ext = os.path.splitext(src)[1].lower()
    if "jpegtran" in base:
        if ext == ".png":
            raise RuntimeError("jpegtran 不支持 PNG，请使用 ect")
        subprocess.run([engine, "-optimize", "-progressive", "-copy", "none",
                        "-outfile", dst, src], check=True)
    elif "jpegoptim" in base:
        if ext == ".png":
            raise RuntimeError("jpegoptim 不支持 PNG，请使用 ect")
        shutil.copy2(src, dst)
        subprocess.run([engine, "--strip-all", "--all-progressive", dst], check=True)
    else:  # ect：JPEG / PNG 均无损
        shutil.copy2(src, dst)
        if ext == ".png":
            subprocess.run([engine, "-9", dst], check=True)
        else:
            subprocess.run([engine, "-9", "-strip", dst], check=True)


def to_webp_lossless(src, dst):
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im.save(dst, "WEBP", lossless=True, method=6)


def compress_one(src, dst, fmt, eng, quality):
    """按当前设置把 src 压缩到 dst，返回新文件大小（字节）。"""
    ext = os.path.splitext(src)[1].lower() if fmt == "原格式" else FMT_EXT[fmt]
    if fmt == "原格式":
        if "ect" not in os.path.basename(eng).lower() and ext == ".png":
            raise RuntimeError("该引擎不支持 PNG，需 ect 引擎")
        run_engine(eng, src, dst)
    elif fmt == "WebP":
        to_webp_lossless(src, dst)
    elif fmt == "PNG":
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            ImageOps.exif_transpose(im).save(dst, "PNG")
    elif fmt == "JPG":
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            ImageOps.exif_transpose(im).convert("RGB").save(dst, "JPEG", quality=quality)
    return os.path.getsize(dst)


def fmt(n):
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 / 1024:.2f}MB"


def fmt_save(old, new):
    if old == 0:
        return "0%"
    s = (1 - new / old) * 100
    return f"-{s:.1f}%" if s > 0 else f"+{-s:.1f}%"


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("JpgLossless · 图片压缩")
        self.root.geometry("1200x780")
        self.queue = queue.Queue()
        self.engine = None
        self.running = False
        self.files = []            # 元素为 (src, base)，base 用于保持目录结构
        self.output_map = {}       # src -> 输出路径
        self.thumb_photos = {}     # src -> PhotoImage（防止被回收）
        self.thumbs = {}           # src -> 缩略图控件
        self.thumb_col = 3
        self._preview_tmp = None
        self.setup_style()
        self.root.minsize(960, 640)
        self.build_ui()
        self.load_apply_config()
        self.root.after(100, self.poll)
        self.refresh_engine()
        self.setup_drag_drop()

    def setup_style(self):
        try:
            self.root.configure(bg=C_BG)
        except Exception:
            pass
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TPanedWindow", background=C_BG)
        style.configure("TFrame", background=C_BG)
        style.configure("TLabel", background=C_BG, foreground=C_TEXT, font=(FONT_UI, 10))
        style.configure("TLabelFrame", background=C_BG, foreground=C_TEXT,
                        font=(FONT_UI, 10, "bold"), bordercolor=C_BORDER)
        style.configure("TLabelFrame.Label", background=C_BG, foreground=C_TEXT,
                        font=(FONT_UI, 10, "bold"))
        style.configure("TButton", background=C_SURFACE, foreground=C_TEXT,
                        font=(FONT_UI, 10), borderwidth=1, relief="raised", padding=(9, 5))
        style.map("TButton",
                  background=[("active", "#E8EDF5"), ("pressed", "#DCE4F0")],
                  relief=[("pressed", "sunken")])
        style.configure("Accent.TButton", background=C_ACCENT, foreground="white",
                        font=(FONT_UI, 10, "bold"), borderwidth=0, padding=(14, 7))
        style.map("Accent.TButton",
                  background=[("active", C_ACCENT_HOVER), ("pressed", C_ACCENT_HOVER)])
        style.configure("TRadiobutton", background=C_BG, foreground=C_TEXT, font=(FONT_UI, 10))
        style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT, font=(FONT_UI, 10))
        style.configure("TEntry", fieldbackground=C_SURFACE, foreground=C_TEXT,
                        bordercolor=C_BORDER, font=(FONT_UI, 10))
        style.configure("TProgressbar", background=C_ACCENT, troughcolor="#E3E8F0", borderwidth=0)
        style.configure("Treeview", background=C_SURFACE, foreground=C_TEXT,
                        fieldbackground=C_SURFACE, font=(FONT_UI, 9.5),
                        rowheight=24, bordercolor=C_BORDER)
        style.configure("Treeview.Heading", background="#EAEFF6", foreground=C_TEXT,
                        font=(FONT_UI, 9.5, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#C9DCFB")],
                  foreground=[("selected", C_TEXT)])

    def build_ui(self):
        # 头部签名区（深色 + 细强调条）
        header = tk.Frame(self.root, bg=C_HEADER, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="JpgLossless", bg=C_HEADER, fg="white",
                 font=(FONT_UI, 16, "bold")).pack(side="left", padx=16)
        tk.Label(header, text="图片无损压缩 · WebP / PNG / JPG 转码",
                 bg=C_HEADER, fg=C_SUBTLE, font=(FONT_UI, 10)).pack(side="left")
        tk.Frame(self.root, bg=C_ACCENT, height=3).pack(fill="x")

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Button(top, text="选择文件", command=self.pick_files).pack(side="left")
        ttk.Button(top, text="选择文件夹", command=self.pick_dir).pack(side="left", padx=6)
        ttk.Button(top, text="清空列表", command=self.clear_list).pack(side="left", padx=6)
        ttk.Label(top, text="（也可把文件 / 文件夹直接拖入右侧拖入区）").pack(side="left", padx=10)
        self.engine_var = tk.StringVar(value="引擎：检测中…")
        ttk.Label(top, textvariable=self.engine_var).pack(side="right")

        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=4)
        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=0)
        self.build_left(left)
        self.build_right(right)

        for v in (self.save_mode_var, self.target_fmt_var, self.suffix_var,
                  self.keep_var, self.quality_var, self.auto_open_var,
                  self.auto_delete_var, self.skip_thumb_var, self.out_dir_var):
            try:
                v.trace_add("write", lambda *a: self.save_config())
            except Exception:
                pass

    def build_left(self, parent):
        opt = ttk.LabelFrame(parent, text="输出选项", padding=10)
        opt.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(opt)
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="保存位置：").pack(side="left")
        self.save_mode_var = tk.StringVar(value="原文件夹")
        ttk.Radiobutton(row1, text="原文件夹(覆盖)", variable=self.save_mode_var,
                        value="原文件夹").pack(side="left")
        ttk.Radiobutton(row1, text="自定义文件夹", variable=self.save_mode_var,
                        value="自定义文件夹").pack(side="left", padx=(6, 0))
        self.out_dir_var = tk.StringVar(value="")
        self.dir_entry = ttk.Entry(row1, textvariable=self.out_dir_var, width=30, state="disabled")
        self.dir_entry.pack(side="left", padx=6)
        self.browse_btn = ttk.Button(row1, text="浏览", command=self.pick_outdir, state="disabled")
        self.browse_btn.pack(side="left")
        self.save_mode_var.trace_add("write", lambda *a: self.on_save_mode())

        row2 = ttk.Frame(opt)
        row2.pack(fill="x", pady=3)
        ttk.Label(row2, text="目标格式：").pack(side="left")
        self.target_fmt_var = tk.StringVar(value="原格式")
        for v in ("原格式", "WebP", "PNG", "JPG"):
            ttk.Radiobutton(row2, text=v, variable=self.target_fmt_var, value=v).pack(side="left")

        row3 = ttk.Frame(opt)
        row3.pack(fill="x", pady=3)
        ttk.Label(row3, text="文件名后缀：").pack(side="left")
        self.suffix_var = tk.StringVar(value="")
        ttk.Entry(row3, textvariable=self.suffix_var, width=16).pack(side="left", padx=6)
        ttk.Label(row3, text="（如 _opt，留空=覆盖/原名）").pack(side="left")
        self.keep_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="保持原目录结构（自定义文件夹时）",
                        variable=self.keep_var).pack(side="left", padx=(14, 0))

        row4 = ttk.Frame(opt)
        row4.pack(fill="x", pady=3)
        ttk.Label(row4, text="压缩质量：").pack(side="left")
        self.quality_var = tk.IntVar(value=95)
        self.quality_entry_var = tk.StringVar(value="95")

        def on_q_scale(v):
            self.quality_entry_var.set(str(int(float(v))))

        def commit_q_entry(*a):
            try:
                val = max(1, min(100, int(float(self.quality_entry_var.get()))))
            except ValueError:
                val = self.quality_var.get()
            self.quality_var.set(val)
            self.quality_entry_var.set(str(val))

        self.quality_entry = ttk.Entry(row4, textvariable=self.quality_entry_var, width=4)
        self.quality_entry.pack(side="left", padx=(0, 6))
        self.quality_entry.bind("<Return>", commit_q_entry)
        self.quality_entry.bind("<FocusOut>", commit_q_entry)
        ttk.Scale(row4, from_=1, to=100, variable=self.quality_var, orient="horizontal",
                  length=220, command=on_q_scale).pack(side="left")
        ttk.Label(row4, text="（转 JPG / WebP / PNG 生效；100=无损）").pack(side="left", padx=6)
        self.quality_var.trace_add("write", lambda *a: self.quality_entry_var.set(str(self.quality_var.get())))

        row5 = ttk.Frame(opt)
        row5.pack(fill="x", pady=3)
        self.auto_open_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row5, text="处理完成后自动打开输出文件夹",
                        variable=self.auto_open_var).pack(side="left")
        ttk.Button(row5, text="预览选中对比", command=self.preview_selected).pack(side="left", padx=12)

        row6 = ttk.Frame(opt)
        row6.pack(fill="x", pady=3)
        self.auto_delete_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row6, text="完成后自动删除源文件(只留压缩后图片)",
                        variable=self.auto_delete_var,
                        command=self.on_auto_delete_toggle).pack(side="left")
        self.skip_thumb_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row6, text="大批量模式(不生成缩略图)",
                        variable=self.skip_thumb_var).pack(side="left", padx=(16, 0))

        cols = ("name", "old", "new", "save", "status")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", height=12)
        for c, t, w in (("name", "文件", 380), ("old", "原大小", 90),
                        ("new", "新大小", 90), ("save", "节省", 70), ("status", "状态", 120)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.tag_configure("ok", foreground=C_SUCCESS)
        self.tree.tag_configure("fail", foreground=C_ERROR)
        self.tree.tag_configure("muted", foreground=C_MUTED)
        self.tree.pack(fill="both", expand=True, pady=8)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        bottom = ttk.Frame(parent)
        bottom.pack(fill="x")
        self.start_btn = ttk.Button(bottom, text="开始压缩", command=self.start, style="Accent.TButton")
        self.start_btn.pack(side="left", anchor="n")
        self.log = scrolledtext.ScrolledText(bottom, height=6, state="disabled", width=42)
        self.log.pack(side="right", fill="both", expand=True, padx=(10, 0))

        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill="x", pady=(6, 0))

    def build_right(self, parent):
        th_frame = ttk.LabelFrame(parent, text="拖入区（缩略图 · 可批量拖入 / 点击选中）", padding=6)
        th_frame.pack(fill="both", expand=True, pady=(0, 8))
        self.thumb_canvas = tk.Canvas(th_frame, bg="#EEF2F8", highlightthickness=0)
        scroll = ttk.Scrollbar(th_frame, orient="vertical", command=self.thumb_canvas.yview)
        self.thumb_canvas.configure(yscrollcommand=scroll.set)
        self.thumb_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.thumb_inner = ttk.Frame(self.thumb_canvas)
        self.thumb_canvas.create_window((0, 0), window=self.thumb_inner, anchor="nw")
        self.thumb_inner.bind(
            "<Configure>",
            lambda e: self.thumb_canvas.configure(scrollregion=self.thumb_canvas.bbox("all")))
        self.thumb_hint = tk.Label(
            th_frame, text="把图片 / 文件夹拖到这里\n（或点上方「选择文件 / 文件夹」）",
            bg="#EEF2F8", fg=C_MUTED, font=(FONT_UI, 11))
        self.thumb_hint.place(relx=0.5, rely=0.5, anchor="center")

        cmp_frame = ttk.LabelFrame(parent, text="对比（原图 | 结果）", padding=6)
        cmp_frame.pack(fill="x", pady=(0, 8))
        c0 = ttk.Frame(cmp_frame)
        c0.pack(fill="x")
        self.orig_canvas = tk.Canvas(c0, width=176, height=176, bg="#EEF2F8", highlightthickness=0)
        self.res_canvas = tk.Canvas(c0, width=176, height=176, bg="#EEF2F8", highlightthickness=0)
        self.orig_canvas.pack(side="left", padx=4)
        self.res_canvas.pack(side="left", padx=4)
        self.orig_info = ttk.Label(cmp_frame, text="原图：—", font=(FONT_UI, 9))
        self.orig_info.pack(anchor="w", pady=(4, 0))
        self.res_info = ttk.Label(cmp_frame, text="结果：—", font=(FONT_UI, 9))
        self.res_info.pack(anchor="w")

    def setup_drag_drop(self):
        """Windows 原生文件拖放，无需额外依赖。

        注意：64 位下 wParam/lParam 是指针（64 位），必须为 CallWindowProcW /
        SetWindowLongPtrW 等显式声明 ctypes 参数类型，否则默认按 32 位 c_int
        处理会溢出并触发 access violation，导致窗口过程崩溃、界面打不开。
        """
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            WM_DROPFILES = 0x0233
            GWL_WNDPROC = -4

            HWND = wintypes.HWND
            UINT = wintypes.UINT
            WPARAM = ctypes.c_uint64
            LPARAM = ctypes.c_int64
            LRESULT = ctypes.c_int64
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, HWND, UINT, WPARAM, LPARAM]
            user32.CallWindowProcW.restype = LRESULT
            user32.SetWindowLongPtrW.argtypes = [HWND, ctypes.c_int, WNDPROC]
            user32.SetWindowLongPtrW.restype = ctypes.c_int64
            user32.GetWindowLongPtrW.argtypes = [HWND, ctypes.c_int]
            user32.GetWindowLongPtrW.restype = ctypes.c_int64
            shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [wintypes.HANDLE]
            shell32.DragFinish.restype = None

            hwnd = self.root.winfo_id()
            shell32.DragAcceptFiles(hwnd, True)
            old_wndproc = user32.GetWindowLongPtrW(hwnd, GWL_WNDPROC)

            def wndproc(hWnd, msg, wParam, lParam):
                if msg == WM_DROPFILES:
                    hDrop = wintypes.HANDLE(wParam)
                    count = shell32.DragQueryFileW(hDrop, 0xFFFFFFFF, None, 0)
                    buf = ctypes.create_unicode_buffer(1024)
                    paths = []
                    for i in range(count):
                        shell32.DragQueryFileW(hDrop, i, buf, 1024)
                        paths.append(buf.value)
                    shell32.DragFinish(hDrop)
                    self.root.after(0, lambda p=list(paths): self.add_files(p))
                    return 0
                return user32.CallWindowProcW(old_wndproc, hWnd, msg, wParam, lParam)

            self._wndproc = WNDPROC(wndproc)
            user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, self._wndproc)
        except Exception as e:
            self.log_msg("拖放初始化失败（不影响其他功能）：" + str(e))

    def on_auto_delete_toggle(self):
        if self.auto_delete_var.get():
            messagebox.showwarning(
                "将删除源文件",
                "你已开启「完成后自动删除源文件」。\n\n"
                "压缩成功后，程序会删除压缩前的原图，仅保留压缩后的图片。\n"
                "此操作不可恢复，如有需要请先备份原图！\n\n"
                "（注：“原文件夹(覆盖)”且“原格式”时，源文件已被压缩结果直接覆盖，\n"
                " 本就只留下压缩后图片，因此不会额外删除。）")

    def on_save_mode(self):
        st = "normal" if self.save_mode_var.get() == "自定义文件夹" else "disabled"
        self.dir_entry.configure(state=st)
        self.browse_btn.configure(state=st)

    # ---------------- 配置持久化 ----------------
    def load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self):
        cfg = {
            "save_mode": self.save_mode_var.get(),
            "out_dir": self.out_dir_var.get(),
            "target_fmt": self.target_fmt_var.get(),
            "suffix": self.suffix_var.get(),
            "keep": self.keep_var.get(),
            "quality": self.quality_var.get(),
            "auto_open": self.auto_open_var.get(),
            "auto_delete": self.auto_delete_var.get(),
            "skip_thumb": self.skip_thumb_var.get(),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_apply_config(self):
        cfg = self.load_config()
        if not cfg:
            self.on_save_mode()
            return
        self.save_mode_var.set(cfg.get("save_mode", "原文件夹"))
        self.out_dir_var.set(cfg.get("out_dir", ""))
        self.target_fmt_var.set(cfg.get("target_fmt", "原格式"))
        self.suffix_var.set(cfg.get("suffix", ""))
        self.keep_var.set(bool(cfg.get("keep", False)))
        self.quality_var.set(int(cfg.get("quality", 95)))
        self.auto_open_var.set(bool(cfg.get("auto_open", False)))
        self.auto_delete_var.set(bool(cfg.get("auto_delete", False)))
        self.skip_thumb_var.set(bool(cfg.get("skip_thumb", False)))
        self.quality_entry_var.set(str(self.quality_var.get()))
        self.on_save_mode()

    # ---------------- 日志 / 引擎 ----------------
    def log_msg(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_engine(self):
        self.engine = find_engine()
        if self.engine:
            self.engine_var.set("引擎：" + os.path.basename(self.engine))
        else:
            self.engine_var.set("引擎：未安装（将尝试自动下载）")

    # ---------------- 选择 / 添加 ----------------
    def pick_files(self):
        paths = filedialog.askopenfilenames(
            filetypes=[("图片", "*.jpg *.jpeg *.png"), ("All", "*.*")])
        self.add_files(list(paths))

    def pick_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.add_files([d])

    def pick_outdir(self):
        d = filedialog.askdirectory()
        if d:
            self.out_dir_var.set(d)

    def add_files(self, paths):
        for p in paths:
            if os.path.isfile(p):
                self._add_one(p, os.path.dirname(p))
            elif os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        if f.lower().endswith(IMG_EXTS):
                            self._add_one(os.path.join(root, f), p)

    def _add_one(self, src, base):
        if any(src == s for s, _ in self.files):
            return
        self.files.append((src, base))
        self.tree.insert("", "end", values=(src, "-", "-", "-", "待处理"))
        self.add_thumb(src)
        try:
            self.thumb_hint.place_forget()
        except Exception:
            pass

    def add_thumb(self, src):
        if self.skip_thumb_var.get():
            return
        if len(self.thumb_photos) >= 2000:  # 安全上限：海量文件时不生成缩略图，避免撑爆内存
            return
        try:
            from PIL import Image, ImageTk
            im = Image.open(src)
            im.thumbnail((80, 80))
            photo = ImageTk.PhotoImage(im)
            self.thumb_photos[src] = photo
            idx = len(self.thumbs)
            r = idx // self.thumb_col
            c = idx % self.thumb_col
            cell = ttk.Frame(self.thumb_inner)
            lbl = ttk.Label(cell, image=photo)
            lbl.pack()
            nm = os.path.basename(src)
            if len(nm) > 14:
                nm = nm[:12] + "…"
            ttk.Label(cell, text=nm, font=(FONT_UI, 8)).pack()
            cell.grid(row=r, column=c, padx=4, pady=4, sticky="n")
            cell.bind("<Button-1>", lambda e, s=src: self.select_src(s))
            lbl.bind("<Button-1>", lambda e, s=src: self.select_src(s))
            self.thumbs[src] = cell
        except Exception:
            pass

    def select_src(self, src):
        for iid in self.tree.get_children():
            if self.tree.item(iid, "values")[0] == src:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self.on_tree_select(None)
                break

    def clear_list(self):
        self.files = []
        self.output_map = {}
        for i in self.tree.get_children():
            self.tree.delete(i)
        for w in self.thumb_inner.winfo_children():
            w.destroy()
        self.thumbs = {}
        self.thumb_photos = {}
        self.orig_canvas.delete("all")
        self.orig_info.configure(text="原图：—")
        self.clear_res()
        self.thumb_hint.place(relx=0.5, rely=0.5, anchor="center")

    # ---------------- 对比 / 预览 ----------------
    def display_image(self, canvas, path):
        from PIL import Image, ImageTk
        im = Image.open(path)
        w = canvas.winfo_width() or 176
        h = canvas.winfo_height() or 176
        im.thumbnail((w - 6, h - 6))
        photo = ImageTk.PhotoImage(im)
        canvas.delete("all")
        canvas.create_image(w // 2, h // 2, image=photo, anchor="center")
        canvas.image = photo  # 保活

    def show_orig(self, src):
        self.display_image(self.orig_canvas, src)
        self.orig_info.configure(text="原图：" + os.path.basename(src) + "  " + fmt(os.path.getsize(src)))

    def clear_res(self):
        self.res_canvas.delete("all")
        self.res_info.configure(text="结果：—")
        self._preview_tmp = None

    def show_res(self, out, old, new):
        self.display_image(self.res_canvas, out)
        self.res_info.configure(
            text=f"结果：{os.path.basename(out)}  {fmt(old)} → {fmt(new)}  ({fmt_save(old, new)})")

    def on_tree_select(self, ev):
        sel = self.tree.selection()
        if not sel:
            return
        src = self.tree.item(sel[0], "values")[0]
        self.show_orig(src)
        out = self.output_map.get(src)
        if out and os.path.exists(out):
            self.show_res(out, os.path.getsize(src), os.path.getsize(out))
        else:
            self.clear_res()

    def _out_ext(self, src):
        fmt_mode = self.target_fmt_var.get()
        if fmt_mode == "原格式":
            return os.path.splitext(src)[1].lower()
        return FMT_EXT[fmt_mode]

    def preview_selected(self):
        """输出前预览：用当前设置生成压缩结果到临时文件并在对比框展示。"""
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表/缩略图中选择一个文件")
            return
        src = self.tree.item(sel[0], "values")[0]
        self.show_orig(src)
        fmt_mode = self.target_fmt_var.get()
        if fmt_mode == "原格式" and not self.engine:
            messagebox.showerror("提示", "原格式无损需要引擎，请先点“开始压缩”以自动下载，或选择转格式模式")
            return
        fd, tmp = tempfile.mkstemp(suffix=self._out_ext(src))
        os.close(fd)
        try:
            new = compress_one(src, tmp, fmt_mode, self.engine, int(self.quality_var.get()))
            old = os.path.getsize(src)
            self.display_image(self.res_canvas, tmp)
            self.res_info.configure(
                text=f"结果(预览)：{fmt(old)} → {fmt(new)}  ({fmt_save(old, new)})  临时文件")
            self._preview_tmp = tmp
        except Exception as e:
            try:
                os.remove(tmp)
            except Exception:
                pass
            messagebox.showerror("预览失败", str(e))

    # ---------------- 处理 ----------------
    def start(self):
        if self.running:
            return
        if not self.files:
            messagebox.showinfo("提示", "请先选择/拖入文件或文件夹")
            return
        fmt_mode = self.target_fmt_var.get()
        if self.save_mode_var.get() == "自定义文件夹" and not self.out_dir_var.get().strip():
            messagebox.showerror("提示", "请先选择自定义输出文件夹")
            return
        self.save_config()
        if fmt_mode == "原格式":
            if not self.engine:
                self.log_msg("未检测到引擎，尝试自动下载 ect…")
                threading.Thread(target=self.install_and_run, daemon=True).start()
            else:
                threading.Thread(target=self.process, daemon=True).start()
        else:
            try:
                import PIL  # noqa
            except ImportError:
                messagebox.showerror("缺少依赖", "转格式模式需要 Pillow：\npip install pillow")
                return
            threading.Thread(target=self.process, daemon=True).start()

    def install_and_run(self):
        try:
            self.queue.put(("status", "下载中…", ""))
            eng = download_ect()
            self.engine = eng
            self.queue.put(("engine", os.path.basename(eng), ""))
            self.queue.put(("log", "ect 下载完成：" + eng, ""))
            self.process()
        except Exception as e:
            self.queue.put(("log", "自动下载失败：" + str(e), ""))
            self.queue.put(("log", "请手动下载 ect.exe 放入 bin/ 目录：", ""))
            self.queue.put(("log", "https://github.com/fhanau/Efficient-Compression-Tool/releases", ""))

    def process(self):
        self.running = True
        self.queue.put(("running", True, ""))
        fmt_mode = self.target_fmt_var.get()
        save_mode = self.save_mode_var.get()
        out_dir = self.out_dir_var.get().strip()
        suffix = self.suffix_var.get().strip()
        keep = self.keep_var.get()
        quality = int(self.quality_var.get())
        eng = self.engine
        self.output_map = {}

        if fmt_mode == "JPG":
            self.queue.put(("log", f"注意：转 JPG 为有损压缩（quality={quality}）", ""))

        total = len(self.files)
        self.queue.put(("progress_max", total, ""))
        done = 0
        first_out_dir = None
        for src, base in self.files:
            try:
                old = os.path.getsize(src)
                fname = os.path.splitext(os.path.basename(src))[0]
                name = fname + suffix
                ext = self._out_ext(src)

                if save_mode == "自定义文件夹" and keep:
                    rel = os.path.relpath(src, base)
                    rel_noext = os.path.splitext(rel)[0]
                    out_path = os.path.join(out_dir, rel_noext + ext)
                else:
                    odir = out_dir if save_mode == "自定义文件夹" else os.path.dirname(src)
                    out_path = os.path.join(odir, name + ext)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                if fmt_mode == "原格式":
                    if "ect" not in os.path.basename(eng).lower() and ext == ".png":
                        status = "跳过:需ect引擎"
                        new = old
                    elif out_path == src:
                        fd, tmp = tempfile.mkstemp(suffix=ext)
                        os.close(fd)
                        new = compress_one(src, tmp, fmt_mode, eng, quality)
                        if new < old:
                            shutil.move(tmp, src)
                            self.output_map[src] = src
                            status = "已压缩"
                        else:
                            os.remove(tmp)
                            status = "已最优"
                    else:
                        compress_one(src, out_path, fmt_mode, eng, quality)
                        new = os.path.getsize(out_path)
                        self.output_map[src] = out_path
                        status = "已生成(原图保留)"
                else:
                    compress_one(src, out_path, fmt_mode, eng, quality)
                    new = os.path.getsize(out_path)
                    self.output_map[src] = out_path
                    status = {"WebP": "WebP无损", "PNG": "PNG无损", "JPG": "JPG有损"}[fmt_mode]
                self.queue.put(("row", src, (fmt(old), fmt(new), fmt_save(old, new), status)))
                # 自动删除源文件：仅当成功生成了「与源不同路径」的压缩结果时才删除源
                ok = not status.startswith("失败")
                if (self.auto_delete_var.get() and ok and out_path != src
                        and os.path.isfile(out_path) and os.path.isfile(src)):
                    try:
                        os.remove(src)
                        self.queue.put(("log", "已删除源文件：" + os.path.basename(src), ""))
                    except Exception as e:
                        self.queue.put(("log", "删除源文件失败 " + os.path.basename(src) + "：" + str(e)[:40], ""))
            except Exception as e:
                self.queue.put(("row", src, ("-", "-", "-", "失败:" + str(e)[:40])))
            if first_out_dir is None:
                first_out_dir = os.path.dirname(out_path) if 'out_path' in dir() else os.path.dirname(src)
            done += 1
            self.queue.put(("progress", done, ""))

        if self.auto_open_var.get():
            folder = out_dir if save_mode == "自定义文件夹" else first_out_dir
            if folder and os.path.isdir(folder):
                try:
                    os.startfile(folder)
                except Exception as e:
                    self.queue.put(("log", "自动打开文件夹失败：" + str(e), ""))

        self.queue.put(("running", False, ""))
        self.queue.put(("log", f"完成：共处理 {total} 个文件"))

    def poll(self):
        try:
            while True:
                kind, a, b = self.queue.get_nowait()
                if kind == "log":
                    self.log_msg(a)
                elif kind == "engine":
                    self.engine_var.set("引擎：" + a)
                elif kind == "status":
                    self.engine_var.set("引擎：" + a)
                elif kind == "running":
                    self.running = a
                    self.start_btn.configure(state="disabled" if a else "normal")
                elif kind == "progress_max":
                    self.progress.configure(maximum=a)
                    self.progress["value"] = 0
                elif kind == "progress":
                    self.progress["value"] = a
                elif kind == "row":
                    for iid in self.tree.get_children():
                        vals = self.tree.item(iid, "values")
                        if vals and vals[0] == a:
                            self.tree.item(iid, values=(a, b[0], b[1], b[2], b[3]))
                            tag = ""
                            st = b[3]
                            if "失败" in st:
                                tag = "fail"
                            elif any(k in st for k in ("已压缩", "已生成", "无损")):
                                tag = "ok"
                            elif any(k in st for k in ("已最优", "跳过", "更大")):
                                tag = "muted"
                            if tag:
                                self.tree.item(iid, tags=(tag,))
                            break
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    # 高 DPI 屏幕下，Tk 默认是“DPI 不感知”，Windows 会把整个窗口当位图拉伸，
    # 导致文字/控件/图片全部发虚（看起来像蒙了毛玻璃）。声明进程感知系统 DPI，
    # 让 Tk 按真实像素渲染，界面即清晰。必须在创建 Tk 实例之前调用。
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 1 = PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
