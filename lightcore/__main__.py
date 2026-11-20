import argparse
import sys

from .engine import DEFAULT_ARGB_LEDS


def build_parser():
    p = argparse.ArgumentParser(prog="lightcore", description="RGB ve fan kontrolü")
    p.add_argument("--openrgb", help="OpenRGB.exe ya da klasörü (verilirse gizli başlatılır)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6742)
    p.add_argument("--board", dest="board", action="store_true", default=True,
                   help="anakart fan/kasa çıkışlarını sür (varsayılan)")
    p.add_argument("--no-board", dest="board", action="store_false",
                   help="anakart fan/kasa çıkışlarına hiç dokunma")
    p.add_argument("--argb-leds", type=int, default=DEFAULT_ARGB_LEDS,
                   help="OpenRGB'de 0 LED görünen JRAINBOW/JARGB başlıklarına verilecek LED sayısı "
                        f"(varsayılan {DEFAULT_ARGB_LEDS}; 0 verilirse dokunulmaz)")
    p.add_argument("--minimized", action="store_true", help="simge tepsisinde başlasın")
    p.add_argument("--selfcheck", action="store_true", help="paketin eksiksiz olduğunu denetle ve çık; donanıma dokunmaz")
    return p


def selfcheck():
    import tkinter as tk
    from . import ble, game, screenshot, tray, ui, ui_dash, ui_fans, ui_settings, ui_shots  # noqa: F401
    if getattr(sys, "frozen", False):  # pakette Bluetooth kütüphanesi de olmalı
        import bleak.backends.winrt.client  # noqa: F401
        import bleak.backends.winrt.scanner  # noqa: F401
        if not ble.available():
            return 3
    root = tk.Tk()
    root.withdraw()
    root.destroy()
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    if args.selfcheck:
        return selfcheck()
    from .ui import run
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
