@echo off
rem 语音 SDK 运行时的本地 dsh 启动包装（bundled 单文件运行时 pwsh 工具损坏，故走本地 dsh）
rem chcp 65001：无控制台启动（pythonw/Start-Process Hidden）时 cmd 管道输出会回落到
rem 系统码页(GBK)，把 SDK 的 utf-8 文本管道弄崩；先切 UTF-8 码页再起 node。
chcp 65001 >nul
node "%USERPROFILE%\.dsh\profiles\node_modules\@deepseek-ai\dsh\lib\bin.js" %*