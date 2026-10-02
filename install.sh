#!/bin/bash
# Installation du pack DCG / linuxloader / TeknoParrot dans Batocera.
# Usage (SSH sur Batocera) :
#   curl -Ls https://raw.githubusercontent.com/DreamerCG/linuxloader/main/install.sh | bash
# Le script telecharge l'archive complete du depot, puis FUSIONNE son dossier
# "system/" dans /userdata/system/ (rien n'est supprime dans /userdata).

set -e

REPO_URL="${ARCHIVE_URL:-https://github.com/DreamerCG/linuxloader/archive/refs/heads/main.tar.gz}"
DEST="/userdata"
# Dossier temporaire sur /userdata (le /tmp de Batocera est en RAM)
WORK="$DEST/system/.dcg_install_tmp"
BACKUP="$DEST/system/dcg_backup_$(date +%Y%m%d_%H%M%S)"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

if [ ! -d "$DEST" ]; then
    echo "ERREUR : $DEST n'existe pas (ce script doit tourner sur Batocera)"
    exit 1
fi

# 1. Telechargement et extraction de l'archive
echo "== Telechargement : $REPO_URL"
rm -rf "$WORK"
mkdir -p "$WORK"
if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$WORK/archive.tar.gz" "$REPO_URL"
elif command -v wget >/dev/null 2>&1; then
    wget -O "$WORK/archive.tar.gz" "$REPO_URL"
else
    echo "ERREUR : ni curl ni wget disponible"
    exit 1
fi

echo "== Extraction"
tar -xzf "$WORK/archive.tar.gz" -C "$WORK"
rm -f "$WORK/archive.tar.gz"

# L'archive GitHub contient un dossier racine (ex: linuxloader-main/)
SRC="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "$SRC" ] || [ ! -d "$SRC/system" ]; then
    echo "ERREUR : dossier 'system' introuvable dans l'archive"
    exit 1
fi

echo "== Source      : $SRC/system"
echo "== Destination : $DEST/system"

# 2. Sauvegarde des fichiers existants qui vont etre ecrases
echo "== Sauvegarde des fichiers existants -> $BACKUP"
(cd "$SRC" && find system -type f) | while read -r f; do
    if [ -f "$DEST/$f" ]; then
        mkdir -p "$BACKUP/$(dirname "$f")"
        cp -p "$DEST/$f" "$BACKUP/$f"
    fi
done
rmdir "$BACKUP" 2>/dev/null || true

# 3. Copie (fusion) des fichiers
echo "== Copie des fichiers"
mkdir -p "$DEST/system"
cp -a "$SRC/system/." "$DEST/system/"

# 4. Conversion des fins de ligne Windows (CRLF -> LF)
echo "== Correction des fins de ligne"
for f in \
    "$DEST/system/dcg/configgen/dcglauncher" \
    "$DEST/system/configs/emulationstation/es_systems_tecknoparrot.cfg" \
    "$DEST/system/configs/emulationstation/es_features_linuxloader.cfg"; do
    [ -f "$f" ] && sed -i 's/\r$//' "$f"
done
find "$DEST/system/dcg/configgen" -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

# 5. Droits d'execution
echo "== Droits d'execution"
chmod +x "$DEST/system/dcg/configgen/dcglauncher" 2>/dev/null || true
LL="$DEST/system/dcg/emulators/linuxloader"
if [ -d "$LL" ]; then
    chmod +x "$LL/linuxloader" "$LL"/*.so 2>/dev/null || true
    # Contenu du dossier lib/ (bibliotheques requises par linuxloader)
    if [ -d "$LL/lib" ]; then
        find "$LL/lib" -type f -exec chmod +x {} +
    fi
fi

# 6. Dossiers necessaires
mkdir -p "$DEST/roms/teknoparrot" "$DEST/system/configs/linuxloader"

# 7. Verification des fichiers cles
echo "== Verification"
missing=0
for f in \
    system/dcg/configgen/dcglauncher \
    system/dcg/configgen/generators/linuxloader/linuxloaderGenerator.py \
    system/dcg/emulators/linuxloader/linuxloader \
    system/configs/emulationstation/es_systems_tecknoparrot.cfg \
    system/configs/emulationstation/es_features_linuxloader.cfg; do
    if [ -e "$DEST/$f" ]; then
        echo "  OK       $f"
    else
        echo "  MANQUANT $f"
        missing=1
    fi
done
[ "$missing" = 1 ] && echo "ATTENTION : des fichiers sont manquants dans l'archive."

sync

echo "== Termine. Redemarrez EmulationStation (ou Batocera) pour voir le systeme."
echo "   Dans EmulationStation : Menu > Parametres > Redemarrer."
