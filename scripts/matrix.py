#!/usr/bin/env python3
"""LED 点阵屏控制 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 点阵屏控制')
    sub = parser.add_subparsers(dest='action', required=True)

    p_scroll = sub.add_parser('scroll', help='滚动显示')
    p_scroll.add_argument('--direction', '-d', required=True,
                          choices=['l', 'r', 'u', 'd'])
    p_scroll.add_argument('--color', '-c', required=True,
                          choices=['r', 'b', 'p'])
    p_scroll.add_argument('--freq', type=float, required=True)
    p_scroll.add_argument('--text', '-t', required=True)

    p_static = sub.add_parser('static', help='静态显示')
    p_static.add_argument('--color', '-c', required=True,
                          choices=['r', 'b', 'p'])
    p_static.add_argument('--text', '-t', required=True)

    sub.add_parser('off', help='关闭')

    args = parser.parse_args()

    if args.action == 'scroll':
        cmd = f"matrix scroll {args.direction} {args.color} {args.freq} {args.text}"
    elif args.action == 'static':
        cmd = f"matrix static {args.color} {args.text}"
    elif args.action == 'off':
        cmd = "matrix off"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
