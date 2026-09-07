#!/bin/bash
# 电商产品图套装生成服务 - Mac/Linux 一键启动

echo "============================================================"
echo "  电商产品图套装生成服务 - 本地启动"
echo "============================================================"
echo ""

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未检测到 Python3，请先安装 Python 3.10+"
    echo "Mac: brew install python3"
    echo "Linux: sudo apt install python3 python3-pip"
    exit 1
fi

echo "[1/3] 检查依赖..."
pip3 install -r requirements.txt -q

echo ""
echo "[2/3] 启动服务..."
echo ""
echo "服务启动后，不要关闭此终端窗口！"
echo "关闭窗口 = 停止服务"
echo ""
echo "[3/3] 服务运行中..."
echo ""

cd "$(dirname "$0")"
python3 app.py
