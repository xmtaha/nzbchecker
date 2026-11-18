"""GUI giriş noktası: python -m nzb_checker.gui"""
import os
from pathlib import Path

from .app import App
from .config import ensure_config


def main():
    ensure_config()
    os.chdir(Path.home())
    App().mainloop()


if __name__ == '__main__':
    main()
