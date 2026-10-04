#!/bin/sh
# 打包 Linux 单文件：产物 dist/SprocketModManager-linux-x64。版本与 Windows 构建同源（modman.APP_VERSION）。
set -e
ProjectRoot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ProjectRoot"

if [ -x ".venv/bin/python" ]; then
    Python=".venv/bin/python"
else
    Python="${PYTHON:-python3}"
fi

"$Python" -m PyInstaller \
    --noconfirm \
    --clean \
    --noupx \
    --onefile \
    --name SprocketModManager-linux-x64 \
    --add-data "sprocket_mod_manager/presentation/client_ui:sprocket_mod_manager/presentation/client_ui" \
    --add-data "resources/app-icon.ico:resources" \
    --collect-submodules dnfile \
    modman.py

Output="$ProjectRoot/dist/SprocketModManager-linux-x64"
if [ ! -f "$Output" ]; then
    echo "Build output not found: $Output" >&2
    exit 1
fi
echo "Built $Output"
sha256sum "$Output"
