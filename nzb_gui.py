#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI'yi başlatır: python3 nzb_gui.py (ya da python3 -m nzb_checker)"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nzb_checker.gui.__main__ import main

if __name__ == '__main__':
    main()
