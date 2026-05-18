#!/usr/bin/env python3
"""LED 彩灯控制 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello LED 控制')
    sub = parser.add_subparsers(dest='action', required=True)

    p_solid = sub.add_parser('solid', help='常亮')
    p_solid.add_argument('--r', type=int, required=True)
    p_solid.add_argument('--g', type=int, required=True)
    p_solid.add_argument('--b', type=int, required=True)

    p_breathe = sub.add_parser('breathe', help='呼吸灯')
    p_breathe.add_argument('--freq', type=float, required=True)
    p_breathe.add_argument('--r', type=int, required=True)
    p_breathe.add_argument('--g', type=int, required=True)
    p_breathe.add_argument('--b', type=int, required=True)

    p_blink = sub.add_parser('blink', help='交替闪烁')
    p_blink.add_argument('--freq', type=float, required=True)
    p_blink.add_argument('--r1', type=int, required=True)
    p_blink.add_argument('--g1', type=int, required=True)
    p_blink.add_argument('--b1', type=int, required=True)
    p_blink.add_argument('--r2', type=int, required=True)
    p_blink.add_argument('--g2', type=int, required=True)
    p_blink.add_argument('--b2', type=int, required=True)

    sub.add_parser('off', help='关闭')

    args = parser.parse_args()

    if args.action == 'solid':
        cmd = f"led solid {args.r} {args.g} {args.b}"
    elif args.action == 'breathe':
        cmd = f"led breathe {args.freq} {args.r} {args.g} {args.b}"
    elif args.action == 'blink':
        cmd = f"led blink {args.freq} {args.r1} {args.g1} {args.b1} {args.r2} {args.g2} {args.b2}"
    elif args.action == 'off':
        cmd = "led off"

    print(send_command(cmd))


if __name__ == '__main__':
    main()
