#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qishui_auto.py — 汽水音乐「自动看广告领免费听时长」真机 UI 自动化

原理（与 cvabm/qishui 相同）：
  手机通过 ADB 连电脑，脚本循环 screencap 截图 + OCR 识别屏幕文字，
  按优先级模拟点击「取消误触 / 领取奖励 / 关闭弹窗 / 跳过 / 继续观看」，
  把官方活动的每日免费听时长攒出来。不修改 App、不伪造上报。

用法：
  python qishui_auto.py                 # 正常运行（先手动把手机停在广告页）
  python qishui_auto.py --once          # 只截一张图并打印 OCR 结果，不点击
  python qishui_auto.py --dry-run       # 正常识别与日志，但不执行点击
  python qishui_auto.py --device SERIAL # 指定设备

运行期间打开 http://127.0.0.1:8765 看监控面板。Ctrl+C 停止并恢复音量。
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / "runtime"
RUNTIME.mkdir(exist_ok=True)
CONFIG_PATH = BASE / "config.json"

# ---------------------------------------------------------------- utilities

def now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")

class Log:
    _lock = threading.Lock()

    def __init__(self, path: Path):
        self.path = path

    def __call__(self, msg: str) -> None:
        line = f"[{now_hms()}] {msg}"
        with self._lock:
            print(line, flush=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

def save_img(path: Path, img, quality: int = 85) -> bool:
    """cv2.imwrite 对中文路径会静默失败，改用 imencode+tofile。"""
    ext = path.suffix.lower()
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, img, params)
    if ok:
        path.write_bytes(buf.tobytes())
    return ok

def cleanup_images() -> int:
    """退出时删除 runtime/ 与 debug_clicks/ 下所有截图（jpg/png），日志与状态文件保留。"""
    n = 0
    for d in (RUNTIME, BASE / "debug_clicks"):
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass
    return n

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        example = BASE / "config.example.json"
        if example.exists():
            CONFIG_PATH.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise SystemExit("缺少 config.json：请从 config.example.json 复制一份")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

# ---------------------------------------------------------------- adb

