#!/usr/bin/env python3
"""方向搜索挑战卡——小步飞行→等待→检测挑战卡→飞上方+亮灯+屏显"""
import argparse
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='方向搜索挑战卡')
    parser.add_argument('--direction', '-d', required=True,
                        choices=['f', 'b', 'l', 'r'])
    parser.add_argument('--step', type=int, default=30, help='每步距离 cm')
    parser.add_argument('--max-attempts', type=int, default=10, help='最大尝试次数')

    args = parser.parse_args()
    direction = args.direction
    step = args.step
    max_attempts = args.max_attempts

    dir_map = {'f': 'f', 'b': 'b', 'l': 'l', 'r': 'r'}

    send_command("mission_pad enable")

    found = False
    preview_started = False
    for i in range(max_attempts):
        resp = send_command(f"flight move {dir_map[direction]} {step}")
        if resp.startswith("error"):
            print(f"飞行错误: {resp}")
            break

        time.sleep(1.0)

        if not preview_started:
            send_command("mission_pad detect")
            preview_started = True

        pad_id = -1
        for _ in range(5):
            resp = send_command("mission_pad id")
            if resp.startswith("error"):
                print(f"检测错误: {resp}")
                break
            try:
                pid = int(resp)
            except ValueError:
                pid = -1
            if 1 <= pid <= 8:
                pad_id = pid
                break
            time.sleep(0.2)

        if 1 <= pad_id <= 8:
            print(f"检测到挑战卡 #{pad_id}")
            send_command(f"mission_pad fly --id {pad_id}")
            send_command("led solid 0 0 255")
            send_command(f"matrix static b {pad_id}")
            found = True
            break

        print(f"尝试 {i+1}/{max_attempts}，未检测到挑战卡")

    if not found:
        print(f"超 max_attempts={max_attempts}，未找到挑战卡")
        send_command("led solid 255 0 0")
        time.sleep(1)
        send_command("led off")

    if preview_started:
        result = send_command("mission_pad detect_stop")
        print(f"挑战卡预览关闭: {result}")

    send_command("mission_pad disable")


if __name__ == '__main__':
    main()
