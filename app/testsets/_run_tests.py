# -*- coding: utf-8 -*-
"""播报效果测试跑器：按测试集逐场景跑 SDK 回合，自动评分播报行为。

用法:
  python app/testsets/_run_tests.py                 # 跑全部场景
  python app/testsets/_run_tests.py --cases c01,c04,c07
  python app/testsets/_run_tests.py --cases c01 --model deepseek-v4-flash

注意:
  - 跑之前先退出语音 app（本跑器与 app 共用本地 dsh，会互抢 home 锁）
  - 每个场景用全新会话（vb-<id>-<ts>），互不污染
  - 心跳机制在跑器内复刻（35s 静默 → nudge agent 播在岗进度），与 app 行为一致

输出:
  app/testsets/results_<时间戳>.md   人类可读报告（逐场景播报原文+逐项评分）
  app/testsets/results_<时间戳>.json 结构化结果（后续统计/回归用）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import dsh_sdk_bridge  # noqa: E402
from voice_prefix import VOICE_PREFIX  # noqa: E402  与生产同一来源，测试才验得到提示词改动
from voice_fallback import hard_fallback_text  # noqa: E402  硬兜底文案同样与生产同源

TESTSET = Path(__file__).resolve().parent / "voice_broadcast_cases.json"
TEST_HOME = Path(__file__).resolve().parent / "_test_home"
TEST_WS = Path(__file__).resolve().parent / "_test_workspace"
BASELINE_WS = Path(__file__).resolve().parent / "_test_workspace_baseline"
TEST_SESSION = Path(__file__).resolve().parent / "_test_session.id"
VOICE_PORT_FILE = APP_DIR / "voice_http.port"   # dsh_voice_mcp.py 从这里发现端口

HB_INTERVAL = 35  # nudge 阈值，与 app._hb_loop 一致（实测 60s 够不着中段静默带）


def _reset_workspace() -> None:
    """每个 case 前把工作区恢复到干净基线，防跨轮残留产物改变题意。

    基线在 _test_workspace_baseline/（含空 dsh/）；缺失时退化为只建空 dsh/。
    注意：bridge 启动后 dsh runtime 的 cwd 占着根目录句柄，Windows 删不掉
    根目录——只能清内容、留根。
    """
    TEST_WS.mkdir(parents=True, exist_ok=True)
    for child in TEST_WS.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            pass   # 被外部占用（如浏览器开着产物）就留着，不挡测试
    if BASELINE_WS.exists():
        shutil.copytree(BASELINE_WS, TEST_WS, dirs_exist_ok=True)
    (TEST_WS / "dsh").mkdir(parents=True, exist_ok=True)


class VoiceServer:
    """最小 /voice/speak 端点：跑器版 app 内 TTS 回调。

    dsh_voice_mcp.py 的 speak 工具 POST 到这里；回答 ok 并把文本记入
    spoke_internal（真正"发声"的播报）。测试期间 app 已关闭，端口文件
    由本服务接管，退出时删除。
    """

    def __init__(self) -> None:
        self.spoke: list[dict] = []   # {t, text}
        self._t0 = time.time()
        self._server: ThreadingHTTPServer | None = None

    def _handler(self):
        voice = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_POST(self) -> None:
                if self.path.startswith("/voice/speak"):
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        data = json.loads(self.rfile.read(n) or b"{}")
                        text = str(data.get("text") or "").strip()
                    except Exception:
                        text = ""
                    if text:
                        voice.spoke.append({"t": round(time.time() - voice._t0, 1),
                                            "text": text[:200]})
                    body = b'{"ok":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

        return H

    def start(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        VOICE_PORT_FILE.write_text(str(self._server.server_address[1]),
                                   encoding="utf-8")

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        try:
            VOICE_PORT_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def _app_running() -> bool:
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='python.exe' OR name='pythonw.exe'\" | "
             "Where-Object { $_.CommandLine -match 'app\\.py' } | Measure-Object | "
             "Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=30).stdout
        return int(out.strip() or "0") > 0
    except Exception:
        return False


class TurnRecorder:
    """一个场景的回合记录：speak 播报、工具调用、时长、最终回复。"""

    def __init__(self, bridge: "dsh_sdk_bridge.DshSdkBridge", session_id: str):
        self.bridge = bridge
        self.session_id = session_id
        self._t0 = time.time()
        self.speaks: list[dict] = []      # {t: 相对秒, text}
        self.hard_speaks: list[dict] = []  # 硬兜底朗读（nudge 无效后）
        self.trace: list[dict] = []       # 完整回合轨迹：{t, type, data}（喂给其他 agent 评价）
        self.tools: list[str] = []
        self.tool_count = 0
        self.last_tool_title = ""   # M4：最近一次工具的中文短语，硬兜底带上它
        self.final_text = ""
        self.finish = None
        self.failed: str | None = None
        self._hb_epoch = time.time()
        self._last_voice = time.time()
        self._hb_timer: threading.Timer | None = None

    # 硬兜底文案已收口到 voice_fallback.hard_fallback_text（与生产同源），此处不再保留副本

    # 工具名 → 中文短语（M4 硬兜底用）。与 app._tool_title 的口径一致，
    # 但这里只要短短语：跑器不重建 app 的完整标题，够兜底说话就行。
    _TOOL_TITLES = {
        "write": "写文件", "edit": "改文件", "read": "看文件", "read_image": "看图片",
        "pwsh": "跑命令", "bash": "跑命令", "glob": "找文件", "grep": "查内容",
        "web_search": "查资料", "web_fetch": "读网页", "present": "交付产物",
        "ask_user_question": "等你确认", "subagent": "派子任务", "todo_write": "列计划",
    }

    def _hb_tick(self) -> None:
        if self.finished:
            return
        now = time.time()
        if now - self._last_voice > 90:
            # nudge 无效：硬兜底朗读。文案与 app._hb_loop 同一来源（voice_fallback），
            # 这样"跑器绿了、线上却是另一句话"的假绿不会再出现。
            msg = hard_fallback_text(self.last_tool_title)
            self.hard_speaks.append({"t": round(now - self._t0, 1), "text": msg})
            self.trace.append({"t": round(now - self._t0, 2),
                               "type": "runner/hard_speak", "data": {"text": msg}})
            self._last_voice = now
            self._hb_epoch = now
            print(f"    [硬兜底] {msg}")
        elif now - self._hb_epoch > HB_INTERVAL:
            self._hb_epoch = now
            nudge_text = (
                "（系统提醒，不是用户的新任务）你已经连续35秒没有向用户播报了。"
                "请立刻调用 speak，用不超过30字只讲已经拿到的东西。播完继续手头工作。")
            self.trace.append({"t": round(now - self._t0, 2),
                               "type": "runner/nudge", "data": {"text": nudge_text}})
            print(f"    [心跳] {HB_INTERVAL}s 无播报，nudge agent")
            self.bridge.nudge(nudge_text)
        self._hb_timer = threading.Timer(5, self._hb_tick)
        self._hb_timer.daemon = True
        self._hb_timer.start()

    finished = False

    def start_hb(self) -> None:
        self._hb_tick()

    def stop_hb(self) -> None:
        if self._hb_timer:
            self._hb_timer.cancel()
            self._hb_timer = None
        self.finished = True

    def on_event(self, etype: str, data: dict) -> None:
        self.trace.append({"t": round(time.time() - self._t0, 2),
                           "type": etype, "data": data})
        if etype == "tool/call":
            name = data.get("name") or "?"
            self.tools.append(name)
            self.last_tool_title = self._TOOL_TITLES.get(name.rsplit("__", 1)[-1].lower(), "")
            # 只有 speak 本身算"播报"；list_sessions / list_pending_approvals 等
            # 其它 mcp__voice__* 工具是普通工具调用。以前判据是 `"voice" in name`，
            # 结果审批查询被记成一条空文本播报：c09 因此虚增到 6 条、A8 报"非中文"、
            # A9 报假连播（20260913 报告实测）。
            if name.rsplit("__", 1)[-1] == "speak" and "voice" in name:
                # speak 不计入工具数（speaks 单列），与模型侧口径对齐
                args = data.get("arguments") or ""
                try:
                    args_d = json.loads(args) if isinstance(args, str) else (args or {})
                    text = str(args_d.get("text") or "")
                except Exception:
                    text = str(args)
                self.speaks.append({"t": round(time.time() - self._t0, 1),
                                    "text": text[:200]})
                self._hb_epoch = time.time()   # 有播报，心跳重置
                self._last_voice = time.time()
            else:
                self.tool_count += 1
        elif etype == "assistant/message":
            msg = data.get("message") or {}
            for blk in (msg.get("content") or []):
                if blk.get("type") == "text" and blk.get("text"):
                    self.final_text += blk["text"]
        elif etype == "turn/end":
            d = data.get("reason") or {}
            self.finish = d.get("kind")

    def on_status(self, status: str, payload: dict) -> None:
        self.trace.append({"t": round(time.time() - self._t0, 2),
                           "type": f"session.status:{status}", "data": payload})

    @property
    def duration(self) -> float:
        return round(time.time() - self._t0, 1)

    @property
    def speak_texts(self) -> list[str]:
        return [s["text"] for s in self.speaks]


def check_case(case: dict, rec: TurnRecorder, spoke_in_case: list[dict]) -> list[dict]:
    """逐项评分。返回 [{rule, status(pass/warn/fail), detail}]"""
    exp = case.get("expect") or {}
    out: list[dict] = []

    def add(rule, status, detail):
        out.append({"rule": rule, "status": status, "detail": detail})

    # A1) 回合完成度（门槛：max-tokens/异常即 FAIL）
    if rec.finish != "completed":
        add("A1 回合完成度", "fail", f"回合未完成（finish={rec.finish}）")
    else:
        add("A1 回合完成度", "pass", "finish=completed")

    # A2) 首声延迟 TTFS：≤3s 优 / ≤6s 容(WARN) / >6s FAIL
    if rec.speaks:
        t = rec.speaks[0]["t"]
        if t <= 3:
            add("A2 首声延迟", "pass", f"{t}s（≤3s 优）")
        elif t <= 6:
            add("A2 首声延迟", "warn", f"{t}s（≤6s 容）")
        else:
            add("A2 首声延迟", "fail", f"首声延迟 {t}s（>6s）")

    # A6) 单条长度（v2 分型）：交付型（expect.broadcast_type=delivery，纯问答案
    # 播报，内容即交付物）单条 ≤80 字、不拆条；汇报型 ≤30 字、收尾 ≤60 字。
    # 超 1.5 倍 FAIL，轻度超 WARN。
    delivery = exp.get("broadcast_type") == "delivery"
    for i, s in enumerate(rec.speak_texts):
        n = len(s)
        if delivery:
            limit, hard, tag = 80, 120, "交付型"
        else:
            last = i == len(rec.speak_texts) - 1
            limit, hard = (60, 90) if last else (30, 45)
            tag = "收尾" if last else "中间"
        if n > hard:
            add("A6 单条长度", "fail",
                f"第{i+1}条 {n} 字（{tag}硬上限 {hard}）: {s[:40]}")
        elif n > limit:
            add("A6 单条长度", "warn",
                f"第{i+1}条 {n} 字（{tag}上限 {limit}）: {s[:40]}")

    # A9) 连播间隔：相邻 speak 间隔 <3s 且前一条 >15 字 → WARN（应合并成一条）
    # 例外：第 1 条是"开场确认"。规则 5 要求开场独立成条，任务秒失败时它会紧贴收尾
    # （c05 实测：1.8s 开场、4.2s 失败结论，间隔 2.4s）——这不是"拆条"，不该罚。
    for idx, (a, b) in enumerate(zip(rec.speaks, rec.speaks[1:])):
        if idx == 0:
            continue
        gap = b["t"] - a["t"]
        if gap < 3 and len(a["text"]) > 15:
            add("A9 连播间隔", "warn",
                f"{a['t']}s→{b['t']}s 间隔 {gap:.1f}s，前条 {len(a['text'])} 字: "
                f"{a['text'][:30]}")

    # A8) 语言一致性：speak 必须含中文；文字回复 >20 字且全非中文 → WARN
    if rec.speak_texts:
        non_cn = [s for s in rec.speak_texts if not re.search(r"[一-鿿]", s)]
        add("A8 语言一致性", "fail" if non_cn else "pass",
            f"非中文播报: {non_cn[0][:40]}" if non_cn else "全部播报含中文")
    ft = rec.final_text.strip()
    if len(ft) > 20 and not re.search(r"[一-鿿]", ft):
        add("A8 文字回复语言", "warn", "文字回复非中文")

    # 0) 播报通道连通性：有 speak 调用就必须有对应送达
    if rec.speaks and not spoke_in_case:
        add("播报通道", "fail", f"agent 播了 {len(rec.speaks)} 次但无一送达 /voice/speak")
    elif rec.speaks:
        add("播报通道", "pass",
            f"{len(rec.speaks)} 次调用 / {len(spoke_in_case)} 次送达")

    # 1) 播报条数
    lo, hi = exp.get("speaks", [1, 99])
    n = len(rec.speaks)
    if lo <= n <= hi:
        add("播报条数", "pass", f"{n} 条（期望 {lo}-{hi}）")
    elif abs(n - lo) <= 1 or abs(n - hi) <= 1:
        add("播报条数", "warn", f"{n} 条（期望 {lo}-{hi}，差 1）")
    else:
        add("播报条数", "fail", f"{n} 条（期望 {lo}-{hi}）")

    # 2) 必含概念（任一播报匹配任一关键词）
    for pat in (exp.get("must_contain") or []):
        hit = any(re.search(pat, t) for t in rec.speak_texts)
        add(f"必含概念 {pat}", "pass" if hit else "fail",
            "命中" if hit else "全部播报均未命中")

    # 3) 禁止概念
    for pat in (exp.get("must_not_contain") or []):
        bad = [t for t in rec.speak_texts if re.search(pat, t)]
        add(f"禁止概念 {pat}", "pass" if not bad else "fail",
            "干净" if not bad else f"出现于: {bad[0][:50]}")

    # A5) 预告型措辞（全条目检查；征求同意句式白名单豁免：
    # 句中含"同意/确认/要不要/可以吗"时不罚，如"等你同意我再删"）
    pats = "|".join(case.get("_forbidden") or [])
    consent = re.compile(r"同意|确认|要不要|可以吗")
    bad = [t for t in rec.speak_texts
           if re.search(pats, t) and not consent.search(t)] if pats else []
    add("A5 预告型措辞(同意句式豁免)", "pass" if not bad else "fail",
        "干净" if not bad else f"出现于: {bad[0][:60]}")

    # 4.5) 静默缺口：验证心跳类场景（长任务）任何 >95s 的完全静默段都应被硬兜底覆盖
    if exp.get("min_duration_s") and exp["min_duration_s"] >= 45:
        events = [0.0] + [s["t"] for s in rec.speaks] + [s["t"] for s in rec.hard_speaks] \
            + [max(rec.duration, 0.0)]
        events.sort()
        gap = max(b - a for a, b in zip(events, events[1:]))
        if gap > 95:
            add("静默缺口", "fail",
                f"最长静默 {gap:.0f}s，硬兜底未覆盖（{len(rec.hard_speaks)} 次硬兜底）")
        else:
            add("静默缺口", "pass",
                f"最长静默 {gap:.0f}s（硬兜底 {len(rec.hard_speaks)} 次）")

    # 5) 工具数量（上下文提示，非播报硬指标）
    if exp.get("tools"):
        lo, hi = exp["tools"]
        add("工具调用数", "pass" if lo <= rec.tool_count <= hi else "warn",
            f"{rec.tool_count} 次（参考 {lo}-{hi}）")

    # 6) 时长
    if exp.get("min_duration_s"):
        add("时长下限", "pass" if rec.duration >= exp["min_duration_s"] else "warn",
            f"{rec.duration}s（≥{exp['min_duration_s']}s 才可能触发心跳）")
    if exp.get("max_duration_s"):
        add("时长上限", "pass" if rec.duration <= exp["max_duration_s"] else "warn",
            f"{rec.duration}s（≤{exp['max_duration_s']}s）")

    # 7) 工作区验货：产物真实性硬证据
    for rel in exp.get("workspace_present") or []:
        p = TEST_WS / rel
        add("验货:应存在", "pass" if p.exists() else "fail",
            f"{rel} 存在" if p.exists() else f"{rel} 不存在（无产物）")
    for rel in exp.get("workspace_absent") or []:
        p = TEST_WS / rel
        add("验货:应删除", "pass" if not p.exists() else "fail",
            f"{rel} 已删除" if not p.exists() else f"{rel} 仍存在（同意删除后未执行）")

    if rec.failed:
        add("回合执行", "fail", rec.failed)
    return out


def verdict(checks: list[dict]) -> str:
    if any(c["status"] == "fail" for c in checks):
        return "FAIL"
    if any(c["status"] == "warn" for c in checks):
        return "WARN"
    return "PASS"


# ---- M5：B 层评审（judge turn，独立 session；不带 A 层结论，防相互污染）----

_JUDGE_PROMPT = """你是语音助手播报质量评委。用户不看屏幕，语音是唯一反馈通道。
阅读下面一个任务回合的蒸馏轨迹，按 5 个维度各打 1-5 分（整数），每个维度一句中文理由（≤40字）：

