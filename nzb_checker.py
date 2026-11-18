#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Komut satırından: python3 nzb_checker.py [config.json]"""
import sys

from nzb_checker.engine.cli import main

if __name__ == '__main__':
    main(sys.argv[1:])
