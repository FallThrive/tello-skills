#!/usr/bin/env python3
"""传感器数据 CLI"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _client import send_command


def main():
    parser = argparse.ArgumentParser(description='Tello 传感器')
    sub = parser.add_subparsers(dest='action', required=True)

    for action in ['battery', 'tof', 'attitude', 'acceleration',
                   'height', 'flight_time', 'barometer']:
        sub.add_parser(action, help=f'获取 {action}')

    args = parser.parse_args()
    print(send_command(f"sensor {args.action}"))


if __name__ == '__main__':
    main()
