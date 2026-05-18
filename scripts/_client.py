"""Tello Controller 客户端——被所有 CLI 脚本导入的薄封装。

通过 TCP（127.0.0.1:9999）向 controller 进程发送文本命令，
并返回响应字符串。
"""

import socket

TCP_HOST = '127.0.0.1'
TCP_PORT = 9999


def send_command(cmd: str) -> str:
    """向 controller 发送命令并返回响应"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((TCP_HOST, TCP_PORT))
        sock.send(cmd.encode())
        response = sock.recv(4096).decode().strip()
        return response
    except ConnectionRefusedError:
        return "error: controller not running. Start with: uv run scripts/controller.py &"
    except (socket.timeout, OSError) as e:
        return f"error: {e}"
    finally:
        sock.close()
