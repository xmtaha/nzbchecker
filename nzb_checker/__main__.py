"""Giriş noktası: python -m nzb_checker [gui|cli] [config.json]"""
import argparse
import sys

from . import __version__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--run':      # AppRun: paketi içe aktarabilmek için yol ekle
        sys.path.insert(0, '/usr/src')
        argv.pop(0)

    parser = argparse.ArgumentParser(
        prog='nzb-checker',
        description='NZB dosyalarını Usenet sağlayıcılarında kontrol eder, '
                    'eksik/hatalı olanları ayırır.')
    parser.add_argument('mode', nargs='?', default='gui',
                        help="'gui' (varsayılan) veya 'cli'")
    parser.add_argument('config', nargs='?', default='config.json',
                        help='cli için ayar dosyası (varsayılan: config.json)')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = parser.parse_args(argv)

    if args.mode not in ('gui', 'cli') and args.mode.endswith('.json'):
        args.mode, args.config = 'gui', args.mode   # eski kullanım: config yolu = GUI

    if args.mode == 'cli':
        from .engine.cli import main as cli_main
        cli_main([args.config])
    elif args.mode == 'gui':
        from .gui.__main__ import main as gui_main
        gui_main()
    else:
        parser.error(f"geçersiz mod: {args.mode} (gui veya cli)")


if __name__ == '__main__':
    main()
