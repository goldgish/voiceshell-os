# -*- coding: utf-8 -*-
"""初始化向导：把这份仓库接到本机的 DSH 上，之后双击「启动.bat」就能用。

做四件事：
  1) 体检：Node / Python / DSH 运行时在不在
  2) 装 Python 依赖（可以跳过，稍后自己 pip install -r app/requirements.txt）
  3) 收你的智谱 GLM API Key，写进 app/.env
     —— 只用来做「语音转文字」。播报走微软 Edge-TTS，免费且不需要任何 key。
  4) 把插件挂进 DSH 的 web profile：
     - 写 profiles/web/cordis.patch.yml：把 app/dsh_voice_mcp.py 作为 voice MCP 服务器挂上
     - 写 profiles/web/package.json：link 到本仓库的 dsh-voice-input 并加进 bundles
     - 在该目录 pnpm install

可以重复跑：已有配置会被认出来，不会重复添加，改动前会留 .bak-setup 备份。

用法：双击根目录的 setup.bat（等价于 python setup.py）
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "app"
PLUGIN_DIR = ROOT / "dsh-voice-input"
DSH_HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
WEB_PROFILE = DSH_HOME / "profiles" / "web"
DSH_BIN = DSH_HOME / "profiles" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
GLM_URL = "https://open.bigmodel.cn/usercenter/apikeys"

#: 依赖探针：import 名 → pip 包名（报错时告诉用户装什么）
DEPS = {
    "webview": "pywebview",
    "pystray": "pystray",
    "PIL": "pillow",
    "pygame": "pygame-ce",
    "edge_tts": "edge-tts",
    "zai": "zai-sdk",
    "mcp": "mcp",
    "winrt.windows.devices.bluetooth": "winrt-runtime 等一组 winrt-* 包",
}

TOTAL = 4


def _utf8_stdout() -> None:
    """控制台按 UTF-8 输出（setup.bat 已 chcp 65001，这里再兜一层）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def hr() -> None:
    print("-" * 64)


def step(n: int, title: str) -> None:
    print()
    hr()
    print(f"  [{n}/{TOTAL}] {title}")
    hr()


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def warn(msg: str) -> None:
    print(f"  [注意] {msg}")


def bad(msg: str) -> None:
    print(f"  [缺]   {msg}")


def ask(question: str, default: str = "") -> str:
    tip = f"（回车 = {default}）" if default else ""
    try:
        got = input(f"  ? {question}{tip}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(1)
    return got or default


def ask_yes(question: str, default: bool = True) -> bool:
    tip = "Y/n" if default else "y/N"
    try:
        got = input(f"  ? {question} [{tip}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(1)
    if not got:
        return default
    return got in ("y", "yes", "是", "好", "行", "1")


def mask(key: str) -> str:
    return key[:8] + "..." + key[-4:] if len(key) > 14 else "***"


# ---------------- 1. 体检 ----------------

def check_python() -> None:
    v = sys.version_info
    if v < (3, 11):
        bad(f"Python {v.major}.{v.minor} 太旧，需要 3.11+（当前解释器 {sys.executable}）")
        raise SystemExit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro}   {sys.executable}")


def check_node() -> None:
    exe = shutil.which("node")
    if not exe:
        bad("Node.js 没找到（DSH 靠它跑）。装一个：https://nodejs.org/")
        return
    try:
        ver = subprocess.run([exe, "-v"], capture_output=True, text=True,
                             timeout=15).stdout.strip()
    except Exception:
        ver = "?"
    ok(f"Node.js {ver}   {exe}")


def check_dsh() -> None:
    if DSH_BIN.exists():
        ok(f"DSH 运行时   {DSH_BIN}")
        return
    bad(f"DSH 运行时没找到：{DSH_BIN}")
    print("         装法：pip install deepseek-harness-runtime-bin")
    print("         装完先跑一次 dsh，让它把运行时铺到 ~/.dsh，再回来跑本向导。")


# ---------------- 2. 依赖 ----------------

def missing_deps() -> list[str]:
    """按 import 名探测，缺的收集起来——比读 pip 冻结列表准，且不挑解释器版本。"""
    out: list[str] = []
    for mod in DEPS:
        try:
            if importlib.util.find_spec(mod) is None:
                out.append(mod)
        except (ImportError, ValueError):
            out.append(mod)
    return out


def install_deps() -> None:
    missing = missing_deps()
    if not missing:
        ok("Python 依赖齐了")
        return
    warn("缺依赖：" + "、".join(DEPS[m] for m in missing))
    req = APP_DIR / "requirements.txt"
    if not ask_yes("现在装吗？（会跑 pip install -r app/requirements.txt）"):
        warn("跳过。装完再回来跑一次本向导即可。")
        return
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req)]
    print(f"  $ {' '.join(cmd)}")
    if subprocess.call(cmd) == 0:
        left = missing_deps()
        ok("依赖装好了") if not left else warn("还缺：" + "、".join(DEPS[m] for m in left))
    else:
        bad("pip 失败了，看上面的报错。也可以手动：")
        print(f"         {sys.executable} -m pip install -r app/requirements.txt")


