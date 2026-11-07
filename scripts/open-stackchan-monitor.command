#!/bin/zsh
set -eu
cd -- "${0:A:h}/.."
# Finder does not load the interactive shell's Node version manager.
monitor_node=$(command -v node || true)
if [[ -z "$monitor_node" && -x /opt/homebrew/bin/node ]]; then monitor_node=/opt/homebrew/bin/node; fi
if [[ -z "$monitor_node" && -x /usr/local/bin/node ]]; then monitor_node=/usr/local/bin/node; fi
if [[ -z "$monitor_node" ]]; then
  for candidate in "$HOME"/.nvm/versions/node/*/bin/node(NOn); do
    if [[ -x "$candidate" ]]; then monitor_node="$candidate"; break; fi
  done
fi
if [[ -z "$monitor_node" ]]; then
  print '需要 Node.js 24。安装后重新双击此文件。'; read -r '?按回车退出'; exit 1
fi
monitor_url=$("$monitor_node" web/monitor/service.mjs start) || { read -r '?启动失败，按回车退出'; exit 1; }
print "$monitor_url"
open "$monitor_url"
