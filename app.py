"""LightCore giriş noktası. OpenRGB, bu dosyanın yanındaki 'openrgb' klasöründedir."""
import os
import sys

from lightcore.__main__ import main

BASE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--openrgb" not in args:
        args = ["--openrgb", os.path.join(BASE, "openrgb")] + args
    sys.exit(main(args))