def record_python() -> None:
    """记下装了依赖的解释器，启动器优先用它，免得机器上多个 Python 时挑错。"""
    try:
        (APP_DIR / ".python").write_text(sys.executable, encoding="utf-8")
    except OSError:
        pass


# ---------------- 3. GLM key ----------------

def read_env_key() -> str:
    env = APP_DIR / ".env"
    if not env.exists():
        return ""
    try:
        lines = env.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if line.startswith("ZHIPU_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def setup_env() -> None:
    step(3, "填「语音转文字」的 key")
    print("  播报（TTS）用微软 Edge-TTS：免费、免登录、不用填任何 key。")
    print("  只有「语音转文字」需要 key，推荐智谱 GLM-ASR：")
    print(f"    申请地址  {GLM_URL}")
    print("    新账号带免费额度，glm-asr 按音频秒数计费，日常用花不了几毛钱。")
    print()
    cur = read_env_key()
    if cur:
        ok(f"app/.env 已配置：{mask(cur)}")
        if not ask_yes("要换成新的吗？", default=False):
            return
    while True:
        key = ask("把你的 ZHIPU_API_KEY 粘进来")
        if not key:
            if cur:
                warn("没填，保留原来的。")
            else:
                warn("没填。可以稍后手改 app/.env，或重跑本向导。")
                print("         注意：不填 key 的话，语音转文字会失败，只有播报能用。")
            return
        if len(key) < 20:
            warn(f"「{mask(key)}」看着不太像 key（长度 {len(key)}，正常 30 位以上）。")
            if not ask_yes("确定就用它？", default=False):
                continue
        (APP_DIR / ".env").write_text(
            "# 由 setup.py 生成。\n"
            "# 语音转文字（ASR）：智谱 GLM-ASR，申请地址 " + GLM_URL + "\n"
            f"ZHIPU_API_KEY={key}\n"
            "\n# 语音播报（TTS）走微软 Edge-TTS，不需要 key。\n",
            encoding="utf-8",
        )
        ok(f"已写入 {APP_DIR / '.env'}")
        return


# ---------------- 4. web profile ----------------

PATCH_HEADER = """\
# 由仓库根目录的 setup.py 生成，每次跑 setup 都会重写（手改请改 setup.py）。
#
# mcp-voice：把 app/dsh_voice_mcp.py 作为 voice MCP 服务器挂进 web profile。
# 秘书会话据此拿到 speak（播报）/ 会话导航 / 审批签字这一组工具。
# 注：@local/dsh-voice-input 由 package.json 的 dsh.profile.bundles 加载，
# 这里不能再 insert 它，否则报 duplicate loader entry id: voice-input。
"""


def backup(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.copy2(path, path.with_name(path.name + ".bak-setup"))
    except OSError:
        pass


def write_patch() -> None:
    py = sys.executable.replace("\\", "/")
    script = str(APP_DIR / "dsh_voice_mcp.py").replace("\\", "/")
    body = (
        PATCH_HEADER
        + "- insert:\n"
        + "    - id: mcp-voice\n"
        + "      name: '@deepseek-ai/dsh-mcp-client'\n"
        + "      config:\n"
        + "        serverName: voice\n"
        + "        transport: stdio\n"
        + f"        command: '{py}'\n"
        + "        args:\n"
        + f"          - '{script}'\n"
    )
    dst = WEB_PROFILE / "cordis.patch.yml"
    backup(dst)
    dst.write_text(body, encoding="utf-8")


def write_package_json() -> bool:
    """把插件 link 进 profile 并加进 bundles。返回是否有实质变化。

    读改写而不是覆盖：用户可能还有别的插件挂在同一个 profile 上，不能被冲掉。
    """
    dst = WEB_PROFILE / "package.json"
    data: dict = {}
    if dst.exists():
        try:
            loaded = json.loads(dst.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            warn("package.json 读不动（格式坏了？），这次按新建处理，原件已备份")
    data.setdefault("name", "dsh-profile-web")
    data.setdefault("private", True)

    link = "link:" + str(PLUGIN_DIR).replace("\\", "/")
    deps = data.get("dependencies")
    if not isinstance(deps, dict):
        deps = {}
    changed = deps.get("@local/dsh-voice-input") != link
    deps["@local/dsh-voice-input"] = link
    data["dependencies"] = deps

    dsh = data.get("dsh")
    if not isinstance(dsh, dict):
        dsh = {}
    profile = dsh.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    bundles = profile.get("bundles")
    if not isinstance(bundles, list):
        bundles = ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]
    if "@local/dsh-voice-input" not in bundles:
        bundles.append("@local/dsh-voice-input")
        changed = True
    profile["bundles"] = bundles
    profile.setdefault("patchReload", "live")
    dsh["profile"] = profile
    data["dsh"] = dsh

    backup(dst)
    dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


PNPM_CMD = "pnpm install --no-frozen-lockfile"


def pnpm_install() -> None:
    """装 profile 依赖。

    必须带 --no-frozen-lockfile：profile 的 pnpm-lock.yaml 是 DSH 铺的，里面没有
    本插件，照着旧 lockfile 严格装会报 ERR_PNPM_OUTDATED_LOCKFILE（CI 环境下 pnpm
    默认就是 frozen）。放开它，让 pnpm 顺手把 lockfile 补上。
    """
    if not shutil.which("pnpm"):
        warn("没找到 pnpm，DSH 装依赖要用它。先 npm i -g pnpm，再手动跑：")
        print(f"         cd /d \"{WEB_PROFILE}\"")
        print(f"         {PNPM_CMD}")
        return
    print(f"  $ {PNPM_CMD}   （在 {WEB_PROFILE}）")
    if subprocess.call(PNPM_CMD, cwd=str(WEB_PROFILE), shell=True) == 0:
        ok("profile 依赖已就绪")
    else:
        bad("pnpm install 失败了，手动重试：")
        print(f"         cd /d \"{WEB_PROFILE}\"")
        print(f"         {PNPM_CMD}")


# ---------------- main ----------------

def main() -> int:
    _utf8_stdout()
    print()
    print("  语音秘书 · 初始化向导")
    print("  把这份仓库接到你本机的 DSH 上。可以重复跑，不会重复添加配置。")

    step(1, "体检")
    check_python()
    check_node()
    check_dsh()
    if not APP_DIR.exists() or not PLUGIN_DIR.exists():
        bad(f"仓库不完整：找不到 {APP_DIR} 或 {PLUGIN_DIR}")
        return 1
    ok("仓库文件齐全")

    step(2, "Python 依赖")
    install_deps()
    record_python()

    setup_env()

    step(4, "把插件挂进 DSH 的 web profile")
    if not WEB_PROFILE.exists():
        bad(f"没找到 {WEB_PROFILE}")
        print("         先装好 DSH 并跑一次 dsh（它会铺出 profiles/web），再回来跑本向导。")
        return 1
    write_patch()
    ok(f"已写 {WEB_PROFILE / 'cordis.patch.yml'}（voice MCP 服务器）")
    changed = write_package_json()
    ok(f"已写 {WEB_PROFILE / 'package.json'}（link → {PLUGIN_DIR}）")
    linked = WEB_PROFILE / "node_modules" / "@local" / "dsh-voice-input"
    if changed or not linked.exists():
        pnpm_install()
    else:
        ok("profile 依赖已就绪，跳过 pnpm install")

    print()
    hr()
    print("  装好了，接下来")
    hr()
    print("  1) 双击本目录的「启动.bat」——它会起 DSH Web 实例和语音运行时，")
    print("     在桌面放一个「语音输入」快捷方式，并自动打开页面。")
    print("  2) 遥控器（小米 RC001-MS）跟电脑蓝牙配对一次，之后按住语音键说话，")
    print("     松手就把话发给秘书。")
    print("  3) 页面上 sidebar 底部会出现「语音在线」绿灯；绿了就是通了。")
    print()
    print("  改配置：重跑 setup.bat（app/.env 也可以直接手改）。")
    print("  停止：双击「停止.bat」。")
    print()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 向导不该甩栈给用户看
        print(f"\n  [错] 向导中断：{exc}")
        raise SystemExit(1)
