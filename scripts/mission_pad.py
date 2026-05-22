#!/usr/bin/env python3
"""挑战卡 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 挑战卡')
    sub = parser.add_subparsers(dest='action', required=True)

    sub.add_parser('enable', help='开启挑战卡识别')
    sub.add_parser('disable', help='关闭挑战卡识别')
    sub.add_parser('id', help='获取挑战卡 ID')
    sub.add_parser('xyz', help='获取相对坐标')

    p_fly = sub.add_parser('fly', help='飞至挑战卡上方')
    p_fly.add_argument('--id', type=int, required=True, help='挑战卡 ID (1-8)')

    sub.add_parser('detect', help='开启挑战卡预览窗口（非阻塞）')
    sub.add_parser('detect_stop', help='关闭挑战卡预览窗口并返回最后检测结果')

    args = parser.parse_args()

    if args.action == 'fly':
        cmd = f"mission_pad fly --id {getattr(args, 'id')}"
    else:
        cmd = f"mission_pad {args.action}"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
