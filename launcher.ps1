#requires -Version 5.1
<#
语音秘书 · 一键启动 / 停止

双击「启动.bat」= 拉起整套组件：
  1) 扫掉孤儿 link.py（父进程已死的残留连接层，会和新实例抢 BLE）
  2) 拉起 DSH Web 实例（4177）：秘书会话 + voice-input 插件住在那儿
  3) 拉起 app.py（遥控器 → ASR → 投给秘书的 /voice/input；TTS 由秘书经 MCP speak 回调）
  4) 补齐桌面快捷方式（指向本启动器）
  5) 打开页面——token 只在实例 stdout 里，不带就是 401，所以必须从日志抓

双击「停止.bat」= 停 app.py / link.py / Web 实例。

注意：本文件含中文，必须存为「UTF-8 带 BOM」，否则 Windows PowerShell 5.1 读成乱码。
#>
[CmdletBinding()]
param([switch]$Stop)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

# ---- 路径（全部按当前用户推导，不写死用户名）----
$Root      = $PSScriptRoot
$AppDir    = Join-Path $Root 'app'
$AppScript = Join-Path $AppDir 'app.py'
$LinkScript= Join-Path $AppDir 'link.py'
$WebOut    = Join-Path $AppDir 'web_stdout.log'
$WebErr    = Join-Path $AppDir 'web_stderr.log'
$Shortcut  = Join-Path ([Environment]::GetFolderPath('Desktop')) '语音输入.lnk'
$Launcher  = Join-Path $Root '启动.bat'
$Port      = 4177

