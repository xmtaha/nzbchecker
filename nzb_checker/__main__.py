"""Giriş noktası: python -m nzb_checker [gui|cli] [args]"""
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--run':      # AppRun: paketi içe aktarabilmek için yol ekle
        sys.path.insert(0, '/usr/src')
        argv.pop(0)
    mode = argv.pop(0) if argv and not argv[0].endswith('.json') else 'gui'
    if mode == 'cli':
        from .engine.cli import main as cli_main
        cli_main(argv)
    else:
        from .gui.__main__ import main as gui_main
        gui_main()


if __name__ == '__main__':
    main()