def _resolve_adb(path: str) -> str:
    """adb 路径自适应：config 指定 → PATH 环境变量 → 常见 SDK 位置。"""
    import shutil
    cands = [path, "adb",
             str(Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe"),
             str(Path.home() / "Library/Android/sdk/platform-tools/adb"),
             "/usr/bin/adb"]
    for c in filter(None, cands):
        w = shutil.which(c)
        if w:
            return w
        if Path(c).exists():
            return c
    raise SystemExit("未找到 adb：请安装 Android platform-tools，"
                     "或在 config.json 的 adb_path 中指定完整路径")

class Adb:
    def __init__(self, adb_path: str, serial: str | None, log: Log):
        self.adb = _resolve_adb(adb_path)
        self.log = log
        self.serial = serial or self._pick_serial()
        # logical/physical scale for tap coordinates
        phys = self._parse_size(self.shell("wm size"), "Physical size")
        override = self._parse_size(self.shell("wm size"), "Override size")
        self.logical = override or phys
        self.physical = phys
        self.scale_x = self.logical[0] / self.physical[0] if self.physical else 1.0
        self.scale_y = self.logical[1] / self.physical[1] if self.physical else 1.0
        log(f"设备 {self.serial}，物理分辨率 {self.physical}，逻辑分辨率 {self.logical}")

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += args
        return subprocess.run(cmd, capture_output=True, timeout=30)

    def shell(self, cmd: str) -> str:
        r = self._run(["shell", cmd])
        return r.stdout.decode("utf-8", "replace").strip()

    def _pick_serial(self) -> str:
        out = self._run(["devices"]).stdout.decode("utf-8", "replace")
        serials = [ln.split("\t")[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]
        if not serials:
            print("未检测到 ADB 设备：请连接手机并开启 USB 调试（或在设置里开启无线调试）。", file=sys.stderr)
            sys.exit(1)
        if len(serials) > 1:
            print(f"检测到多个设备 {serials}，请用 --device 指定。", file=sys.stderr)
            sys.exit(1)
        return serials[0]

    @staticmethod
    def _parse_size(out: str, label: str) -> tuple[int, int] | None:
        m = re.search(rf"{label}\s*:\s*(\d+)x(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else None

    def screencap(self) -> np.ndarray | None:
        r = self._run(["exec-out", "screencap", "-p"])
        if r.returncode != 0 or not r.stdout:
            return None
        img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
        return img

    def tap(self, x_phys: float, y_phys: float) -> None:
        x = int(x_phys * self.scale_x)
        y = int(y_phys * self.scale_y)
        self.shell(f"input tap {x} {y}")
        self.log(f"点击 ({x},{y})  [物理坐标({int(x_phys)},{int(y_phys)})]")

    def keyevent(self, code: int) -> None:
        self.shell(f"input keyevent {code}")

    def media_volume(self) -> int | None:
        """读取媒体音量档位；读不到返回 None。"""
        out = self.shell("settings get system volume_music_speaker")
        m = re.search(r"^(\d+)$", out.strip(), re.M)
        return int(m.group(1)) if m else None

    def force_mute(self) -> None:
        """连按音量减到底，强制静音（ColorOS 会忽略 VOLUME_MUTE 键）。"""
        for _ in range(25):
            self.keyevent(25)  # KEYCODE_VOLUME_DOWN

    def current_focus(self) -> str:
        return self.shell("dumpsys window | grep -m1 mCurrentFocus")

# ---------------------------------------------------------------- ocr

class Ocr:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.engine = RapidOCR()

    def read(self, img: np.ndarray, min_conf: float) -> list[dict]:
        res, _ = self.engine(img)
        boxes = []
        for quad, text, conf in res or []:
            if float(conf) < min_conf:
                continue
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            boxes.append({
                "text": text.strip(),
                "conf": float(conf),
                "cx": (min(xs) + max(xs)) / 2,
                "cy": (min(ys) + max(ys)) / 2,
                "x1": min(xs), "x2": max(xs), "y1": min(ys), "y2": max(ys),
            })
        return boxes

def find(boxes: list[dict], targets: list[str], skip_locked: bool = False,
         max_len: int = 12, exactish: bool = False) -> dict | None:
    """按 targets 顺序匹配。只接受文本长度<=max_len 的"按钮体"匹配，
    防止广告免责声明等长句里的"完成/取消"字样被误认为按钮。
    exactish=True 时文本必须几乎等于关键词本身（差≤2字），用于『取消』类高危按钮。"""
    for t in targets:
        for b in boxes:
            txt = b["text"]
            if not t:
                continue
            ok = (txt == t or (not exactish and t in txt)) if exactish else t in txt
            if ok and len(txt) <= max(12, len(t) + 2 if exactish else max_len):
                if skip_locked and is_countdown_locked(txt):
                    continue
                return b
    return None

def countdown_left(boxes: list[dict]) -> int | None:
    """识别『跳过 5』这类按钮里的剩余秒数。"""
    for b in boxes:
        if "跳过" in b["text"]:
            m = re.search(r"(\d+)", b["text"])
            return int(m.group(1)) if m else 0
    return None

def is_countdown_locked(text: str) -> bool:
    """『04秒后可领取奖励』这类未解锁提示：带"秒后"且数字>0 时不可点。"""
    m = re.search(r"(\d+)\s*秒后", text)
    return bool(m) and int(m.group(1)) > 0

# ---------------------------------------------------------------- state

STATE_PATH = RUNTIME / "state.json"
SHOT_PATH = RUNTIME / "last.jpg"
MAX_LOG_LINES = 300

def write_state(**kw) -> None:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        state = {"date": str(date.today()), "claims": 0, "runs": 0}
    if state.get("date") != str(date.today()):
        state.update({"date": str(date.today()), "claims": 0})
    state.update(kw)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------------------------------------------------------- panel

PANEL_HTML = """
<!doctype html>
<html lang="zh" data-zcode-design><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>汽水音乐 · 流体云监控</title>
<style>
:root{
  --ink:#22293c; --muted:#5f6b84; --faint:#93a0ba;
  --blue:#0a7cff; --violet:#8a6cff; --green:#00d27e; --pink:#ff5e9a; --orange:#ffab3d;
  --radius:26px; --gap:20px;
  --glass:linear-gradient(155deg,rgba(255,255,255,.74),rgba(255,255,255,.48));
  --stroke:rgba(255,255,255,.8);
  --shadow:0 18px 44px rgba(56,86,140,.16), inset 0 1px 0 rgba(255,255,255,.9);
  --font:'Segoe UI Variable Display','Segoe UI','PingFang SC','HarmonyOS Sans SC','Microsoft YaHei',sans-serif;
  --mono:'Cascadia Code',Consolas,'Courier New',monospace;
}
*{box-sizing:border-box}
html,body{margin:0}
body{font-family:var(--font);color:var(--ink);min-height:100vh;overflow-x:hidden;
  background:#e9f0fb;padding:26px clamp(14px,4vw,56px) 40px;position:relative}
.bg{position:fixed;inset:0;z-index:-1;pointer-events:none}
.blob{position:absolute;border-radius:50%;filter:blur(74px);opacity:.5;
  animation:drift 24s ease-in-out infinite alternate}
.b1{width:54vw;height:54vw;left:-14vw;top:-18vw;background:radial-gradient(circle at 32% 30%,#7ec8ff,#2f7dff);animation-delay:-2s}
.b2{width:46vw;height:46vw;right:-12vw;top:-10vw;background:radial-gradient(circle at 32% 30%,#93ffd6,#00d27e);animation-delay:-8s}
.b3{width:42vw;height:42vw;left:22vw;bottom:-20vw;background:radial-gradient(circle at 32% 30%,#ffc3e2,#ff5e9a);animation-delay:-14s}
.b4{width:34vw;height:34vw;right:4vw;bottom:-14vw;background:radial-gradient(circle at 32% 30%,#ffe2ac,#ffab3d);animation-delay:-20s}
@keyframes drift{from{transform:translate(0,0) scale(1)}to{transform:translate(5vw,4vh) scale(1.18)}}
/* 流体云胶囊 */
.island{margin:0 auto 26px;display:flex;align-items:center;gap:10px;width:max-content;
  padding:11px 24px;border-radius:999px;background:rgba(16,21,33,.82);color:#fff;
  backdrop-filter:blur(20px) saturate(1.6);box-shadow:0 14px 34px rgba(16,21,33,.32);
  font-size:14px;transition:background .5s}
.island .dot{width:10px;height:10px;border-radius:50%;flex:none;
  background:linear-gradient(120deg,var(--green),var(--blue));box-shadow:0 0 14px rgba(0,210,126,.9);
  animation:pulse 1.6s ease-out infinite}
.island.dead .dot{background:#7c8aa5;box-shadow:none;animation:none}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(0,210,126,.55)}70%{box-shadow:0 0 0 10px rgba(0,210,126,0)}100%{box-shadow:0 0 0 0 rgba(0,210,126,0)}}
.island b{font-weight:650}
.island .grad{background:linear-gradient(90deg,#7ec8ff,#93ffd6);-webkit-background-clip:text;background-clip:text;color:transparent;font-weight:650}
.island .sep{opacity:.35}
.layout{display:grid;grid-template-columns:minmax(300px,360px) minmax(250px,330px) 1fr;gap:var(--gap);align-items:stretch}
@media(max-width:1000px){.layout{grid-template-columns:1fr 1fr}.logs{grid-column:1/-1}}
@media(max-width:660px){.layout{grid-template-columns:1fr}}
.card{background:var(--glass);border:1px solid var(--stroke);border-radius:var(--radius);
  box-shadow:var(--shadow);backdrop-filter:blur(22px) saturate(1.5);padding:22px;
  transition:transform .2s ease,box-shadow .2s ease}
.card:hover{transform:translateY(-3px);box-shadow:0 26px 54px rgba(56,86,140,.22),inset 0 1px 0 rgba(255,255,255,.9)}
.til{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);margin-bottom:12px}
.til svg{width:15px;height:15px}
.i-blue{fill:var(--blue)}.i-green{fill:var(--green)}.i-violet{fill:var(--violet)}.i-pink{fill:var(--pink)}
.col{display:flex;flex-direction:column;gap:var(--gap)}
.num{font-size:64px;font-weight:800;line-height:1.05;letter-spacing:-.03em;
  background:linear-gradient(110deg,var(--blue),var(--violet) 45%,var(--pink) 85%);
  -webkit-background-clip:text;background-clip:text;color:transparent;transition:filter .3s}
.num small{font-size:22px;font-weight:650;color:var(--faint);margin-left:8px;-webkit-text-fill-color:var(--faint)}
.chips{display:flex;gap:8px;margin:14px 0 6px;flex-wrap:wrap}
.chip{font-size:12.5px;font-weight:600;color:#fff;padding:6px 13px;border-radius:999px;
  box-shadow:0 8px 18px rgba(56,86,140,.22)}
.c-blue{background:linear-gradient(120deg,#4f9bff,#0a7cff)}
.c-green{background:linear-gradient(120deg,#37e7a4,#00b96b)}
.c-orange{background:linear-gradient(120deg,#ffc06a,#ff9526)}
.c-gray{background:linear-gradient(120deg,#a9b6cf,#7c8aa5)}
.bar{height:10px;border-radius:99px;background:rgba(120,140,180,.18);overflow:hidden}
.bar i{display:block;height:100%;width:0;border-radius:99px;position:relative;
  background:linear-gradient(90deg,var(--blue),var(--green) 55%,var(--orange));
  transition:width .9s cubic-bezier(.2,.8,.2,1)}
.bar i::after{content:"";position:absolute;inset:0;
  background:linear-gradient(110deg,transparent 30%,rgba(255,255,255,.55) 50%,transparent 70%);
  background-size:220% 100%;animation:shine 2.6s linear infinite}
@keyframes shine{from{background-position:180% 0}to{background-position:-60% 0}}
.bar-note{display:flex;justify-content:space-between;font-size:11.5px;color:var(--faint);margin-top:8px}
.row{display:grid;grid-template-columns:1fr 1.2fr;gap:var(--gap)}
.mnum{font-size:30px;font-weight:750;background:linear-gradient(120deg,var(--violet),var(--pink));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.msub{font-size:11.5px;color:var(--faint);margin-top:4px}
.mact{font-family:var(--mono);font-size:12.5px;color:var(--ink);word-break:break-all;line-height:1.5}
.phone{display:flex;flex-direction:column}
.frame{flex:1;border-radius:24px;padding:3px;
  background:linear-gradient(160deg,#7ec8ff,#93ffd6 40%,#ffc3e2 75%,#ffe2ac);
  box-shadow:0 14px 34px rgba(56,86,140,.18)}
.frame>div{height:100%;display:grid;place-items:center;background:#0d1120;border-radius:21px;padding:10px;min-height:300px}
.frame img{max-height:540px;max-width:100%;border-radius:12px}
.logs{display:flex;flex-direction:column}
.logbox{flex:1;overflow:auto;border-radius:18px;padding:14px 16px;min-height:360px;max-height:660px;
  background:rgba(255,255,255,.52);border:1px solid rgba(255,255,255,.7);
  font-family:var(--mono);font-size:12.5px;line-height:1.8;color:#33405e;white-space:pre-wrap}
.logbox::-webkit-scrollbar{width:8px}.logbox::-webkit-scrollbar-thumb{background:rgba(120,140,180,.35);border-radius:8px}
footer{margin-top:24px;text-align:center;font-size:12px;color:var(--faint)}
</style></head><body>
<div class="bg"><i class="blob b1"></i><i class="blob b2"></i><i class="blob b3"></i><i class="blob b4"></i></div>
<div class="island" id="island">
  <span class="dot"></span><b id="istate">连接中…</b>
  <span class="sep">·</span><span class="grad" id="iday">—</span>
  <span class="sep" id="isep" style="display:none">·</span><span id="ineed"></span>
</div>
<div class="layout">
  <div class="col">
    <div class="card">
      <div class="til"><svg class="i-blue" viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 10.4 3.2 2.3-1 1.4L11 13V6h2z"/></svg>免费听天数 · 上限 30</div>
      <div class="num" id="pool">—<small>天</small></div>
      <div class="chips"><span class="chip c-gray" id="needchip">等待弹窗识别</span><span class="chip c-blue" id="pctchip" style="display:none"></span></div>
      <div class="bar"><i id="poolbar"></i></div>
      <div class="bar-note"><span>0</span><span id="poolpct"></span><span>30 天</span></div>
    </div>
    <div class="row">
      <div class="card"><div class="til"><svg class="i-violet" viewBox="0 0 24 24"><path d="M13 2 3 14h7l-1 8 10-12h-7z"/></svg>今日发起</div><div class="mnum" id="claims">—</div><div class="msub">次点击（含未到账）</div></div>
      <div class="card"><div class="til"><svg class="i-pink" viewBox="0 0 24 24"><path d="M4 5h16v2H4zm0 6h16v2H4zm0 6h10v2H4z"/></svg>最近动作</div><div class="mact" id="last">—</div></div>
    </div>
  </div>
  <div class="card phone">
    <div class="til"><svg class="i-green" viewBox="0 0 24 24"><path d="M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm3 17h4v1h-4z"/></svg>手机实时画面 · 每 2 秒</div>
    <div class="frame"><div><img id="shot" alt="手机画面"></div></div>
  </div>
  <div class="card logs">
    <div class="til"><svg class="i-blue" viewBox="0 0 24 24"><path d="M12 20a8 8 0 1 0-8-8 8 8 0 0 0 8 8zm0-10a2 2 0 1 1 0 4 2 2 0 0 1 0-4z"/></svg>运行日志</div>
    <div class="logbox" id="log"></div>
  </div>
</div>
<footer>汽水音乐自动化 · 仅本机 127.0.0.1:8765 · 先「启动」再看面板</footer>
<script>
const logEl=document.getElementById('log');let stick=true;
logEl.onscroll=()=>stick=logEl.scrollTop+logEl.clientHeight<logEl.scrollHeight-30;
const $=id=>document.getElementById(id);
async function tick(){
 try{
  const s=await (await fetch('/state?ts='+Date.now())).json();
  const d=s.pool_days,n=s.pool_need;
  $('istate').textContent=s.alive?'运行中':'已停止';
  $('island').className='island'+(s.alive?'':' dead');
  $('iday').textContent=d!=null?'第 '+d+' 日免费听':'等待识别';
  $('isep').style.display=(d!=null&&n>0)?'':'none';
  $('ineed').textContent=(d!=null&&n>0)?'还差 '+n+' 个视频':'';
  $('pool').innerHTML=(d!=null?d:'—')+'<small>天</small>';
  $('needchip').textContent=d==null?'等待弹窗识别':(n>0?'还差 '+n+' 个视频达成第 '+d+' 日':'第 '+d+' 日已达成');
  $('needchip').className='chip '+(d==null?'c-gray':(n>0?'c-orange':'c-green'));
  const pct=d!=null?Math.min(d/30*100,100):0;
  $('poolbar').style.width=pct+'%';
  $('poolpct').textContent=pct?pct.toFixed(0)+'%':'';
  $('pctchip').style.display=pct?'':'none';
  $('pctchip').textContent='进度 '+pct.toFixed(0)+'%';
  $('claims').textContent=s.claims??'—';
  $('last').textContent=s.last_action??'—';
  $('shot').src='/shot?ts='+Date.now();
  const t=await (await fetch('/tail?ts='+Date.now())).text();
  if(t!==logEl.textContent){logEl.textContent=t;if(stick)logEl.scrollTop=logEl.scrollHeight;}
 }catch(e){
  $('istate').textContent='连接断开';
  $('island').className='island dead';
 }
}
tick();setInterval(tick,2000);
</script></body></html>
"""

LOG_LINES: list[str] = []

class PanelHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(PANEL_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/state":
            try:
                data = STATE_PATH.read_bytes()
            except FileNotFoundError:
                data = b"{}"
            self._send(data, "application/json; charset=utf-8")
        elif path == "/shot":
            try:
                self._send(SHOT_PATH.read_bytes(), "image/jpeg")
            except FileNotFoundError:
                self._send(b"", "image/jpeg")
        elif path == "/tail":
            self._send("\n".join(LOG_LINES[-MAX_LOG_LINES:]).encode("utf-8"),
                       "text/plain; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()

def start_panel(port: int, log: Log) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), PanelHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"监控面板: http://127.0.0.1:{port}")

# ---------------------------------------------------------------- main loop

def run(cfg: dict, args) -> None:
    log_path = RUNTIME / f"run_{date.today()}.jsonl"
    text_log = RUNTIME / f"run_{date.today()}.log"
    log = Log(text_log)

    def logall(msg: str) -> None:
        line = f"[{now_hms()}] {msg}"
        LOG_LINES.append(line)
        log(msg)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "msg": msg}, ensure_ascii=False) + "\n")

    adb = Adb(cfg["adb_path"], args.device or cfg.get("serial"), logall)
    ocr = Ocr()
    logall("初始化完成，等待手机停在广告页面…按 Ctrl+C 停止")
    start_panel(cfg["panel_port"], logall)

    if cfg["mute_during_run"]:
        adb.force_mute()
        logall("已静音开跑（全程强制静音，结束后也不恢复音量）")
    if cfg["keep_screen_on"]:
        adb.shell("svc power stayon usb")

    dry_run = args.dry_run or cfg.get("dry_run", False)
    poll = cfg["poll_interval"]
    goal = cfg["daily_goal"]                # 0 = 无上限，越多越好
    stuck_after = cfg["stuck_archive_after"]
    min_conf = cfg["ocr_min_confidence"]
    max_runtime = cfg.get("max_runtime_min", 360) * 60      # 硬时长上限
    idle_stop = cfg.get("max_stuck_rounds", 6) * stuck_after # 连续无进展自动收工

    state = {"last_action": "待命", "claims": 0, "runs": 0, "pool_days": None, "pool_need": None}
    try:
        s = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if s.get("date") == str(date.today()):
            state["claims"] = s.get("claims", 0)
            state["runs"] = s.get("runs", 0)
            # 兼容旧字段：pool_target 即弹窗天数
            state["pool_days"] = s.get("pool_days") or s.get("pool_target")
            state["pool_need"] = s.get("pool_need")
    except Exception:
        pass

    last_action_time = time.time()
    start_time = time.time()
    last_claim_time = time.time()
    prev_key = None
    prev_box = None
    repeat_n = 0
    saw_countdown = False
    try:
        while True:
            img = adb.screencap()
            if img is None:
                logall("截图失败，2 秒后重试")
                time.sleep(2)
                continue
            save_img(SHOT_PATH, img, quality=80)

            boxes = ocr.read(img, min_conf)
            texts = " | ".join(b["text"] for b in boxes[:12]) or "(无文字)"
            if cfg["mute_during_run"]:
                # 巡检：系统/广告SDK把音量调回去就立刻压回 0
                v = adb.media_volume()
                if v not in (None, 0):
                    adb.force_mute()
                    logall(f"检测到媒体音量被调回 {v}，已重新压回静音")
            pi = pool_info(boxes)
            if pi and (state["pool_days"] or 0) < pi["target"]:
                state["pool_days"] = pi["target"]
                state["pool_need"] = pi["need"]
                logall(f"奖励池弹窗：第 {pi['target']} 日免费听（还差 {pi['need']} 个视频达成）")
            action = decide(cfg, boxes, img.shape[0])
            has_countdown = any("秒后可领取" in b["text"] for b in boxes)
            if has_countdown:
                saw_countdown = True

            if action is None:
                if saw_countdown and not has_countdown:
                    # 倒计时刚消失 => 广告播完停在落地页，BACK 回奖励弹窗
                    focus = adb.current_focus()
                    if cfg.get("package", "com.luna.music") in focus:
                        adb.keyevent(4)
                        logall("广告播完，BACK 返回奖励弹窗")
                    else:
                        logall(f"焦点不在汽水音乐({focus.strip()})，跳过 BACK")
                    saw_countdown = False
                    last_action_time = time.time()
                    repeat_n = 0
                elif time.time() - last_action_time > stuck_after:
                    stuck_dir = BASE / "debug_clicks"
                    stuck_dir.mkdir(exist_ok=True)
                    p = stuck_dir / f"stuck_{datetime.now():%H%M%S}.jpg"
                    save_img(p, img)
                    logall(f"超过 {stuck_after}s 无可操作按钮，已存截图 {p.name}（当前: {texts}）")
                    last_action_time = time.time()
                    # 落地页/直播卡片特征：BACK 自救一次（绝不点『进入直播/立即下载』诱饵）
                    if any(m in b["text"] and len(b["text"]) <= 6
                           for b in boxes for m in ("立即下载", "进入直播")):
                        focus = adb.current_focus()
                        if cfg.get("package", "com.luna.music") in focus:
                            adb.keyevent(4)
                            logall("检测到广告落地/直播卡片，BACK 自救")
                            repeat_n = 0
            else:
                key, b = action
                if prev_key == key and time.time() - last_action_time < poll * 2.5:
                    pass  # 刚点过同类按钮，等页面变化（不计入连点）
                else:
                    same_target = (key == prev_key and prev_box is not None
                                   and abs(b["cx"] - prev_box[0]) < 40
                                   and abs(b["cy"] - prev_box[1]) < 40)
                    repeat_n = repeat_n + 1 if same_target else 1
                    prev_box = (b["cx"], b["cy"])
                    prev_key = key
                    last_action_time = time.time()
                    if not dry_run and repeat_n >= 3:
                        # 保险丝：同一目标真实连点3次屏幕仍不变 => 大概率误判，存图休整
                        stuck_dir = BASE / "debug_clicks"
                        stuck_dir.mkdir(exist_ok=True)
                        p = stuck_dir / f"repeat_{datetime.now():%H%M%S}.jpg"
                        save_img(p, img)
                        logall(f"同一目标「{b['text']}」真实连点{repeat_n}次无变化，疑似误判，休整 180s（已存 {p.name}）")
                        repeat_n = 0
                        saw_countdown = False
                        time.sleep(180)
                    elif dry_run:
                        logall(f"[dry-run] 应点击「{b['text']}」({key})  当前: {texts}")
                    else:
                        adb.tap(b["cx"], b["cy"])
                        logall(f"点击「{b['text']}」({key})")
                        if key == "领取奖励":
                            state["claims"] += 1     # 点中央领取按钮=完成一轮
                            saw_countdown = False
                            last_claim_time = time.time()
                        if key == "跳过":
                            state["runs"] += 1
                        state["last_action"] = f"{now_hms()} {key}:{b['text']}"

            write_state(date=str(date.today()), claims=state["claims"],
                        runs=state["runs"], pool_days=state["pool_days"],
                        pool_need=state["pool_need"],
                        last_action=state["last_action"], alive=True)
            if goal and state["claims"] >= goal:
                logall(f"已达目标 {goal} 次，停止。")
                break
            if time.time() - start_time > max_runtime:
                logall(f"已达最长运行时长 {int(max_runtime/60)} 分钟，收工。")
                break
            if time.time() - last_claim_time > idle_stop:
                logall(f"{int(idle_stop/60)} 分钟没有新奖励进账，视为当前可领已刷完，收工。")
                break
            time.sleep(poll)
    finally:
        if cfg["mute_during_run"]:
            adb.force_mute()
            logall("已保持静音状态（按设置不恢复音量；想听声音请按手机音量+）")
        adb.shell("svc power stayon false")
        write_state(date=str(date.today()), claims=state["claims"],
                    runs=state["runs"], pool_days=state["pool_days"],
                    pool_need=state["pool_need"],
                    last_action=state["last_action"], alive=False)
        if cfg.get("cleanup_on_exit", True):
            n = cleanup_images()
            logall(f"退出自动清理：删除 {n} 个截图文件（日志保留）")
        logall("已停止（音量保持静音；屏幕设置已恢复）。")

