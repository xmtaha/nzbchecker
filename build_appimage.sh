#!/bin/sh
# NZB Checker AppImage üretir: ./build_appimage.sh [çıktı.appimage]
set -e
cd "$(dirname "$0")"

out="$1"
[ -n "$out" ] || out=NZB-Checker-x86_64.AppImage
# 2. argüman: gömülü Python/Tk ağacı (verilirse "bundled" sürüm üretilir)
python_tree="$2"

tool="$APPIMAGETOOL"
if [ ! -x "$tool" ]; then
    tool=/tmp/appimagetool.AppImage
    curl -fsSL -o "$tool" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$tool"
fi

appdir=/tmp/NZB-Checker.AppDir
rm -rf "$appdir"
mkdir -p "$appdir/usr/src"
cp nzb_gui.py nzb_checker.py nntp_client.py "$appdir/usr/src/"
cp -r tkdnd "$appdir/usr/src/tkdnd"
if [ -n "$python_tree" ]; then
    cp AppRun-bundled "$appdir/AppRun"
    cp -a "$python_tree" "$appdir/usr/python"
else
    cp AppRun "$appdir/AppRun"
fi
cp nzb-checker.desktop "$appdir/nzb-checker.desktop"
cp nzb-checker.png "$appdir/nzb-checker.png"
cp nzb-checker.png "$appdir/.DirIcon"
chmod -R a+rX "$appdir"

rm -f "$out"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$tool" --no-appstream "$appdir" "$out"
ls -lh "$out"