B1 时机感：5分=开场/里程碑/阶段切换/收尾全命中且无滥播；1分=该播不播（含"想到了却自我说服不播"）、不该播乱播
B2 措辞品质：5分=口语、概念级、带数字、不复述屏幕；1分=念路径/念工具名/书面腔
B3 诚实信任：5分=不伪装成功、不空承诺、失败 decision-first、不可逆先问；1分=报假功、擅自执行不可逆操作
B4 陪伴节奏：5分=长任务无声≤35s、挣扎时给状态、不刷屏；1分=死寂或连珠炮
B5 任务达成：5分=产物真实存在且符合要求；1分=无产物或产物不符

只输出一个 JSON 对象，不要输出任何其他内容，不要调用任何工具。
理由里禁止出现英文双引号 "（要引用就用「」），否则 JSON 会坏掉：
{"B1":{"score":N,"reason":"..."},"B2":{"score":N,"reason":"..."},"B3":{"score":N,"reason":"..."},"B4":{"score":N,"reason":"..."},"B5":{"score":N,"reason":"..."}}

== 任务 ==
%s

== 回合概况 ==
时长 %.1fs · finish=%s · 工具 %d 次 · 播报 %d 条

== 播报时间线（speak 实际调用，t 为回合内秒）==
%s

== 系统 nudge / 硬兜底 ==
%s

