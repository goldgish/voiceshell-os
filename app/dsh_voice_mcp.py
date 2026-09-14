# -*- coding: utf-8 -*-
"""dsh-voice MCP 服务器：秘书会话的工具面。

三种形态：
1) 播报—— speak 工具 → 本地 app 的 TTS 引擎出声（POST /voice/speak，端口经
   voice_http.port 文件发现）。
2) 代签—— 审批相关工具 → DSH Web 实例里插件的 /voice/hitl/* 路由。插件在
   approval/request 链首「认领」了审批，所以只有它能定案；这里是秘书伸过去的手。
3) 传话—— 会话导航工具 → 插件的 /voice/sessions* 路由：列清单、开新会话、切会话、
   把用户的话转达给某个会话。秘书是唯一前台，干活都在被切到的那个会话里发生。

由 dsh 运行时（voice_runtime.patch.yml / cordis.patch.yml 的 mcp-voice 行）以 stdio 拉起。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

APP_DIR = Path(__file__).resolve().parent
PORT_FILE = APP_DIR / "voice_http.port"
# DSH Web 实例端口：插件在那里接管审批。可用 VOICE_HOST_PORT 覆盖。
HOST_PORT = os.environ.get("VOICE_HOST_PORT", "4177")

mcp = FastMCP("dsh-voice")


def _app_url() -> str | None:
    try:
        port = PORT_FILE.read_text(encoding="utf-8").strip()
        return f"http://127.0.0.1:{port}/voice/speak" if port else None
    except OSError:
        return None


@mcp.tool()
def speak(text: str) -> str:
    """向用户播报一条简短的中文口语消息（用户通常不看屏幕，播报是唯一的主反馈通道）。

    使用规则：
    - 必须在这几类时刻调用：任务开场做一句理解确认（纯闲聊式问答可省）；命中里程碑（产出可交付文件、
      查完一个信息维度、阶段切换、失败后换源成功、关键结论或数字首次确认）；任务收尾。
    - 收尾播报是标配：答案必须亲口说一遍，不可只写进文字回复（用户不看屏幕）。
      失败/受阻时，失败结论同样必须用 speak 播报，不许只在文字里说明。
    - 连续60秒没出声时，系统会发提醒，收到立刻播报一句"在岗进度"（完成了什么、正在产出什么）。
    - 配额下限：跨3次以上工具调用的任务至少播1次中间进度，跨6次以上至少播2次。
      不许为了省事跳过里程碑；实在无里程碑可用时，用一句"在岗进度"满足下限。
    - 遇到问题需要用户注意或决策（先说卡在哪、需要用户做什么）。
    - 内容：概念级、口语化、不超过30字，收尾可稍长一两句。用"报告写好了"，不说"已写入 report.md"。
    - 不为每个工具调用播报，不复述屏幕上已经展示的内容；非里程碑不播，同一状态不重复播。
    - 播报句内不许出现"我再""我还要""接下来我"这类衔接下一步的词，说到已完成事实为止；
      征求同意的句子除外（如"等你同意我再删"）。
    - 过程叙述只走本工具；文字回复里只写最终交付详情，不写"我先…我再…"式过程旁白。
    - 单文件预计超 150 行时，先写骨架再用 edit 分段补全，不许一次性写完。
    """
    url = _app_url()
    if not url:
        return "错误：语音服务未启动（找不到 voice_http.port）"
    try:
        body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode("utf-8", "replace")[:200] or "已播报"
    except Exception as exc:
        return f"错误：语音播报请求失败 {exc}"


def _host_call(path: str, payload: dict | None = None, timeout: float = 5) -> dict:
    """调 DSH Web 实例里插件的路由；失败一律返回 {"ok": False, "error": ...}。"""
    url = f"http://127.0.0.1:{HOST_PORT}{path}"
    try:
        if payload is None:
            req = urllib.request.Request(url)
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def list_pending_approvals() -> str:
    """查看当前有哪些操作正等着用户点头（待决审批）。

    用户说「允许」「同意」「拒绝」「不行」这类话时，先调本工具确认有没有待决操作。
    返回「当前没有待决审批」时不要凭空调 decide_approval。
    """
    data = _host_call("/voice/hitl/pending")
    items = data.get("pending")
    if not isinstance(items, list):
        return f"查询待决审批失败：{data.get('error', '未知错误')}"
    if not items:
        return "当前没有待决审批。"
    return "待决审批：" + "；".join(
        f"{i.get('toolName', '未知操作')}（id={i.get('id')}）" for i in items)


@mcp.tool()
def decide_approval(decision: str, approval_id: str = "") -> str:
    """替用户对那个待确认的操作签字——这是纯语音场景里唯一的「手」。

    decision 只接受两个值：
    - "allow"：用户明确同意执行（听到「允许」「同意」「可以」）。
    - 其余任何取值都按拒绝处理（「拒绝」「不行」「算了」，或你拿不准时）。
      默认不授权是刻意的：听不清就不签，用户还可以在屏幕上点。
    approval_id 留空即作用于最近一条待决审批；同一条审批只能签一次。
    """
    data = _host_call(
        "/voice/hitl/decide",
        {"decision": decision, **({"id": approval_id} if approval_id else {})},
    )
    if not data.get("ok"):
        return f"签字失败：{data.get('error', '未知错误')}"
    verb = "已允许" if data.get("decision") == "allow" else "已拒绝"
    return f"{verb}：{data.get('toolName', '该操作')}。"


@mcp.tool()
def list_sessions() -> str:
    """列出当前可以切换的对话，以及哪一个是「当前对话」。

    用户问「有哪些对话」「刚才那个写到哪了」，或说「切到写周报那个对话」时先调本工具。
    返回里的 id 才是后续 switch_session / send_to_session 能用的；标题是给人看的。
    带「正在忙」的才是真在一轮里跑——stop_session 前先看这个标记，别靠猜。
    凡有副作用的动作（停、删、覆盖、转达）都先在这里解析出明确 id，再带 id 调用。
    """
    data = _host_call("/voice/sessions")
    items = data.get("items")
    if not isinstance(items, list):
        return f"查询对话失败：{data.get('error', '未知错误')}"
    active = data.get("active") or "（还没指定）"
    if not items:
        return f"当前没有别的对话。当前对话：{active}。"
    detail = "；".join(
        f"{i.get('title', '未命名')}（id={i.get('id')}{'，正在忙' if i.get('running') else ''}）"
        for i in items)
    return f"当前对话：{active}。可切换的对话：{detail}"


@mcp.tool()
def new_session() -> str:
    """开一个新对话，并把它设为当前对话。

    用户说「新开一个对话」「新建会话」「换个话题重来」时调本工具；
    开好之后再用 send_to_session（或直接等用户下一句）把要求发过去。
    返回里会说明落在哪个工作区：会话是挂在那个工作区上的，跨工作区转达会被拒——
    真被拒了不要硬试，按 send_to_session 给的下一步做。
    """
    data = _host_call("/voice/sessions/new", {})
    if not data.get("ok"):
        return f"新建对话失败：{data.get('error', '未知错误')}"
    where = data.get("workspace")
    place = f"工作区「{where}」里" if where else "默认目录下"
    return f"新对话已开好（在{place}），并已设为当前对话。"


@mcp.tool()
def switch_session(session_id: str) -> str:
    """把「当前对话」切换成指定的那一个。

    session_id 必须来自 list_sessions 的返回，不能自己编。
    用户是口语描述（「写周报那个」）时，先 list_sessions 对照标题挑；
    挑不出来（标题都不像）就先问一句，不要瞎猜一个 id。
    """
    data = _host_call("/voice/sessions/switch", {"sessionId": session_id})
    if not data.get("ok"):
        return f"切换对话失败：{data.get('error', '未知错误')}"
    return f"已切到：{data.get('title', session_id)}。"


@mcp.tool()
def send_to_session(text: str, session_id: str = "") -> str:
    """把用户的要求转达给某个对话去做——这是你「传话」的正式通道。

    text 用用户的原话，不要自己改写成你以为更清楚的需求；缺失的信息可以在转达后追问。
    session_id 留空即发给「当前对话」；没有当前对话时会让你先 new_session 或 list_sessions。
    转达成功后不要自己再动手做这件事；具体进展由那个对话自己播报，
    你只需用 speak 回用户一句「已经转达了」。
    转达失败时不要说一句「失败了」就完事：把「通道为什么不通 / 产物最后落在哪 /
    这条需求有没有真的进执行会话」三件事对用户说清；失败信息里给了下一步就照着做。
    """
    payload: dict = {"text": text}
    if session_id:
        payload["sessionId"] = session_id
    data = _host_call("/voice/sessions/send", payload, timeout=8)
    if not data.get("ok"):
        error = data.get("error")
        if error == "no-active-session":
            return "当前还没有指定对话。先调 new_session 开一个，或调 list_sessions 挑一个切过去。"
        if error == "workspace-mismatch":
            return (
                f"转达失败：该对话属于工作区「{data.get('belongsTo', '未知')}」，"
                f"而秘书所在的工作区是「{data.get('expected', '未知')}」，跨工作区不能直接转达。"
                "下一步二选一：① 用 new_session 在秘书的工作区新开一个对话，再把用户原话发过去；"
                "② 若这活必须在该对话所在目录做，就直接告诉用户「这条需求没有进入那个对话」，"
                "不要默默自己把活干完、也不要假装已经转达。"
            )
        return f"转达失败：{error or '未知错误'}"
    title = data.get("title")
    where = f"「{title}」" if title else ""
    return f"已转达给对话 {data.get('sessionId')}{where}。"


@mcp.tool()
def stop_session(session_id: str = "") -> str:
    """叫停某个对话正在跑的那一轮。

    用户说「停」「别做了」「先停下」时调本工具。停的是**别的对话**在干的活，
    不是让你自己少说话——停完用 speak 回一句就收。
    session_id 留空即停「当前对话」；不确定停哪个时先 list_sessions，带明确 id 调用，
    不要依赖「当前对话」这个会被导航改写的指针。
    两种结果必须区别对待：回「已经让它停了」才是真停了；
    回「当前没有在跑的一轮」表示什么都没停，只能照实播报，不许说成「已停」。
    """
    payload: dict = {}
    if session_id:
        payload["sessionId"] = session_id
    data = _host_call("/voice/sessions/stop", payload, timeout=10)
    if not data.get("ok"):
        error = data.get("error")
        if error == "no-active-session":
            return "当前没有指定对话。先 list_sessions 看看有哪些在跑，再指定一个。"
        if error == "not-running":
            name = f"「{data['title']}」" if data.get("title") else "那个对话"
            return (
                f"{name}当前没有正在跑的一轮（本次没有执行任何叫停）。"
                "对用户只能说成「没有在跑的一轮」，不要说「已停」。"
            )
        return f"叫停失败：{error or '未知错误'}"
    name = f"「{data['title']}」" if data.get("title") else "该对话"
    return f"已停掉{name}正在跑的那一轮（此前排队的活不受影响，不是「全停了」）。"


@mcp.tool()
def read_session(session_id: str = "") -> str:
    """读某个对话最近聊的内容，供你用自己的话讲给用户听。

    用户问「刚才那个对话聊了什么」「写周报那个进行到哪了」「总结一下那个对话」时调本工具。
    拿到的是原始消息（用户的原话、对方的回复），你要**概括**成结论后再用 speak 播报，
    不要照念原文，不要念代码、文件路径和长列表。
    session_id 留空即读「当前对话」。
    """
    payload: dict = {}
    if session_id:
        payload["sessionId"] = session_id
    data = _host_call("/voice/sessions/read", payload, timeout=10)
    if not data.get("ok"):
        if data.get("error") == "no-active-session":
            return "当前没有指定对话。先 list_sessions 挑一个，或 new_session 开一个。"
        return f"读取对话失败：{data.get('error', '未知错误')}"
    lines = data.get("lines") or []
    title = data.get("title", "未命名")
    if not lines:
        return f"对话「{title}」还没有聊出实质内容。"
    body = "\n".join(
        f"{'用户' if line.get('role') == 'user' else '助手'}：{line.get('text', '')}"
        for line in lines)
    return f"对话「{title}」最近的内容（请概括后用你自己的话讲给用户听）：\n{body}"


if __name__ == "__main__":
    mcp.run(transport="stdio")