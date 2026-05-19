#!/usr/bin/env python3
"""YOLO 人员检测 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello YOLO 检测')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('detect', help='检测人员（输出边界框+中心+置信度 JSON）')
    sub.add_parser('count', help='统计人数')
    args = parser.parse_args()
    print(send_command(f"yolo {args.action}"))


if __name__ == '__main__':
    main()
