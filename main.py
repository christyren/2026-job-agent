"""Entry point.

Usage:
  python3 main.py                          # offline mock demo (no API key needed)
  python3 main.py "处理 200 个岗位要花多少钱?"
  ANTHROPIC_API_KEY=... python3 main.py --real "我的求职标准是什么?"
"""
from __future__ import annotations

import os
import sys

from job_agent.agent import run_mock, run_real

DEFAULT_DEMO = [
    "我的求职标准是什么?",
    "处理 200 个岗位大概要花多少钱?",
]


def main(argv: list[str]) -> int:
    use_real = "--real" in argv
    args = [a for a in argv if a != "--real"]
    queries = args or DEFAULT_DEMO

    if use_real and not os.getenv("ANTHROPIC_API_KEY"):
        print("错误: --real 模式需要先设置 ANTHROPIC_API_KEY（见 .env.example）")
        return 1

    for q in queries:
        print("\n" + "=" * 60)
        (run_real if use_real else run_mock)(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