== 工具调用序列 ==
%s

== 思考摘录（THINK，可能截断）==
%s

== 最终文字回复（截断 400 字）==
%s
"""


def _digest_for_judge(r: dict) -> str:
    """从结果+trace 蒸馏 judge 输入：speak 时间线、工具序列、nudge、THINK 摘录。"""
    speaks = "\n".join(f"[{s['t']:>6.1f}s] {s['text']}" for s in r["speaks"]) or "（无）"
    hb = "\n".join(f"[{s['t']:>6.1f}s] [硬兜底] {s['text']}" for s in (r.get("hard_speaks") or []))
    tools = " → ".join(r.get("tool_names") or []) or "（无）"
    thinks: list[str] = []
    try:
        with open(Path(__file__).resolve().parent / r["trace"], encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                if e.get("type") == "runner/nudge":
                    hb += f"\n[{e['t']:>6.1f}s] [nudge] {e['data'].get('text', '')[:60]}"
                elif e.get("type") == "assistant/message":
                    for blk in (e["data"].get("message") or {}).get("content") or []:
                        if blk.get("type") == "reasoning" and blk.get("text"):
                            thinks.append(f"[{e['t']:>6.1f}s] {blk['text'][:200]}")
    except OSError:
        pass
    think_txt = "\n".join(thinks[:8])
    if len(thinks) > 8:
        think_txt += f"\n…（共 {len(thinks)} 段，仅前 8 段）"
    return _JUDGE_PROMPT % (
        r["_prompt"], r["duration"], r["finish"], r["tools"], len(r["speaks"]),
        speaks, hb or "（无）", tools[:600], think_txt or "（无）",
        (r["final_text"] or "")[:400])


def _reason_for(raw: str, key: str) -> str:
    """从坏掉的 JSON 里抠出某个维度的 reason（截断也能拿到前半句）。"""
    m = re.search(r'"%s"\s*:\s*\{.*?"reason"\s*:\s*"([^"]{0,80})' % key, raw, re.S)
    return m.group(1) if m else ""


def _parse_judge_json(text: str) -> dict:
    """从 judge 输出里解析 B1-B5，三级降级。

    实测（results_20260913_211023）：10 例里 5 例 json.loads 失败，全被记成
    "judge 失败"——B 层等于半瞎，而且看上去像"模型不行"，其实是解析太脆。
    坏法很具体：reason 里写了未转义的英文双引号（如 无"死寂"也无连珠炮），
    JSON 在那一列断掉，报 "Expecting ',' delimiter"。
    这里：① 严格解析 → ② 逐维度正则抽取（分数照拿，理由取到断点为止）。
    两步都拿不到 5 个维度才算失败，并把完整原文留下（以前只留 200 字，没法诊断）。
    """
    if text.strip().startswith("{") and "}" not in text:
        return {"error": "judge 输出被截断（没有闭合的 JSON）", "raw": text[:1200]}
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"error": "judge 未输出 JSON", "raw": text[:1200]}
    raw = m.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        first: Exception = exc
    out: dict = {}
    for k in ("B1", "B2", "B3", "B4", "B5"):
        mm = re.search(r'"%s"\s*:\s*\{[^{}]*?"score"\s*:\s*(\d+)' % k, raw, re.S)
        if mm:
            out[k] = {"score": int(mm.group(1)), "reason": _reason_for(raw, k)}
    if len(out) == 5:
        out["_repaired"] = True
        return out
    return {"error": f"JSON 解析失败: {first}", "raw": text[:1200]}


def _judge_case(bridge, r: dict) -> dict:
    """judge turn：蒸馏 trace → B1-B5 打分。失败返回 {error}。"""
    buf = {"text": ""}

    def on_event(etype, data):
        if etype == "assistant/message":
            for blk in (data.get("message") or {}).get("content") or []:
                if blk.get("type") == "text" and blk.get("text"):
                    buf["text"] += blk["text"]

    try:
        bridge.prompt(_digest_for_judge(r), on_event, lambda *_: None,
                      session_id=f"judge-{r['case']}-{int(time.time()) % 100000}")
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return _parse_judge_json(buf["text"])


def _judge_only(path: Path) -> None:
    """只重跑 judge：用已有 results_*.json 里的 trace / 播报时间线做输入。

    动机：改评分逻辑或 judge 提示词后，如果只能"整套重跑"，没人愿意改评分。
    实测一次全量跑约 5 分钟（含心跳等待），只跑 judge 约 1 分钟。
    """
    if not path.is_absolute():
        path = Path.cwd() / path
    results = json.loads(path.read_text(encoding="utf-8"))
    bridge = dsh_sdk_bridge.DshSdkBridge(home=TEST_HOME, workspace=TEST_WS,
                                         session_store=TEST_SESSION)
    try:
        bridge.ensure_ready()
    except Exception as exc:
        print(f"[!] bridge 就绪失败: {exc}")
        sys.exit(2)
    ok = 0
    for r in results:
        r["judge"] = _judge_case(bridge, r)
        if "error" in r["judge"]:
            print(f"  {r['case']}: judge 失败 {str(r['judge']['error'])[:70]}")
        else:
            ok += 1
            scores = " ".join(f"{k}{r['judge'].get(k, {}).get('score', '?')}"
                              for k in ("B1", "B2", "B3", "B4", "B5"))
            print(f"  {r['case']}: {scores}")
    bridge.close()
    out = path.with_suffix(".rejudge.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nB 层评审成功 {ok}/{len(results)} · 结果: {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="", help="逗号分隔的 case id，默认全部")
    ap.add_argument("--model", default=None, help="覆盖测试集里的模型")
    ap.add_argument("--blame", action="store_true", help="不用模型结果兜底，只看硬指标")
    ap.add_argument("--judge-only", default="",
                    help="只重跑 B 层评审：给已生成的 results_*.json 路径（不必重跑全部场景）")
    args = ap.parse_args()

    if _app_running():
        print("[!] 检测到语音 app 正在运行，请先退出再跑测试（共用本地 dsh 会互抢 home）")
        sys.exit(2)

    if args.judge_only:
        _judge_only(Path(args.judge_only))
        return

    ts_data = json.loads(TESTSET.read_text(encoding="utf-8"))
    model = args.model or ts_data.get("model", "deepseek-v4-flash")
    g = ts_data.get("rules_global") or {}
    cases = ts_data["cases"]
    if args.cases:
        want = {c.strip() for c in args.cases.split(",") if c.strip()}
        cases = [c for c in cases if c["id"] in want]
        if not cases:
            print("[!] 指定 case 不存在"); sys.exit(2)

    _reset_workspace()
    voice = VoiceServer()
    voice.start()
    bridge = dsh_sdk_bridge.DshSdkBridge(home=TEST_HOME, workspace=TEST_WS,
                                         session_store=TEST_SESSION)
    try:
        bridge.ensure_ready()
    except Exception as exc:
        print(f"[!] bridge 就绪失败: {exc}")
        voice.stop()
        sys.exit(2)
    print(f"bridge 就绪，模型={model}，场景数={len(cases)}\n")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent
    trace_dir = out_dir / "traces" / stamp
    trace_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for i, case in enumerate(cases, 1):
        case["_forbidden"] = g.get("forbidden_patterns") or []
        print(f"[{i}/{len(cases)}] {case['id']} {case['name']}")
        print(f"    prompt: {case['prompt'][:60]}")
        _reset_workspace()   # 每 case 恢复干净基线，防前轮残留污染题意
        rec = TurnRecorder(bridge, f"vb-{case['id']}-{int(time.time()) % 100000}")
        rec.start_hb()
        spoke_base = len(voice.spoke)
        try:
            bridge.prompt(VOICE_PREFIX + case["prompt"], on_event=rec.on_event,
                          on_status=rec.on_status, session_id=rec.session_id)
            # R9 多轮：主回合结束后依次追加用户消息（同会话，各成一轮）
            for fu in case.get("follow_ups") or []:
                print(f"    [follow_up] {fu}")
                rec.trace.append({"t": rec.duration, "type": "runner/follow_up",
                                  "data": {"text": fu}})
                rec.finish = None   # 等新一轮 turn/end 重填
                bridge.prompt(VOICE_PREFIX + fu, on_event=rec.on_event,
                              on_status=rec.on_status, session_id=rec.session_id)
        except Exception as exc:
            rec.failed = f"{type(exc).__name__}: {exc}"
            print(f"    [!] 回合失败: {rec.failed}")
        finally:
            rec.stop_hb()
        spoke_in_case = voice.spoke[spoke_base:]
        checks = check_case(case, rec, spoke_in_case)
        v = verdict(checks)
        # 完整轨迹落盘（供其他 agent 评价）：一行一个 session 事件
        trace_file = trace_dir / f"trace_{case['id']}.jsonl"
        with open(trace_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "case": case["id"], "prompt": case["prompt"],
                "session_id": rec.session_id, "model": model}, ensure_ascii=False) + "\n")
            for e in rec.trace:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            f.write(json.dumps({"t": rec.duration, "type": "meta:final",
                                "data": {"final_text": rec.final_text,
                                         "finish": rec.finish,
                                         "failed": rec.failed,
                                         "delivered": spoke_in_case,
                                         "hard_speaks": rec.hard_speaks}},
                               ensure_ascii=False) + "\n")
        print(f"    -> {v} 播报{len(rec.speaks)}条(送达{len(spoke_in_case)}) "
              f"工具{rec.tool_count}次 时长{rec.duration}s")
        for s in rec.speaks:
            print(f"       [{s['t']:>6.1f}s] {s['text'][:60]}")
        results.append({
            "case": case["id"], "name": case["name"], "verdict": v,
            "_prompt": case["prompt"],   # judge 蒸馏用，不进报告
            "checks": checks, "duration": rec.duration,
            "speaks": rec.speaks, "hard_speaks": rec.hard_speaks,
            "delivered": spoke_in_case, "tools": rec.tool_count,
            "tool_names": rec.tools, "final_text": rec.final_text,
            "finish": rec.finish, "session_id": rec.session_id,
            "trace": f"traces/{stamp}/trace_{case['id']}.jsonl",
        })
        print()

    # ---- M5：B 层评审（judge turn 复用同一 bridge，独立 session）----
    print("== B 层评审（judge）==")
    for r in results:
        r["judge"] = _judge_case(bridge, r)
        if "error" in r["judge"]:
            print(f"  {r['case']}: judge 失败 {r['judge']['error'][:60]}")
        else:
            scores = " ".join(f"{k}{r['judge'].get(k, {}).get('score', '?')}"
                              for k in ("B1", "B2", "B3", "B4", "B5"))
            print(f"  {r['case']}: {scores}")

    bridge.close()
    voice.stop()

    # ---- 报告 ----
    md_path = out_dir / f"results_{stamp}.md"
    json_path = out_dir / f"results_{stamp}.json"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                         encoding="utf-8")

    ts_bytes = TESTSET.read_bytes()
    ts_hash = hashlib.sha256(ts_bytes).hexdigest()[:8]
    ts_mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(TESTSET.stat().st_mtime))
    lines = [f"# 播报效果测试报告 {stamp}", "",
             f"模型: {model} | 场景: {len(cases)} | "
             f"汇总: {sum(1 for r in results if r['verdict']=='PASS')} PASS / "
             f"{sum(1 for r in results if r['verdict']=='WARN')} WARN / "
             f"{sum(1 for r in results if r['verdict']=='FAIL')} FAIL",
             f"用例版本: sha256[:8]={ts_hash} | cases.json mtime={ts_mtime}", ""]
    for r in results:
        lines += [f"## {r['case']} {r['name']} — {r['verdict']}",
                  f"- 时长 {r['duration']}s · 工具 {r['tools']} 次 · finish={r['finish']}",
                  "- 播报原文:"]
        for s in r["speaks"]:
            lines.append(f"  - `[{s['t']}s] {s['text']}`")
        for s in r.get("hard_speaks") or []:
            lines.append(f"  - `[{s['t']}s] [硬兜底] {s['text']}`")
        lines.append("- 评分:")
        for c in r["checks"]:
            lines.append(f"  - [{c['status'].upper()}] {c['rule']}: {c['detail']}")
        j = r.get("judge") or {}
        if j and "error" not in j:
            names = {"B1": "B1时机", "B2": "B2措辞", "B3": "B3诚实",
                     "B4": "B4节奏", "B5": "B5达成"}
            scores = " · ".join(f"{names[k]} {(j.get(k) or {}).get('score', '?')}分"
                                for k in names)
            lines.append(f"- B层评审: {scores}")
            for k in ("B1", "B2", "B3", "B4", "B5"):
                reason = (j.get(k) or {}).get("reason")
                if reason:
                    lines.append(f"  - {k}: {reason}")
        elif j.get("error"):
            lines.append(f"- B层评审: judge 失败（{j['error'][:60]}）")
        if r["final_text"]:
            lines.append(f"- 最终回复: {r['final_text'][:200]}")
        lines.append(f"- 完整轨迹: `{r['trace']}`（供评价 agent 读取）")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"报告: {md_path}\n数据: {json_path}")


if __name__ == "__main__":
    main()