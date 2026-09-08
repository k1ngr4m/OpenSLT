#!/usr/bin/env python3
"""命令行入口；统计实现随 OpenSLT 安装包一起分发。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.builtin_statistics import main

if __name__ == "__main__":
    sys.exit(main())
