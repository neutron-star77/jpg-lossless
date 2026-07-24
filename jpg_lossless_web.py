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
import re
import sys
import json
import base64
import io
import shutil
import queue
import threading
import tempfile
import subprocess
# Windows 下隐藏 ect/jpegtran 子进程的黑窗口；非 Windows 回退为 0（无此标志）
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import webview
from PIL import Image, ImageOps

def _app_dir():
    # 单文件打包时 __file__ 位于临时解压目录（_MEIPASS），若把配置写在那里退出即丢。
    # 故用户数据（配置/下载的引擎）统一落到 exe 所在目录，保证跨重启持久化。
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))
APP_DIR = _app_dir()
BIN_DIR = os.path.join(APP_DIR, "bin")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
FAILED_PATH = os.path.join(APP_DIR, "failed.json")

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


# ect 用 std::filesystem 打开文件，在 Windows 上遇到非 ASCII 路径（中文 / 全角括号等）
# 会报 “filesystem error: Cannot convert character sequence: Illegal byte sequence”
# 进而表现为 “encoding error 6”。最稳的绕法：把文件复制到临时 ASCII 路径让 ect 处理，
# 再拷回原路径。下面两个辅助函数即为此服务。
def _ascii_safe(path):
    """若路径含非 ASCII 字符，复制到临时 ASCII 名文件，返回 (safe_path, staged, real_path)。
    staged=True 时调用方处理完后须调用 _restore_staged 把结果拷回并清理临时文件。"""
    try:
        path.encode("ascii")
        return path, False, path
    except UnicodeEncodeError:
        ext = os.path.splitext(path)[1].lower() or ".tmp"
        fd, safe = tempfile.mkstemp(suffix=ext, prefix="ect_")
        os.close(fd)
        os.remove(safe)
        shutil.copy2(path, safe)
        return safe, True, path


def _restore_staged(safe, real, pre_size):
    """把 ect 处理后的临时文件（若更小）拷回原路径，并删除临时文件。"""
    try:
        if os.path.exists(safe) and os.path.getsize(safe) < (pre_size or 0):
            shutil.copy2(safe, real)
    except Exception:
        pass
    finally:
        try:
            os.remove(safe)
        except Exception:
            pass


def _as_text(v, default=""):
    """把可能来自前端的 list / None / 数字 规整成普通字符串，避免 .strip() 报错。"""
    if v is None:
        return default
    if isinstance(v, list):
        return _as_text(v[0], default) if v else default
    return str(v).strip()


def find_jpegtran():
    # 用于 ect 仍失败时的无损兜底（jpegtran 仅做 Huffman 优化，无损）
    dirs = []
    if getattr(sys, "_MEIPASS", None):
        dirs.append(os.path.join(sys._MEIPASS, "bin"))
    dirs.append(BIN_DIR)
    for d in dirs:
        p = os.path.join(d, "jpegtran.exe")
        if os.path.isfile(p):
            return p
    return shutil.which("jpegtran")


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
            safe, staged, real = _ascii_safe(dst)
            pre = os.path.getsize(dst)
            try:
                subprocess.run([engine, str(level), safe], check=True)
            finally:
                if staged:
                    _restore_staged(safe, real, pre)
        else:
            safe, staged, real = _ascii_safe(dst)
            pre = os.path.getsize(dst)
            try:
                subprocess.run([engine, str(level), "-strip", safe], check=True)
            finally:
                if staged:
                    _restore_staged(safe, real, pre)