# Python：setup.bat 会把装了依赖的解释器路径写进 app/.python，优先用它；
# 没有就按 PATH 找，再退到常见安装位置——机器上几个 Python 并存时别挑错那个。
$Python = $null
if ($env:VOICE_PYTHON -and (Test-Path $env:VOICE_PYTHON)) {
  $Python = $env:VOICE_PYTHON
} elseif (Test-Path (Join-Path $AppDir '.python')) {
  $stamp = Get-Content (Join-Path $AppDir '.python') -Raw -ErrorAction SilentlyContinue
  if ($stamp) { $stamp = $stamp.Trim() }
  if ($stamp -and (Test-Path $stamp)) { $Python = $stamp }
}
if (-not $Python) {
  foreach ($n in @('python.exe', 'python3.exe')) {
    $c = Get-Command $n -ErrorAction SilentlyContinue
    # 跳过 Microsoft Store 的占位 exe，它只会弹应用商店
    if ($c -and $c.Source -notlike '*WindowsApps*') { $Python = $c.Source; break }
  }
}
if (-not $Python) {
  $hit = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter 'python.exe' `
                       -Recurse -Depth 1 -ErrorAction SilentlyContinue |
         Sort-Object FullName -Descending | Select-Object -First 1
  if ($hit) { $Python = $hit.FullName }
}
$PythonW = if ($Python) { Join-Path (Split-Path $Python) 'pythonw.exe' } else { $null }
if ($PythonW -and -not (Test-Path $PythonW)) { $PythonW = $Python }

# DSH 运行时入口。用 node 直接跑 bin.js，不走 dsh.exe——那个 SEA 单文件快照
# 缺 dsh-session-title-llm，dsh web 起不来。
$DshBin = if ($env:VOICE_DSH_BIN) { $env:VOICE_DSH_BIN } `
          else { Join-Path $env:USERPROFILE '.dsh\profiles\node_modules\@deepseek-ai\dsh\lib\bin.js' }

# 匹配进程命令行用（路径里有中文和反斜杠，必须转义成字面量）
$AppRe  = [regex]::Escape($AppScript)
$LinkRe = [regex]::Escape($LinkScript)
$UrlRe  = 'http://127\.0\.0\.1:4177/\?token=[A-Za-z0-9_\-]+'

function Head($m) { Write-Host "`n[*] $m" -ForegroundColor Cyan }
function Say($m, $c = 'Gray') { Write-Host "    $m" -ForegroundColor $c }

function Get-Py {
  Get-CimInstance Win32_Process -Filter "name='python.exe' or name='pythonw.exe'" -ErrorAction SilentlyContinue
}
function Test-Listening {
  [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}
function Test-Url($u) {
  try { (Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { $false }
}
function Read-Token([string]$file) {
  # 从实例 stdout 里抓带 token 的 URL；日志被覆盖/过期时自然抓不到
  if (-not (Test-Path $file)) { return $null }
  $txt = Get-Content $file -Raw -ErrorAction SilentlyContinue
  if ($txt -match $UrlRe) { return $Matches[0] }
  return $null
}
function Stop-Web {
  # 只认「dsh bin.js + --profile web」这一对特征：cmd 外壳和 node 子进程都命中，
  # 别的 DSH 实例（TUI / sdk / 第三方 MCP）不受影响
  $n = 0
  foreach ($p in (Get-CimInstance Win32_Process -Filter "name='node.exe' or name='cmd.exe'" -ErrorAction SilentlyContinue)) {
    if ($p.CommandLine -match 'dsh\\lib\\bin\.js' -and $p.CommandLine -match '--profile\s+web') {
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
      $n++
    }
  }
  return $n
}
function Stop-App {
  $n = 0
  foreach ($p in (Get-Py)) {
    if ($p.CommandLine -match "$AppRe|$LinkRe") {
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
      $n++
    }
  }
  return $n
}
function Stop-OrphanLink {
  # link.py 的父进程死了就是孤儿：它握着 BLE 长连，不清掉新实例连不上遥控器
  $live = New-Object 'System.Collections.Generic.HashSet[int]'
  foreach ($p in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) { [void]$live.Add([int]$p.ProcessId) }
  $n = 0
  foreach ($p in (Get-Py)) {
    if ($p.CommandLine -match $LinkRe -and -not $live.Contains([int]$p.ParentProcessId)) {
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
      $n++
    }
  }
  return $n
}

# ============================ 停止 ============================
if ($Stop) {
  Head '停止语音秘书'
  Say "app.py / link.py：停掉 $(Stop-App) 个进程"
  Say "Web 实例：停掉 $(Stop-Web) 个进程"
  Start-Sleep -Seconds 2
  if (Test-Listening) {
    Say "端口 $Port 仍被占用——可能是别的程序，没有强杀" 'Yellow'
  } else {
    Say "端口 $Port 已释放" 'Green'
  }
  Write-Host ''
  Read-Host '按回车关闭本窗口'
  return
}

# ============================ 启动 ============================
Head '语音秘书 · 启动'

# ---- 1. 前置检查：缺什么直接说清，别让 pythonw 静默死掉 ----
$missing = @()
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { $missing += 'Node.js（未装或不在 PATH）' }
if (-not (Test-Path $DshBin))    { $missing += "DSH 运行时：$DshBin" }
if (-not $Python -or -not (Test-Path $Python)) { $missing += 'Python 3.11+（未装或不在 PATH）' }
if (-not (Test-Path $AppScript)) { $missing += "主程序：$AppScript" }
if ($missing.Count -gt 0) {
  Say '前置条件不齐，先补齐再启动：' 'Red'
  foreach ($m in $missing) { Say "  - $m" 'Red' }
  Say '  一次装齐：双击本目录的 setup.bat' 'Yellow'
  Write-Host ''
  Read-Host '按回车关闭本窗口'
  return
}

# Python 依赖探针。pygame 导入时会往 stdout 打横幅，所以用标记行取结果
$probe = @'
import importlib
bad = []
for n in ['webview', 'pygame', 'edge_tts', 'zai', 'mcp', 'winrt.windows.devices.bluetooth']:
    try:
        importlib.import_module(n)
    except Exception:
        bad.append(n)
print('MISSING:' + ','.join(bad))
'@
$out  = & $Python -c $probe 2>$null
$line = $out | Where-Object { $_ -like 'MISSING:*' } | Select-Object -First 1
$bad  = if ($line) { $line.Substring(8).Trim() } else { '' }
if ($bad) {
  Say "Python 依赖缺失：$bad" 'Red'
  Say "装上再启动：& \"$Python\" -m pip install -r app\requirements.txt" 'Red'
  Say '  或者重跑 setup.bat，它会帮你装' 'Red'
  Write-Host ''
  Read-Host '按回车关闭本窗口'
  return
}
Say '前置检查通过' 'Green'

# ---- 2. 清孤儿 link.py ----
Say "孤儿 link.py 清理：$(Stop-OrphanLink) 个"

# ---- 3. Web 实例：能复用就复用，否则新起 ----
$url = $null
if (Test-Listening) {
  # token 每次重启都变，日志里那条可能已过期——验一下再决定复用
  $url = Read-Token $WebOut
  if ($url -and -not (Test-Url $url)) { $url = $null }
  if ($url) {
    Say "Web 实例已在运行（端口 $Port），复用" 'Green'
  } else {
    Say "端口 $Port 被占但拿不到有效令牌，重启实例" 'Yellow'
    [void](Stop-Web)
    Start-Sleep -Seconds 2
  }
}

if (-not $url) {
  Remove-Item $WebOut, $WebErr -Force -ErrorAction SilentlyContinue
  # chcp 65001：隐藏窗口启动时 cmd 管道会回落系统码页(GBK)，把实例的 utf-8 输出弄崩
  $inner = 'chcp 65001 >nul && node "{0}" --profile web --port {1} --no-open' -f $DshBin, $Port
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $inner -WorkingDirectory $Root `
    -WindowStyle Hidden -RedirectStandardOutput $WebOut -RedirectStandardError $WebErr | Out-Null
  Say "Web 实例启动中（端口 $Port）…"

  $deadline = (Get-Date).AddSeconds(45)
  while ((Get-Date) -lt $deadline -and -not $url) {
    Start-Sleep -Milliseconds 500
    $url = Read-Token $WebOut
  }
  if (-not $url) {
    Say '45 秒没起来，看这两个日志：' 'Red'
    Say "  $WebOut" 'Red'
    Say "  $WebErr" 'Red'
    if (Test-Path $WebErr) {
      Get-Content $WebErr -Tail 12 -ErrorAction SilentlyContinue | ForEach-Object { Say "  $_" 'DarkGray' }
    }
    Write-Host ''
    Read-Host '按回车关闭本窗口'
    return
  }
  Say 'Web 实例就绪' 'Green'
}

# ---- 4. app.py：已在跑就不重复拉（会抢 BLE 连接和会话写句柄）----
if (@(Get-Py | Where-Object { $_.CommandLine -match $AppRe }).Count -gt 0) {
  Say 'app.py 已在运行，跳过' 'Green'
} else {
  Start-Process -FilePath $PythonW -ArgumentList '-u', $AppScript -WorkingDirectory $AppDir -WindowStyle Hidden
  Say 'app.py 已拉起（无控制台，日志见 app\app_debug.log）'
}

# ---- 5. 桌面快捷方式：不在、或指向别的入口，就修好 ----
$ok = $false
if (Test-Path $Shortcut) {
  try {
    $ws = New-Object -ComObject WScript.Shell
    $ok = ($ws.CreateShortcut($Shortcut).TargetPath -eq $Launcher)
  } catch { $ok = $false }
}
if (-not $ok) {
  try {
    $ws  = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($Shortcut)
    $lnk.TargetPath       = $Launcher
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation     = "$PythonW,0"
    $lnk.Description      = '语音秘书：一键启动'
    $lnk.Save()
    Say '桌面快捷方式「语音输入」已就位'
  } catch {
    Say "建快捷方式失败：$($_.Exception.Message)" 'Yellow'
  }
}

# ---- 6. 打开页面 ----
try {
  Start-Process $url
  Say '已用默认浏览器打开页面'
} catch {
  Say '打不开浏览器，手动访问下面的地址' 'Yellow'
}

Write-Host "`n    页面地址（含访问令牌，别外传）：" -ForegroundColor Green
Write-Host "    $url`n" -ForegroundColor Green
Read-Host '按回车关闭本窗口'