def pool_info(boxes: list[dict]) -> dict | None:
    """解析奖励绿色弹窗：『再看X个视频提前得 第Y日免费听』。
    第Y日 = 弹窗显示的当前免费听天数（App 口径，上限 30）；X = 还差几个视频解锁。
    返回 {target, need}；无日序号弹窗(如今日任务)返回 None。"""
    joined = " ".join(b["text"] for b in boxes)
    mt = re.search(r"第(\d+)日免费听", joined)
    if not mt:
        return None
    mn = re.search(r"再看(\d+)个视频", joined)
    return {"target": int(mt.group(1)), "need": int(mn.group(1)) if mn else 0}

def decide(cfg: dict, boxes: list[dict], screen_h: int) -> tuple[str, dict] | None:
    """状态机（依据真机实测）：
    1. 误入直播间/广告跳转 -> 取消
    2. 中央绿色『领取奖励』大按钮（不含"N秒后"倒计时） -> 点它，算一次领取
    3. 弹窗确认按钮（完成/我知道了）
    4. 『继续观看/再看一条』
    5. 广告跳过按钮（倒计时到）
    注：顶部"N秒后可领取奖励"与"领取成功 ×"都是状态角标，不是按钮，绝不点击。
    """
    b = find(boxes, cfg["cancel_targets"], exactish=True)
    if b:
        return "取消", b
    # 领取奖励：只认位于画面中下部、短文本的大按钮
    for t in cfg["reward_targets"]:
        for box in boxes:
            if t in box["text"] and len(box["text"]) <= 12 \
               and not is_countdown_locked(box["text"]) \
               and box["cy"] > screen_h * 0.4:
                return "领取奖励", box
    b = find(boxes, cfg["close_targets"])
    if b:
        return "关闭弹窗", b
    b = find(boxes, cfg["go_targets"])
    if b:
        return "继续观看", b
    left = countdown_left(boxes)
    if left is not None and left <= cfg["skip_after_seconds"]:
        b = find(boxes, cfg["skip_targets"])
        if b:
            return "跳过", b
    return None

def once(cfg: dict, args) -> None:
    adb = Adb(cfg["adb_path"], args.device or cfg.get("serial"), Log(Path("NUL")))
    ocr = Ocr()
    img = adb.screencap()
    if img is None:
        print("截图失败")
        return
    out = BASE / "runtime" / f"once_{datetime.now():%H%M%S}.png"
    save_img(out, img)
    print(f"截图已存: {out}")
    for b in ocr.read(img, cfg["ocr_min_confidence"]):
        print(f"  ({int(b['cx'])},{int(b['cy'])})  conf={b['conf']:.2f}  {b['text']}")

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="汽水音乐自动看广告助手")
    ap.add_argument("--once", action="store_true", help="截一屏并打印 OCR 文字，不点击")
    ap.add_argument("--dry-run", action="store_true", help="识别并打日志，但不执行点击")
    ap.add_argument("--device", help="adb 序列号")
    args = ap.parse_args()
    cfg = load_config()
    if args.once:
        once(cfg, args)
    else:
        run(cfg, args)

if __name__ == "__main__":
    main()
