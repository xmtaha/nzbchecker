"""Komut satırı giriş noktası."""
import sys

from .checker import NZBChecker


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    print("=" * 60)
    print("NZB Checker - NZB Dosya Kontrolü ve Sınıflandırma")
    print("=" * 60 + "\n")

    config_file = argv[0] if argv else "config.json"

    try:
        checker = NZBChecker(config_file)
        checker.process_nzb_files()
        checker.print_summary()
    except KeyboardInterrupt:
        print("\n\nİşlem kullanıcı tarafından durduruldu.")
        sys.exit(0)
    except Exception as e:
        print(f"\nFatal hata: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
