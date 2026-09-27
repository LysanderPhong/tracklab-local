#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c "import pymobiledevice3" >/dev/null 2>&1; then
  print '首次使用：正在准备项目内运行环境（需要联网）。'
  if command -v uv >/dev/null 2>&1; then
    uv sync --locked
  elif [[ -x "$HOME/.local/bin/uv" ]]; then
    "$HOME/.local/bin/uv" sync --locked
  elif command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv .venv
    .venv/bin/python -m pip install --require-hashes -r requirements-desktop.txt
  else
    print '此电脑还没有运行环境。请先安装 Python 3.12 或 uv，再双击本文件。'
    read '?按回车退出…'
    exit 1
  fi
fi
if ! .venv/bin/python desktop_launcher.py; then
  read '?启动未完成，按回车退出…'
  exit 1
fi
