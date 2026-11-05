#!/bin/zsh
set -eu
cd "${0:A:h:h}"
echo '请在飞书官方向导中选择「使用已有应用」。App Secret 在密码框输入，不会回显，也不要发送到聊天。'
python3 -B scripts/feishu-setup.py existing-app
python3 -B scripts/feishu-setup.py check
echo '认证步骤已完成，可以回到 Codex。'
read -k 1 '?按任意键关闭此窗口'