def to_webp(src, dst, quality=95):
    q = int(quality)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if q >= 100:
            # 无损：libwebp 对照片既慢（数十秒）又会让文件变大，仅作可选
            im.save(dst, "WEBP", lossless=True, method=0)
            return
        # 有损：method=0 最快编码。但 libwebp 在部分大图(尤其 q≈90~95)会抛
        # “encoding error 6”，此处优先用用户设定的 q，失败则逐级降质量重试，
        # 尽量贴近设定且保证最终能成功编码（低质量反而更小，符合压缩意图）。
        last_err = None
        for qq in [q] + list(range(min(q - 5, 90), 49, -5)):
            try:
                im.save(dst, "WEBP", lossless=False, quality=qq, method=0)
                return
            except Exception as e:
                last_err = e
        raise last_err


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
        self.results = {}  # src -> (out_path, status, ok, old, new)，供“清理已完成/重试”使用
        self.failed = {}  # src -> {name, status, old, new}：上一次压缩失败的图片（持久化）
        self.drop_dir = os.path.join(tempfile.gettempdir(), "jpgll_drop")
        os.makedirs(self.drop_dir, exist_ok=True)
        self._queue = queue.Queue()  # 线程安全：压缩线程只往这里塞事件
        self._busy = False  # 是否有压缩任务在运行（用于前端看门狗恢复卡死的按钮）
        self._load_config()
        self._load_failed()

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
        # pywebview 在不同版本/后端下，对话框返回值可能是 str / list / tuple，且可能嵌套。
        # 递归展平为字符串列表，避免 "expected str ... not tuple" 之类错误。
        def flat(v):
            out = []
            if v is None:
                return out
            if isinstance(v, (list, tuple)):
                for x in v:
                    out.extend(flat(x))
            elif isinstance(v, str):
                out.append(v)
            else:
                try:
                    s = str(v)
                    if s:
                        out.append(s)
                except Exception:
                    pass
            return out
        paths = flat(paths)
        if not paths:
            return []
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

    # ---------- 失败图片记录（跨重启持久化） ----------
    def _load_failed(self):
        try:
            if os.path.exists(FAILED_PATH):
                with open(FAILED_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.failed = {k: v for k, v in data.items()
                                   if isinstance(v, dict) and "status" in v}
                elif isinstance(data, list):
                    self.failed = {}
                    for d in data:
                        if isinstance(d, dict) and d.get("src") and "status" in d:
                            self.failed[d["src"]] = {
                                "name": d.get("name", os.path.basename(d["src"])),
                                "status": d["status"],
                                "old": d.get("old", 0), "new": d.get("new", 0)}
        except Exception:
            self.failed = {}
        # 清理源文件已不存在的记录，并把仍然存在的失败图片恢复到内存（下次启动仍可见/可重试）
        missing = [s for s in list(self.failed) if not os.path.isfile(s)]
        for s in missing:
            self.failed.pop(s, None)
        if missing:
            self._save_failed()
        for src, info in list(self.failed.items()):
            if not any(src == s for s, _ in self.files):
                self.files.append((src, os.path.dirname(src)))
                old = info.get("old", 0)
                new = info.get("new", 0)
                self.results[src] = (src, info.get("status", "失败"), False, old, new)

    def _save_failed(self):
        try:
            with open(FAILED_PATH, "w", encoding="utf-8") as f:
                json.dump(self.failed, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _sync_failed(self):
        # 压缩结束后，根据本次结果更新失败记录：成功的移除，失败的（含重试成功的）更新/加入
        changed = False
        for src, base in self.files:
            r = self.results.get(src)
            if not r:
                continue
            if r[2]:  # ok
                if src in self.failed:
                    self.failed.pop(src, None)
                    changed = True
            else:
                st, old, new = r[1], r[3], r[4]
                rec = self.failed.get(src)
                if (not rec or rec.get("status") != st
                        or rec.get("old") != old or rec.get("new") != new):
                    self.failed[src] = {"name": os.path.basename(src),
                                        "status": st, "old": old, "new": new}
                    changed = True
        if changed:
            self._save_failed()

    def get_all_files(self):
        # 供前端启动时拉取当前内存中的文件（含上次恢复的失败图片）
        out = []
        for src, base in self.files:
            r = self.results.get(src)
            status = r[1] if r else "待处理"
            old = r[3] if r else 0
            new = r[4] if r else 0
            out_path = r[0] if r else src
            try:
                thumb = self._thumb_b64(src) if os.path.isfile(src) else ""
            except Exception:
                thumb = ""
            out.append({"src": src, "name": os.path.basename(src), "thumb": thumb,
                        "status": status, "old": old, "new": new, "out": out_path})
        return {"files": out}

    def get_failed_count(self):
        # 供前端判断启动时是否需要显示「正在载入失败图片」提示
        return {"count": len(self.failed)}

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

    def send_feedback(self, subject, body):
        # 调起系统默认邮件客户端，发送到指定邮箱
        addr = "359083341@qq.com"
        try:
            subject = urllib.parse.quote((subject or "")[:120])
            body = urllib.parse.quote((body or "")[:1500])
            link = "mailto:%s?subject=%s&body=%s" % (addr, subject, body)
            os.startfile(link)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _flat_paths(self, v):
        # 把对话框返回值（str / list / tuple，可能嵌套）展平为字符串路径列表
        out = []
        if v is None:
            return out
        if isinstance(v, (list, tuple)):
            for x in v:
                out.extend(self._flat_paths(x))
        elif isinstance(v, str):
            out.append(v)
        else:
            try:
                s = str(v)
                if s:
                    out.append(s)
            except Exception:
                pass
        return out

    def choose_files(self):
        # 注意：新版本 pywebview 的 file_types 必须是字符串格式 "描述(*.ext)"，
        # 旧版的 ("描述", "*.ext") 元组格式会让 parse_file_type 抛 TypeError，
        # 导致对话框打不开（表现为“点击选择文件没反应”）。
        r = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("图片(*.jpg;*.jpeg;*.png)",))
        out = []
        for p in self._flat_paths(r):
            if os.path.isfile(p) and p.lower().endswith(IMG_EXTS):
                out.append(p)
        return out

    def _pick_folder(self):
        # 直接用 webview 文件夹对话框（本机已验证可用，且支持地址栏粘贴/浏览路径）。
        # 返回值在不同版本/后端下可能是 str / list / tuple（含嵌套），统一规整成单个文件夹路径。
        try:
            r = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=True)
        except Exception:
            return None
        if r is None:
            return None
        if isinstance(r, str):
            return r if os.path.isdir(r) else None
        if isinstance(r, (list, tuple)):
            for x in r:
                if isinstance(x, (list, tuple)):
                    for y in x:
                        if isinstance(y, str) and os.path.isdir(y):
                            return y
                elif isinstance(x, str) and os.path.isdir(x):
                    return x
        return None

    def choose_dirs(self):
        path = self._pick_folder()
        if not path:
            return []
        out = []
        n = 0
        for root, _, fs in os.walk(path):
            for f in fs:
                n += 1
                if n % 200 == 0:  # 每扫 200 个文件上报一次，避免事件刷屏
                    self._emit({"t": "scan", "n": n})
                if f.lower().endswith(IMG_EXTS):
                    out.append(os.path.join(root, f))
        self._emit({"t": "scan", "n": n})  # 结束时再报一次最终值
        return out

    def choose_outdir(self):
        return self._pick_folder()

    def get_files_info(self, paths):
        # 给定一批图片路径，返回带缩略图的行（前端分批调用以显示 10/100 进度）
        out = []
        for p in (paths or []):
            if not os.path.isfile(p):
                continue
            if any(p == s for s, _ in self.files):
                continue
            self.files.append((p, os.path.dirname(p)))
            out.append({"src": p, "name": os.path.basename(p),
                        "thumb": self._thumb_b64(p)})
        return out

    def clear_files(self):
        # 仅清空软件内的文件清单记录与处理状态（不动磁盘上的任何文件）
        self.files = []
        self.results = {}
        return {"ok": True, "count": 0}

    def clear_done(self):
        # 仅清空「已成功压缩」的图片；压缩失败与尚未处理的图片保留（便于重试）
        kept = []
        for s, b in self.files:
            r = self.results.get(s)
            ok = r[2] if r else False
            if not ok:           # 失败或未处理 -> 保留
                kept.append((s, b))
        removed = len(self.files) - len(kept)
        self.files = kept
        self.results = {k: v for k, v in self.results.items()
                        if any(k == s for s, _ in kept)}
        # 注意：不清空 failed.json，失败的图片记录保留，重启后仍在。
        return {"removed": removed, "kept": len(kept)}

    def add_drop(self, items):
        out = []
        for it in (items or []):
            name = it.get("name", "image.dat")
            data = it.get("data", "")
            if not data:
                out.append({"name": name, "ok": False, "err": "数据为空"})
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
            except Exception as e:
                out.append({"name": name, "ok": False, "err": "写入失败: %s" % str(e)[:40]})
                continue
            if any(dst == s for s, _ in self.files):
                out.append({"name": os.path.basename(dst), "ok": False, "err": "重复图片"})
                continue
            self.files.append((dst, self.drop_dir))
            out.append({"src": dst, "name": os.path.basename(dst), "ok": True,
                        "thumb": self._thumb_b64(dst)})
        return out

    def remove_file(self, src):
        self.files = [(s, b) for s, b in self.files if s != src]
        self.results.pop(src, None)
        if src in self.failed:
            self.failed.pop(src, None)
            self._save_failed()
        return True

    def is_busy(self):
        return bool(self._busy)

    def start(self, settings):
        threading.Thread(target=self._process, args=(settings or {},), daemon=True).start()

    def retry_failed(self, settings):
        # 仅重试标记为「失败」的图片（status 以“失败”开头 / results 的 ok=False）
        threading.Thread(target=self._process, args=(settings or {}, True), daemon=True).start()

    # ---------- 处理（线程） ----------
    def _process(self, settings, retry=False):
        if self._busy:
            # 已有任务在跑，忽略重复启动，避免多线程同时写文件互相干扰
            self._emit({"t": "log", "m": "已有任务在运行，本次启动被忽略"})
            return
        self._busy = True
        self._emit({"t": "start"})  # 通知前端进入“处理中”状态（不确定进度）
        msg = "完成"
        try:
            if retry:
                failed = [(s, b) for (s, b) in self.files
                          if not self.results.get(s, (None, None, True))[2]]
                if not failed:
                    self._emit({"t": "log", "m": "没有需要重试的失败图片。"})
                    msg = "无需重试"
                else:
                    self._emit({"t": "log", "m": "开始重试 %d 张失败图片…" % len(failed)})
                    msg = self._run(settings, failed) or "完成"
            else:
                msg = self._run(settings) or "完成"
        except Exception as e:
            self._emit({"t": "log", "m": "处理异常：%s" % str(e)})
            msg = "失败：" + str(e)[:60]
        finally:
            # 关键修复：无论成功 / 异常 / 提前终止，都复位 _busy 并发出 finish，
            # 否则前端“开始压缩”按钮会永久卡在 disabled（表现为点不动）。
            self._busy = False
            self._emit({"t": "finish", "m": msg})

    def _run(self, settings, subset=None):
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

        files_snapshot = list(subset) if subset else list(self.files)
        total = len(files_snapshot)

        rename = bool(settings.get("rename", False))
        # 预计算每个文件的输出路径，统一处理“按数字重命名”，保证两种格式分支编号一致。
        # 重命名起始序号：扫描目标文件夹已有的“数字命名”文件取最大值+1，并与持久化的
        # rename_idx 取较大者——这样关机 / 换目录后也能接着往下排，不会从 001 重来。
        start_idx = 0
        if rename:
            try:
                start_idx = int(self.config.get("rename_idx", 0))
            except Exception:
                start_idx = 0
            target_dirs = set()
            if save_mode == "自定义文件夹" and out_dir:
                target_dirs.add(out_dir)
            else:
                for (src, base) in files_snapshot:
                    target_dirs.add(out_dir if save_mode == "自定义文件夹" else base)
            mx = 0
            for d in target_dirs:
                if os.path.isdir(d):
                    try:
                        for fn in os.listdir(d):
                            m = re.match(r'^0*(\d+)\.', fn)
                            if m:
                                mx = max(mx, int(m.group(1)))
                    except Exception:
                        pass
            start_idx = max(start_idx, mx)
        pad = len(str(max(total, start_idx + total))) if total else 1
        counter = start_idx
        out_paths = {}
        for (src, base) in files_snapshot:
            ext = (os.path.splitext(src)[1].lower() if fmt_mode == "原格式"
                   else FMT_EXT[fmt_mode])
            if rename:
                counter += 1
                name = "%0*d" % (pad, counter)
            else:
                fname = os.path.splitext(os.path.basename(src))[0]
                name = fname + suffix
            if save_mode == "自定义文件夹" and keep and not rename:
                rel = os.path.relpath(src, base)
                rel_noext = os.path.splitext(rel)[0]
                # 保持原目录结构时，也要把文件名后缀（suffix）加回去，否则会丢失
                out_path = os.path.join(out_dir, rel_noext + suffix + ext)
            else:
                odir = out_dir if save_mode == "自定义文件夹" else os.path.dirname(src)
                out_path = os.path.join(odir, name + ext)
            out_paths[src] = out_path
        if rename:
            try:
                self.config["rename_idx"] = counter
                self.set_config(self.config)
            except Exception:
                pass
            self._emit({"t": "log", "m": "输出将按数字顺序重命名（%0*d 补零，起始 %d）" % (pad, total, start_idx + 1)})

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
            # 中文 / 全角括号等路径 ect 打不开 -> 中转成临时 ASCII 路径再处理
            staging = {}  # safe_path -> (real_path, pre_size)
            target_safe_bn = {}  # real_path -> 临时名 basename（用于日志归因）
            safe_targets = []
            for t in targets:
                safe, staged, real = _ascii_safe(t)
                if staged:
                    staging[safe] = (real, os.path.getsize(real))
                safe_targets.append(safe)
            ect_log = []
            if safe_targets and is_ect:
                self._emit({"t": "log", "m": "批量调用 ect（--mt-file=%d），请稍候…" % threads})
                for i in range(0, len(safe_targets), 200):
                    chunk = safe_targets[i:i + 200]
                    cmd = [eng, str(level), "-strip", "--strict", "--mt-file=%d" % threads] + chunk
                    try:
                        proc = subprocess.run(cmd, check=False, stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE,
                                              creationflags=CREATE_NO_WINDOW,
                                              text=True, errors="replace")
                        ect_log.append((proc.stdout or "") + "\n" + (proc.stderr or ""))
                    except Exception as e:
                        self._emit({"t": "log", "m": "  ⚠ 批量压缩异常：%s" % str(e)})
                # 把中转文件（若更小）拷回原路径并清理临时文件
                for safe, (real, pre) in staging.items():
                    _restore_staged(safe, real, pre)
                # 中转文件的 ect 日志里用的是临时名，故同时用临时名 basename 匹配失败
                target_safe_bn = {real: os.path.basename(safe)
                                  for safe, (real, pre) in staging.items()}
                # （上面重建以便与循环外的空初始化保持一致；非 ect 路径下保持为空）
            elif targets and not is_ect:
                # jpegtran / jpegoptim 不支持 PNG，仅逐文件原地处理 jpg
                # 同样用 ASCII 中转绕开中文 / 全角括号路径
                for t in targets:
                    if os.path.splitext(t)[1].lower() in (".jpg", ".jpeg"):
                        try:
                            safe, staged, real = _ascii_safe(t)
                            pre = os.path.getsize(real)
                            if "jpegtran" in os.path.basename(eng).lower():
                                subprocess.run([eng, "-optimize", "-progressive", "-copy", "none",
                                                "-outfile", safe, safe], check=False)
                            else:
                                subprocess.run([eng, "--strip-all", "--all-progressive", safe], check=False)
                            if staged:
                                _restore_staged(safe, real, pre)
                        except Exception as e:
                            self._emit({"t": "log", "m": "  ⚠ %s 压缩失败：%s" % (t, str(e))})

            # 汇总结果行
            ect_out = "\n".join(ect_log)

            def _ect_real_error(text):
                # 从 ect 日志里提取真正的报错，并分类为「人类可读」的中文原因，
                # 让用户一眼看清到底是「路径问题」还是「图片本身损坏」。
                # 注意：这里只返回纯原因（不含“失败:”前缀），由调用方统一拼接。
                for line in text.splitlines():
                    low = line.lower()
                    # 1) 路径含中文/全角字符，ect 用 std::filesystem 打不开
                    #    （正常情况下 ASCII 中转已绕开，若仍出现说明中转后仍打不开）
                    if "cannot convert character sequence" in low or "filesystem error" in low:
                        return "路径含非ASCII字符(中文/全角括号)，ect无法打开（已尝试ASCII中转重试）"
                    # 2) 图片本身编码失败 / 损坏：优先抓具体错误串
                    if re.search(r'encoding error|corrupt|invalid|abort|truncat', line, re.I):
                        m = re.search(r'(encoding error[^\n]*|corrupt[^\n]*|invalid[^\n]*)', line, re.I)
                        detail = (m.group(1).strip() if m else line.strip())
                        return "图片无法编码/可能已损坏：%s" % detail[:120]
                    # 3) 其它带 error 的行，截断放宽到 120 字符，保留足够现场
                    if re.search(r'\berror\b|exception', line, re.I):
                        m = re.search(r'(error[^\n]*|exception[^\n]*)', line, re.I)
                        return (m.group(1).strip() if m else line.strip())[:120]
                return None

            for src, out_path, old in jobs:
                res = out_path if os.path.exists(out_path) else src
                try:
                    new = os.path.getsize(res)
                except Exception:
                    new = old
                # 先判定是否编码失败（如 “encoding error 6”）：文件未变小且 ect 日志含错误关键字
                status = None
                if new >= old:
                    bn = os.path.basename(src)
                    for line in ect_out.splitlines():
                        if (bn in line or target_safe_bn.get(res) in line) and re.search(
                                r'encoding error|error|fail|invalid|corrupt|exception|warn|abort|编码',
                                line, re.I):
                            status = '失败: ' + (_ect_real_error(line) or '编码失败')
                            break
                # 整批/多线程偶发失败（encoding error 6 多为中文/全角路径或边缘情况）：
                # 对该文件单独再跑一次 ect（无 --mt-file），并用 ASCII 中转路径确保能打开
                if status is not None and is_ect:
                    retry_target = res
                    try:
                        safe, staged, real = _ascii_safe(retry_target)
                        pre = os.path.getsize(retry_target)
                        rp = subprocess.run([eng, str(level), "-strip", "--strict", safe],
                                            check=False, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE,
                                            creationflags=CREATE_NO_WINDOW,
                                            text=True, errors="replace")
                        if staged:
                            _restore_staged(safe, real, pre)
                        new2 = os.path.getsize(real)
                        if new2 > 0 and new2 < old:
                            new = new2
                            status = "已压缩"
                            self._emit({"t": "log", "m": "  ↻ 单文件兜底重试成功：%s（%s）" % (
                                bn, human(new2))})
                        else:
                            real_err = _ect_real_error(rp.stdout + rp.stderr) or _ect_real_error(ect_out)
                            if real_err:
                                status = '失败: ' + real_err
                            # ect 单文件仍失败（内容级 encoding error 6 等）：
                            # 用 jpegtran 仅做 Huffman 优化（无损）兜底一次
                            try:
                                jt = find_jpegtran()
                                if jt:
                                    jsafe, jstaged, jreal = _ascii_safe(retry_target)
                                    jpre = os.path.getsize(retry_target)
                                    subprocess.run([jt, "-optimize", "-copy", "none",
                                                    "-outfile", jsafe, jsafe], check=False,
                                                   creationflags=CREATE_NO_WINDOW)
                                    if jstaged:
                                        _restore_staged(jsafe, jreal, jpre)
                                    new3 = os.path.getsize(retry_target)
                                    if new3 > 0 and new3 < old:
                                        new = new3
                                        status = "已压缩(无损兜底)"
                                        self._emit({"t": "log", "m": "  ↻ jpegtran 无损兜底成功：%s（%s）" % (
                                            bn, human(new3))})
                                    else:
                                        status = '失败: ' + real_err if real_err else '失败: 编码失败'
                            except Exception:
                                pass
                    except Exception as e:
                        status = '失败: ' + str(e)[:60]
                if status is None:
                    status = "已生成(原图保留)" if out_path != src else ("已压缩" if new < old else "已最优")
                if cap and new > cap:
                    self._emit({"t": "log", "m": "  ⚠ %s 无损后 %s 仍超 %dKB，已保留" % (
                        os.path.basename(src), human(new), max_kb)})
                self.results[src] = (out_path, status, not status.startswith("失败"), old, new)
                self._emit({"t": "row", "src": src, "old": old, "new": new, "status": status, "out": out_path})
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
                out_path = out_paths.get(src, src)
                try:
                    if not os.path.isfile(src):
                        st = "失败：源文件不存在(可能已被移动或删除)"
                        self.results[src] = (out_path, st, False, 0, 0)
                        self._emit({"t": "row", "src": src, "old": 0, "new": 0, "status": st})
                        return
                    old = os.path.getsize(src)
                    self._emit({"t": "log", "m": "正在处理：%s（%s）" % (os.path.basename(src), human(old))})
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
                    self.results[src] = (out_path, status, not status.startswith("失败"), old, new)
                    self._emit({"t": "row", "src": src, "old": old, "new": new, "status": status, "out": out_path})
                except Exception as e:
                    bn = os.path.basename(src)
                    msg = re.sub(r"\s+", " ", str(e)).strip()
                    if "cannot identify" in msg.lower() or "unidentified" in msg.lower():
                        st = "失败：图片无法识别(可能已损坏或非图片文件) %s" % bn
                    elif "truncated" in msg.lower() or "corrupt" in msg.lower():
                        st = "失败：图片已损坏 %s" % bn
                    else:
                        st = "失败：" + (msg[:80] if msg else "未知错误")
                    self.results[src] = (out_path, st, False, 0, 0)
                    self._emit({"t": "row", "src": src, "old": 0, "new": 0, "status": st})
                finally:
                    with done_lock:
                        state["done"] += 1
                        d = state["done"]
                        if state["first_out_dir"] is None:
                            state["first_out_dir"] = os.path.dirname(out_path)
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
        self._sync_failed()  # 同步“上一次失败图片”记录到磁盘
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
