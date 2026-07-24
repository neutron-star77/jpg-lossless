#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JpgLossless · Web 版（pywebview 壳 + 系统 WebView2 内核）
=========================================================
纯本地运行：压缩逻辑（ect 引擎 / Pillow）在本机执行，图片不上传任何服务器。
前端 web/index.html 用系统 Edge WebView2 渲染，界面走「工程蓝图」风，
签名元素是每个文件行里的像素对比条（原大小灰条 vs 新大小蓝/绿条）。

打包：
  pyinstaller --onefile --noconsole --name JpgLossless ^
    --add-binary "bin/ect.exe;bin" --add-data "web/index.html;web" jpg_lossless_web.py
"""
import os
import sys
import json
import base64
import io
import shutil
import queue
import threading
import tempfile
import subprocess
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import webview
from PIL import Image, ImageOps

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(APP_DIR, "bin")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

ENGINE_ORDER = {"ect": 0, "jpegtran": 1, "jpegoptim": 2}
IMG_EXTS = (".jpg", ".jpeg", ".png", ".jpe", ".jfif")
FMT_EXT = {"WebP": ".webp", "PNG": ".png", "JPG": ".jpg"}


def human(n):
    if n < 1024:
        return "%d B" % n
    if n < 1048576:
        return "%.1f KB" % (n / 1024)
    return "%.2f MB" % (n / 1048576)


def engine_key(path):
    return ENGINE_ORDER.get(os.path.basename(path).lower().replace(".exe", ""), 9)


def _as_text(v, default=""):
    """把可能来自前端的 list / None / 数字 规整成普通字符串，避免 .strip() 报错。"""
    if v is None:
        return default
    if isinstance(v, list):
        return _as_text(v[0], default) if v else default
    return str(v).strip()


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


def run_engine(engine, src, dst, level=-5):
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
    else:  # ect
        shutil.copy2(src, dst)
        if ext == ".png":
            subprocess.run([engine, str(level), dst], check=True)
        else:
            subprocess.run([engine, str(level), "-strip", dst], check=True)


def to_webp(src, dst, quality=95):
    q = int(quality)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        # method=0 编码最快；但大尺寸/高细节图会触发 libwebp 分区溢出
        # (VP8_ENC_ERROR_PARTITION0_OVERFLOW, 即 ValueError: encoding error 6)，
        # 此时回退到更稳妥的 method（4/6）即可正常编码。
        if q >= 100:
            methods = (0, 6)
        else:
            methods = (0, 4, 6)
        last = None
        for m in methods:
            try:
                if q >= 100:
                    im.save(dst, "WEBP", lossless=True, method=m)
                else:
                    im.save(dst, "WEBP", lossless=False, quality=q, method=m)
                return
            except Exception as e:
                last = e
                # 仅当是 WebP 编码分区溢出等错误才回退；其它错误（如损坏文件）直接抛出
                if "encoding error" in str(e).lower() or "encoder error" in str(e).lower():
                    continue
                raise
        raise RuntimeError("WebP 编码失败（图片过大或细节过多）：%s" % last)


def compress_one(src, dst, fmt, eng, quality, level=-5):
    ext = os.path.splitext(src)[1].lower() if fmt == "原格式" else FMT_EXT[fmt]
    if fmt == "原格式":
        if "ect" not in os.path.basename(eng).lower() and ext == ".png":
            raise RuntimeError("该引擎不支持 PNG，需 ect 引擎")
        run_engine(eng, src, dst, level)
    elif fmt == "WebP":
        to_webp(src, dst, quality)
    elif fmt == "PNG":
        with Image.open(src) as im:
            ImageOps.exif_transpose(im).save(dst, "PNG")
    elif fmt == "JPG":
        with Image.open(src) as im:
            ImageOps.exif_transpose(im).convert("RGB").save(dst, "JPEG", quality=quality)
    return os.path.getsize(dst)


class Api:
    def __init__(self):
        self.engine_exe = find_engine()
        self.files = []  # 元素 (src, base)，base 用于保持目录结构
        self.drop_dir = os.path.join(tempfile.gettempdir(), "jpgll_drop")
        os.makedirs(self.drop_dir, exist_ok=True)
        self._queue = queue.Queue()  # 线程安全：压缩线程只往这里塞事件
        self._busy = False  # 是否有压缩任务在运行（用于前端看门狗恢复卡死的按钮）
        self._load_config()

    # ---------- 基础工具 ----------
    def _emit(self, ev):
        # 不在此处直接 evaluate_js（后台线程调用 Invoke 易在 Windows 下死锁），
        # 只把事件压入队列，由前端轮询 poll_events 拉取。
        try:
            self._queue.put(ev)
        except Exception:
            pass

    def _thumb_b64(self, path, size=120, quality=85):
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((size, size))
                buf = io.BytesIO()
                if im.mode in ("RGBA", "P", "LA"):
                    im.convert("RGB").save(buf, "JPEG", quality=quality)
                else:
                    im.save(buf, "JPEG", quality=quality)
                return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return ""

    def _ingest(self, paths, folders=False):
        if not paths:
            return []
        if not isinstance(paths, list):
            paths = [paths]
        added = []
        for p in paths:
            entries = []
            if folders or os.path.isdir(p):
                for root, _, fs in os.walk(p):
                    for f in fs:
                        if f.lower().endswith(IMG_EXTS):
                            entries.append((os.path.join(root, f), p))
            else:
                entries.append((p, os.path.dirname(p)))
            for src, base in entries:
                if any(src == s for s, _ in self.files):
                    continue
                self.files.append((src, base))
                added.append({"src": src, "name": os.path.basename(src),
                              "thumb": self._thumb_b64(src)})
        return added

    # ---------- 配置 ----------
    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                self.config = json.load(f)
        except Exception:
            self.config = {}

    def get_config(self):
        return self.config

    def set_config(self, cfg):
        try:
            self.config = dict(cfg)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- 前端轮询取事件 ----------
    def poll_events(self):
        out = []
        try:
            while True:
                out.append(self._queue.get_nowait())
        except Exception:
            pass
        return out

    # ---------- JS 调用接口 ----------
    def engine(self):
        return os.path.basename(self.engine_exe) if self.engine_exe else "未安装（将自动下载）"

    # 反馈接收邮箱：部署/分享时改成你自己的邮箱即可
    FEEDBACK_EMAIL = "your-email@example.com"

    def send_feedback(self, subject, body):
        # 调起系统默认邮件客户端，发送到指定邮箱
        addr = self.FEEDBACK_EMAIL
        try:
            subject = urllib.parse.quote((subject or "")[:120])
            body = urllib.parse.quote((body or "")[:1500])
            link = "mailto:%s?subject=%s&body=%s" % (addr, subject, body)
            os.startfile(link)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def choose_files(self):
        # 注意：新版本 pywebview 的 file_types 必须是字符串格式 "描述(*.ext)"，
        # 旧版的 ("描述", "*.ext") 元组格式会让 parse_file_type 抛 TypeError，
        # 导致对话框打不开（表现为“点击选择文件没反应”）。
        r = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("图片(*.jpg;*.jpeg;*.png)",))
        return self._ingest(r)

    def choose_dirs(self):
        r = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=True)
        return self._ingest(r, folders=True)

    def choose_outdir(self):
        r = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        # pywebview 不同版本对 FOLDER_DIALOG 的返回值可能是 字符串 / 单层列表 / 嵌套列表，
        # 这里统一规整成“单个字符串路径”，避免前端把 list 当 out_dir 传入后 .strip() 报错。
        if isinstance(r, list):
            r = r[0] if r else None
        if isinstance(r, list):  # 嵌套列表兜底
            r = r[0] if r else None
        return r

    def add_drop(self, items):
        out = []
        for it in (items or []):
            name = it.get("name", "image.dat")
            data = it.get("data", "")
            if not data:
                continue
            dst = os.path.join(self.drop_dir, name)
            base, ext = os.path.splitext(name)
            i = 1
            while os.path.exists(dst):
                dst = os.path.join(self.drop_dir, f"{base}_{i}{ext}")
                i += 1
            try:
                with open(dst, "wb") as f:
                    f.write(base64.b64decode(data))
            except Exception:
                continue
            if any(dst == s for s, _ in self.files):
                continue
            self.files.append((dst, self.drop_dir))
            out.append({"src": dst, "name": os.path.basename(dst),
                        "thumb": self._thumb_b64(dst)})
        return out

    def remove_file(self, src):
        self.files = [(s, b) for s, b in self.files if s != src]
        return True

    def is_busy(self):
        return bool(self._busy)

    def start(self, settings):
        threading.Thread(target=self._process, args=(settings or {},), daemon=True).start()

    # ---------- 处理（线程） ----------
    def _process(self, settings):
        if self._busy:
            # 已有任务在跑，忽略重复启动，避免多线程同时写文件互相干扰
            self._emit({"t": "log", "m": "已有任务在运行，本次启动被忽略"})
            return
        self._busy = True
        self._emit({"t": "start"})  # 通知前端进入“处理中”状态（不确定进度）
        msg = "完成"
        try:
            msg = self._run(settings) or "完成"
        except Exception as e:
            self._emit({"t": "log", "m": "处理异常：%s" % str(e)})
            msg = "失败：" + str(e)[:60]
        finally:
            # 关键修复：无论成功 / 异常 / 提前终止，都复位 _busy 并发出 finish，
            # 否则前端“开始压缩”按钮会永久卡在 disabled（表现为点不动）。
            self._busy = False
            self._emit({"t": "finish", "m": msg})

    # ---------- 拖放（pywebview DOM drop，可拿到真实路径，含文件夹） ----------
    def on_drag_drop(self, event):
        # 由 window.pywebview.dom.body.on('drop') 触发：
        # pywebview 会把每个 file 的真实路径放进 event.dataTransfer.files[i].pywebviewFullPath
        # （文件夹也会作为一项出现，pywebviewFullPath 指向该文件夹）
        try:
            files = ((event or {}).get("dataTransfer", {}) or {}).get("files", []) or []
        except Exception:
            files = []
        paths = []
        for f in files:
            p = (f.get("pywebviewFullPath") or f.get("path") or "").strip()
            if not p:
                continue
            if os.path.isdir(p) or p.lower().endswith(IMG_EXTS):
                paths.append(p)
        if not paths:
            self._emit({"t": "log", "m": "拖入的内容不是图片/文件夹，已忽略"})
            return
        added = self._ingest(paths)
        if added:
            self._emit({"t": "files", "rows": added})
            self._emit({"t": "log", "m": "拖入已添加 %d 个文件" % len(added)})
        else:
            self._emit({"t": "log", "m": "拖入的内容里没有可添加的图片"})

    def enable_drop(self):
        # 通过 pywebview 的 DOM 事件注册 drop：只有这样才能拿到拖入项的真实路径（含文件夹）。
        # 放在前端 pywebviewready 之后调用（见 index.html 的 init()），避免在页面加载早期
        # evaluate_js 过早执行而抛异常、进而把整个窗口的 API 桥接弄坏（会导致所有按钮失灵）。
        try:
            webview.windows[0].dom.body.on("drop", self.on_drag_drop)
        except Exception as e:
            self._emit({"t": "log", "m": "拖放监听注册失败：%s" % e})

    def _run(self, settings):
        fmt_mode = _as_text(settings.get("target_fmt", "原格式"), "原格式")
        save_mode = _as_text(settings.get("save_mode", "原文件夹"), "原文件夹")
        out_dir = _as_text(settings.get("out_dir"))
        suffix = _as_text(settings.get("suffix"))
        keep = bool(settings.get("keep", False))
        quality = int(settings.get("quality", 95))
        try:
            max_kb = int(settings.get("max_kb") or 0)
        except Exception:
            max_kb = 0
        cap = max_kb * 1024 if max_kb > 0 else 0
        try:
            level = int(settings.get("level", -2))
        except Exception:
            level = -2
        try:
            threads = int(settings.get("threads") or 0)
        except Exception:
            threads = 0
        threads = threads or min(os.cpu_count() or 4, 8)
        threads = max(1, min(threads, 16))

        files_snapshot = list(self.files)
        total = len(files_snapshot)

        rename = bool(settings.get("rename", False))
        # 预计算每个文件的输出路径，统一处理“按数字重命名”，保证两种格式分支编号一致
        pad = len(str(total)) if total else 1
        out_paths = {}
        for idx, (src, base) in enumerate(files_snapshot, 1):
            ext = (os.path.splitext(src)[1].lower() if fmt_mode == "原格式"
                   else FMT_EXT[fmt_mode])
            if rename:
                name = "%0*d" % (pad, idx)
            else:
                fname = os.path.splitext(os.path.basename(src))[0]
                name = fname + suffix
            if save_mode == "自定义文件夹" and keep and not rename:
                rel = os.path.relpath(src, base)
                rel_noext = os.path.splitext(rel)[0]
                out_path = os.path.join(out_dir, rel_noext + ext)
            else:
                odir = out_dir if save_mode == "自定义文件夹" else os.path.dirname(src)
                out_path = os.path.join(odir, name + ext)
            out_paths[src] = out_path
        if rename:
            self._emit({"t": "log", "m": "输出将按数字顺序重命名（%0*d 补零）" % (pad, total)})

        self._emit({"t": "log", "m": "开始处理，共 %d 个文件（等级 %d，并行 %d 线程）" % (total, level, threads)})
        if fmt_mode == "JPG":
            self._emit({"t": "log", "m": "注意：转 JPG 为有损压缩（quality=%d）" % quality})
        if cap:
            self._emit({"t": "log", "m": "输出上限：%d KB（超限时%s）" % (
                max_kb, "自动降质以达标(JPG)" if fmt_mode == "JPG" else "无损无法缩小则保留")})

        if fmt_mode == "原格式" and not self.engine_exe:
            self._emit({"t": "log", "m": "未检测到引擎，尝试自动下载 ect…"})
            try:
                self.engine_exe = download_ect()
                self._emit({"t": "log", "m": "ect 下载完成"})
            except Exception as e:
                self._emit({"t": "log", "m": "自动下载失败：%s" % str(e)})
                self._emit({"t": "log", "m": "请手动把 ect.exe 放入 bin/ 目录"})
                raise RuntimeError("缺少引擎，已终止")

        self._emit({"t": "prog", "done": 0, "total": total})
        eng = self.engine_exe
        state = {"done": 0, "first_out_dir": None}
        done_lock = threading.Lock()
        is_ect = "ect" in os.path.basename(eng).lower() if eng else False

        if fmt_mode == "原格式":
            # 计算输出路径并记录原始大小（必须在批量压缩前记录，因为 ECT 原地修改文件）
            jobs = []  # (src, out_path, old_size)
            for src, base in files_snapshot:
                out_path = out_paths[src]
                try:
                    old = os.path.getsize(src)
                except Exception:
                    old = 0
                jobs.append((src, out_path, old))

            inplace = [j for j in jobs if j[1] == j[0]]
            copyjobs = [j for j in jobs if j[1] != j[0]]

            # 保留原图模式：先把源文件并行复制到目标位置，再原地压缩
            if copyjobs:
                def docopy(j):
                    os.makedirs(os.path.dirname(j[1]), exist_ok=True)
                    shutil.copy2(j[0], j[1])
                with ThreadPoolExecutor(max_workers=threads) as ex:
                    list(ex.map(docopy, copyjobs))

            # 关键优化：ECT 默认原地压缩、仅变小才写回（已验证安全，绝不放大）。
            # 一次性传入所有文件 + --mt-file=N，让 ECT 内部多线程吃满多核，
            # 避免“逐文件 spawn 子进程 + 整份复制”带来的 I/O 等待（这正是 CPU 低、很慢的根因）。
            targets = [j[0] for j in inplace] + [j[1] for j in copyjobs]
            if targets and is_ect:
                self._emit({"t": "log", "m": "批量调用 ect（--mt-file=%d），请稍候…" % threads})
                for i in range(0, len(targets), 200):
                    chunk = targets[i:i + 200]
                    cmd = [eng, str(level), "-strip", "--strict", "--mt-file=%d" % threads] + chunk
                    try:
                        subprocess.run(cmd, check=False)
                    except Exception as e:
                        self._emit({"t": "log", "m": "  ⚠ 批量压缩异常：%s" % str(e)})
            elif targets and not is_ect:
                # jpegtran / jpegoptim 不支持 PNG，仅逐文件原地处理 jpg
                for t in targets:
                    if os.path.splitext(t)[1].lower() in (".jpg", ".jpeg"):
                        try:
                            if "jpegtran" in os.path.basename(eng).lower():
                                subprocess.run([eng, "-optimize", "-progressive", "-copy", "none",
                                                "-outfile", t, t], check=False)
                            else:
                                subprocess.run([eng, "--strip-all", "--all-progressive", t], check=False)
                        except Exception as e:
                            self._emit({"t": "log", "m": "  ⚠ %s 压缩失败：%s" % (t, str(e))})

            # 汇总结果行
            for src, out_path, old in jobs:
                res = out_path if os.path.exists(out_path) else src
                try:
                    new = os.path.getsize(res)
                except Exception:
                    new = old
                status = "已生成(原图保留)" if out_path != src else ("已压缩" if new < old else "已最优")
                if cap and new > cap:
                    self._emit({"t": "log", "m": "  ⚠ %s 无损后 %s 仍超 %dKB，已保留" % (
                        os.path.basename(src), human(new), max_kb)})
                self._emit({"t": "row", "src": src, "old": old, "new": new, "status": status})
                with done_lock:
                    state["done"] += 1
                    d = state["done"]
                    if state["first_out_dir"] is None:
                        state["first_out_dir"] = os.path.dirname(out_path)
                self._emit({"t": "prog", "done": d, "total": total})
        else:
            # 非原格式（转 WebP / PNG / JPG）：Pillow 逐文件，多线程并行
            def work(item):
                src, base = item
                try:
                    old = os.path.getsize(src)
                    self._emit({"t": "log", "m": "正在处理：%s（%s）" % (os.path.basename(src), human(old))})
                    out_path = out_paths[src]
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    compress_one(src, out_path, fmt_mode, eng, quality, level)
                    new = os.path.getsize(out_path)
                    if fmt_mode == "WebP":
                        status = "WebP无损" if quality >= 100 else "WebP有损"
                    else:
                        status = {"PNG": "PNG无损", "JPG": "JPG有损"}[fmt_mode]
                    if cap and new > cap and fmt_mode == "JPG":
                        q = quality
                        while q > 10 and new > cap:
                            q -= 8
                            new = compress_one(src, out_path, "JPG", eng, q, level)
                        status = "JPG有损≤%dKB" % max_kb
                    self._emit({"t": "row", "src": src, "old": old, "new": new, "status": status})
                except Exception as e:
                    self._emit({"t": "row", "src": src, "old": 0, "new": 0,
                                "status": "失败:" + str(e)[:40]})
                finally:
                    with done_lock:
                        state["done"] += 1
                        d = state["done"]
                        if state["first_out_dir"] is None:
                            state["first_out_dir"] = (os.path.dirname(out_path)
                                                      if "out_path" in dir() else os.path.dirname(src))
                    self._emit({"t": "prog", "done": d, "total": total})

            try:
                with ThreadPoolExecutor(max_workers=threads) as ex:
                    list(ex.map(work, files_snapshot))
            except Exception as e:
                self._emit({"t": "log", "m": "并行处理异常：%s" % str(e)})

        if settings.get("auto_open"):
            folder = out_dir if save_mode == "自定义文件夹" else state["first_out_dir"]
            if folder and os.path.isdir(folder):
                try:
                    os.startfile(folder)
                except Exception as e:
                    self._emit({"t": "log", "m": "自动打开文件夹失败：%s" % str(e)})

        done_msg = "完成：共处理 %d 个文件" % total
        return done_msg


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def main():
    # 用 url= 指向本地 file:// 路径（而非 html= 直接传字符串）：
    # html= 模式下 WebView2 页面 origin 为 null，会导致 pywebview 后续注入 api 方法的
    # evaluate_js 受限失败（表现为 window.pywebview.api 是空壳、engine 不是函数）。
    # file:// 有正常 origin，api 方法注入可靠。前端再用轮询等待 api 就绪。
    html_path = resource_path(os.path.join("web", "index.html"))
    api = Api()
    webview.create_window(
        "JpgLossless · 图片压缩工作台",
        url=html_path,
        js_api=api,
        width=1180,
        height=760,
        min_size=(960, 640),
    )
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
