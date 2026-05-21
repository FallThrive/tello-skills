#!/usr/bin/env python3
"""实时人员跟随——通过 controller 内闭环 task follow 命令实现"""
import argparse
import time
import signal
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='实时人员跟随')
    parser.add_argument('--duration', type=int, default=120, help='跟随时长(秒)')
    parser.add_argument('--model', choices=['seg', 'pose'], default='pose',
                        help='跟踪模型')
    parser.add_argument('--kp-yaw', type=float, default=0.2, help='水平偏转系数')
    parser.add_argument('--kp-ud', type=float, default=0.3, help='垂直方向系数')
    parser.add_argument('--fb-speed', type=int, default=15, help='前后移动速度(cm/s)')
    parser.add_argument('--dist-low', type=float, default=None,
                        help='距离下限（pose: 躯干高度px, seg: 掩码面积px）')
    parser.add_argument('--dist-high', type=float, default=None,
                        help='距离上限（pose: 躯干高度px, seg: 掩码面积px）')
    args = parser.parse_args()

    def sigint_handler(signum, frame):
        print("\n收到中断信号，停止跟随...")
        try:
            send_command("task stop")
        except Exception as e:
            print(f"无法发送停止命令: {e}")

    signal.signal(signal.SIGINT, sigint_handler)

    resp = send_command("vision stream_on")
    if resp.startswith("error"):
        print(f"开启视频流失败: {resp}")
        return
    time.sleep(1)

    send_command("led solid 255 0 0")

    cmd_parts = [f"task follow --model {args.model} --duration {args.duration}"]
    if args.kp_yaw is not None:
        cmd_parts.append(f"--kp-yaw {args.kp_yaw}")
    if args.kp_ud is not None:
        cmd_parts.append(f"--kp-ud {args.kp_ud}")
    if args.fb_speed is not None:
        cmd_parts.append(f"--fb-speed {args.fb_speed}")
    if args.dist_low is not None:
        cmd_parts.append(f"--dist-low {args.dist_low}")
    if args.dist_high is not None:
        cmd_parts.append(f"--dist-high {args.dist_high}")
    cmd = " ".join(cmd_parts)

    print(f"跟随模式开始，时长 {args.duration} 秒，模型: {args.model}")

    response = send_command(cmd, timeout=args.duration + 15)
    print(f"跟随结果: {response}")

    send_command("flight rc 0 0 0 0")
    send_command("led off")
    send_command("matrix off")
    send_command("vision stream_off")
    print("跟随结束")


if __name__ == '__main__':
    main()
