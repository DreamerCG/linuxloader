#!/bin/bash
# Installation du pack DCG / linuxloader / TeknoParrot dans Batocera.
# Usage (SSH sur Batocera) :
#   curl -Ls https://raw.githubusercontent.com/DreamerCG/linuxloader/main/install.sh | bash
# Le script télécharge l'archive complète du dépôt, puis FUSIONNE son dossier
# "system/" dans /userdata/system/ (rien n'est supprimé dans /userdata).

set -e

REPO_URL="${ARCHIVE_URL:-https://github.com/DreamerCG/linuxloader/archive/refs/heads/main.tar.gz}"
DEST="/userdata"
# Dossier temporaire sur /userdata (le /tmp de Batocera est en RAM)
WORK="$DEST/system/.dcg_install_tmp"
BACKUP="$DEST/system/dcg_backup_$(date +%Y%m%d_%H%M%S)"
TOTAL_STEPS=6

# --- Affichage -------------------------------------------------------------
if [ -t 1 ]; then
    B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; C=$'\033[36m'; N=$'\033[0m'
else
    B=""; G=""; R=""; Y=""; C=""; N=""
fi
STEP=0
step() { STEP=$((STEP + 1)); printf "\n${B}${C}[%s/%s]${N} ${B}%s${N}\n" "$STEP" "$TOTAL_STEPS" "$1"; }
ok()   { printf "  ${G}✔${N} %s\n" "$1"; }
warn() { printf "  ${Y}!${N} %s\n" "$1"; }
fail() { printf "\n${R}✘ ERREUR : %s${N}\n" "$1"; exit 1; }

cleanup() {
    code=$?
    rm -rf "$WORK"
    if [ "$code" -ne 0 ] && [ -d "$BACKUP" ]; then
        printf "\n${Y}Sauvegarde conservée (installation interrompue) : %s${N}\n" "$BACKUP"
    fi
}
trap cleanup EXIT

printf "${B}${C}\n=========================================\n"
printf "   Installation DCG / linuxloader\n"
printf "=========================================${N}\n"

[ -d "$DEST" ] || fail "$DEST n'existe pas (ce script doit tourner sur Batocera)"

# --- 1. Téléchargement -----------------------------------------------------
step "Téléchargement de l'archive"
rm -rf "$WORK"
mkdir -p "$WORK"
if command -v curl >/dev/null 2>&1; then
    curl -fsL --retry 5 --retry-delay 2 -o "$WORK/archive.tar.gz" "$REPO_URL" \
        || fail "téléchargement impossible (vérifiez la connexion Internet)"
elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$WORK/archive.tar.gz" "$REPO_URL" \
        || fail "téléchargement impossible (vérifiez la connexion Internet)"
else
    fail "ni curl ni wget n'est disponible"
fi
SIZE="$(du -h "$WORK/archive.tar.gz" | cut -f1)"
ok "Archive téléchargée ($SIZE)"

# --- 2. Extraction ---------------------------------------------------------
step "Extraction"
tar -xzf "$WORK/archive.tar.gz" -C "$WORK" || fail "archive corrompue"
rm -f "$WORK/archive.tar.gz"
# L'archive GitHub contient un dossier racine (ex: linuxloader-main/)
SRC="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -n "$SRC" ] && [ -d "$SRC/system" ] || fail "dossier 'system' introuvable dans l'archive"
NFILES="$(find "$SRC/system" -type f | wc -l)"
ok "$NFILES fichiers prêts à installer"

# --- 3. Sauvegarde ---------------------------------------------------------
step "Sauvegarde des fichiers existants"
NBACKUP=0
while read -r f; do
    if [ -f "$DEST/$f" ]; then
        mkdir -p "$BACKUP/$(dirname "$f")"
        cp -p "$DEST/$f" "$BACKUP/$f"
        NBACKUP=$((NBACKUP + 1))
    fi
done < <(cd "$SRC" && find system -type f)
if [ "$NBACKUP" -gt 0 ]; then
    ok "$NBACKUP fichier(s) sauvegardé(s) temporairement (supprimés si tout se passe bien)"
else
    ok "Première installation, rien à sauvegarder"
fi

# --- 4. Copie --------------------------------------------------------------
step "Copie des fichiers vers $DEST/system"
mkdir -p "$DEST/system"
cp -a "$SRC/system/." "$DEST/system/"
ok "Fichiers copiés (fusion avec l'existant, rien n'est supprimé)"

# --- 5. Finalisation -------------------------------------------------------
step "Finalisation (fins de ligne, droits, dossiers)"
for f in \
    "$DEST/system/dcg/configgen/dcglauncher" \
    "$DEST/system/configs/emulationstation/es_systems_teknoparrot.cfg" \
    "$DEST/system/configs/emulationstation/es_features_linuxloader.cfg"; do
    [ -f "$f" ] && sed -i 's/\r$//' "$f"
done
find "$DEST/system/dcg/configgen" -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
ok "Fins de ligne corrigées"

chmod +x "$DEST/system/dcg/configgen/dcglauncher" 2>/dev/null || true
LL="$DEST/system/dcg/emulators/linuxloader"
if [ -d "$LL" ]; then
    chmod +x "$LL/linuxloader" "$LL"/*.so 2>/dev/null || true
    # Contenu du dossier lib/ (bibliothèques requises par linuxloader)
    [ -d "$LL/lib" ] && find "$LL/lib" -type f -exec chmod +x {} +
fi
ok "Droits d'exécution appliqués"

mkdir -p "$DEST/roms/teknoparrot" "$DEST/system/configs/linuxloader"
ok "Dossiers créés (roms/teknoparrot, configs/linuxloader)"

# --- 6. Vérification -------------------------------------------------------
step "Vérification"
missing=0
for f in \
    system/dcg/configgen/dcglauncher \
    system/dcg/configgen/generators/linuxloader/linuxloaderGenerator.py \
    system/dcg/emulators/linuxloader/linuxloader \
    system/configs/emulationstation/es_systems_teknoparrot.cfg \
    system/configs/emulationstation/es_features_linuxloader.cfg; do
    if [ -e "$DEST/$f" ]; then
        ok "$f"
    else
        printf "  ${R}✘${N} %s ${R}(manquant)${N}\n" "$f"
        missing=1
    fi
done
sync

# Installation réussie : la sauvegarde temporaire n'est plus utile
if [ -d "$BACKUP" ]; then
    rm -rf "$BACKUP"
    ok "Sauvegarde temporaire supprimée"
fi

printf "\n"
if [ "$missing" = 1 ]; then
    warn "Installation terminée, mais des fichiers sont manquants dans l'archive."
else
    printf "${B}${G}✔ Installation terminée avec succès !${N}\n"
fi
printf "  Placez vos jeux dans : ${B}%s/roms/teknoparrot${N}\n" "$DEST"

# --- Redémarrage optionnel d'EmulationStation ------------------------------
# (le script est lu via un pipe : on interroge le clavier via /dev/tty)
if [ -r /dev/tty ] && command -v batocera-es-swissknife >/dev/null 2>&1; then
    printf "\n${B}Redémarrer EmulationStation maintenant ? [o/N] ${N}"
    read -r answer < /dev/tty || answer=""
    case "$answer" in
        o|O|y|Y|oui|OUI) batocera-es-swissknife --restart ;;
        *) printf "OK. Redémarrez plus tard : Menu > Paramètres > Redémarrer.\n" ;;
    esac
else
    printf "  Redémarrez EmulationStation pour voir le système.\n"
fi
