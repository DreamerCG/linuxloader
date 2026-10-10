#!/bin/bash
# Installation du pack DCG / linuxloader / TeknoParrot dans Batocera.
# Usage (SSH sur Batocera) :
#   curl -Ls https://raw.githubusercontent.com/DreamerCG/linuxloader/main/install.sh | bash
# Le script télécharge l'archive complète du dépôt, puis FUSIONNE son dossier
# "system/" dans /userdata/system/ (rien n'est supprimé dans /userdata).

set -e

REPO_URL="${ARCHIVE_URL:-https://github.com/DreamerCG/linuxloader/archive/refs/heads/main.tar.gz}"
WAL_PREFIX_URL="${WAL_PREFIX_URL:-https://media.githubusercontent.com/media/DreamerCG/linuxloader/main/system/dcg/emulators/windows-arcade-loader/wine-prefix/full.tar.gz}"
WAL_RUNNER_URL="${WAL_RUNNER_URL:-https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton11-7/GE-Proton11-7-x86_64.tar.gz}"
DEST="/userdata"
# Dossier temporaire sur /userdata (le /tmp de Batocera est en RAM)
WORK="$DEST/system/.dcg_install_tmp"
BACKUP="$DEST/system/dcg_backup_$(date +%Y%m%d_%H%M%S)"
TOTAL_STEPS=7

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

resolve_lfs_archive() {
    local archive="$1" url="$2" label="$3"
    [ -f "$archive" ] || return 0
    if head -n 1 "$archive" | grep -qF 'version https://git-lfs.github.com/spec/v1'; then
        printf "  Récupération de %s stocké avec Git LFS...\n" "$label"
        if command -v curl >/dev/null 2>&1; then
            curl -fsL --retry 5 --retry-delay 2 -o "$archive" "$url" \
                || fail "téléchargement LFS impossible : $label"
        elif command -v wget >/dev/null 2>&1; then
            wget -q -O "$archive" "$url" \
                || fail "téléchargement LFS impossible : $label"
        else
            fail "ni curl ni wget n'est disponible pour télécharger $label via Git LFS"
        fi
        head -n 1 "$archive" | grep -qF 'version https://git-lfs.github.com/spec/v1' \
            && fail "GitHub a renvoyé le pointeur LFS au lieu de $label"
        ok "$label téléchargé"
    fi
    gzip -t "$archive" || fail "archive invalide ou incomplète : $label"
}

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

WAL_PREFIX="$SRC/system/dcg/emulators/windows-arcade-loader/wine-prefix/full.tar.gz"
INSTALLED_WAL_PREFIX="$DEST/system/dcg/emulators/windows-arcade-loader/wine-prefix/full.tar.gz"
if [ -f "$INSTALLED_WAL_PREFIX" ] \
    && ! head -n 1 "$INSTALLED_WAL_PREFIX" | grep -qF 'version https://git-lfs.github.com/spec/v1' \
    && gzip -t "$INSTALLED_WAL_PREFIX" >/dev/null 2>&1; then
    cp -p "$INSTALLED_WAL_PREFIX" "$WAL_PREFIX" \
        || fail "réutilisation de l'archive du préfixe Wine installée impossible"
    ok "Archive du préfixe Wine déjà installée, téléchargement ignoré"
else
    resolve_lfs_archive "$WAL_PREFIX" "$WAL_PREFIX_URL" "l'archive du préfixe Wine"
fi

# Le runner vient directement de la release officielle. L'extraction avec tar
# préserve les permissions d'exécution nécessaires aux binaires Wine.
WAL_RUNNER="$DEST/system/wine/custom/GE-Proton11-7-x86_64"
if [ -x "$WAL_RUNNER/bin/wine" ] && [ -x "$WAL_RUNNER/lib/wine/x86_64-unix/wine" ]; then
    ok "Runner GE-Proton déjà installé, téléchargement ignoré"
else
    WAL_RUNNER_ARCHIVE="$WORK/GE-Proton11-7-x86_64.tar.gz"
    if command -v curl >/dev/null 2>&1; then
        curl -fsL --retry 5 --retry-delay 2 -o "$WAL_RUNNER_ARCHIVE" "$WAL_RUNNER_URL" \
            || fail "téléchargement du runner GE-Proton impossible"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$WAL_RUNNER_ARCHIVE" "$WAL_RUNNER_URL" \
            || fail "téléchargement du runner GE-Proton impossible"
    else
        fail "ni curl ni wget n'est disponible pour télécharger le runner GE-Proton"
    fi
    gzip -t "$WAL_RUNNER_ARCHIVE" || fail "archive GE-Proton invalide ou incomplète"
    mkdir -p "$DEST/system/wine/custom"
    tar -xzpf "$WAL_RUNNER_ARCHIVE" -C "$DEST/system/wine/custom" \
        || fail "extraction du runner GE-Proton impossible"
    ok "Runner GE-Proton téléchargé et installé dans $WAL_RUNNER"
fi

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
ok "Fichiers copiés"

WAL_DESKTOP="$DEST/system/tools/windows-arcade-loader.desktop"
if [ -f "$WAL_DESKTOP" ]; then
    mkdir -p /usr/share/applications
    cp -f "$WAL_DESKTOP" /usr/share/applications/windows-arcade-loader.desktop \
        || fail "copie du raccourci Windows Arcade Loader impossible"
    chmod 644 /usr/share/applications/windows-arcade-loader.desktop
    ok "Raccourci Windows Arcade Loader installé dans /usr/share/applications"
else
    warn "Raccourci introuvable : $WAL_DESKTOP"
fi

# Supprime uniquement les bibliothèques LinuxLoader qui ne sont plus dans
# l'archive courante. Les autres fichiers utilisateur restent intacts.
SRC_LL="$SRC/system/dcg/emulators/linuxloader"
DEST_LL="$DEST/system/dcg/emulators/linuxloader"
REMOVED_SO=0
if [ -d "$SRC_LL" ] && [ -d "$DEST_LL" ]; then
    while IFS= read -r -d '' old_so; do
        relative="${old_so#"$DEST_LL"/}"
        if [ ! -f "$SRC_LL/$relative" ]; then
            rm -f "$old_so"
            REMOVED_SO=$((REMOVED_SO + 1))
        fi
    done < <(find "$DEST_LL" -type f -name '*.so' -print0)
fi
ok "$REMOVED_SO bibliothèque(s) .so obsolète(s) supprimée(s)"

# --- 5. Finalisation -------------------------------------------------------
step "Finalisation (fins de ligne, droits, dossiers)"
for f in \
    "$DEST/system/dcg/configgen/dcglauncher" \
    "$DEST/system/configs/emulationstation/es_systems_teknoparrot.cfg" \
    "$DEST/system/configs/emulationstation/es_features_linuxloader.cfg" \
    "$DEST/system/configs/emulationstation/es_features_windowsarcadeloader.cfg"; do
    [ -f "$f" ] && sed -i 's/\r$//' "$f"
done
find "$DEST/system/dcg/configgen" -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
ok "Fins de ligne corrigées"

chmod +x "$DEST/system/dcg/configgen/dcglauncher" 2>/dev/null || true
chmod +x "$DEST/system/dcg/bin/batocera-wine" 2>/dev/null || true
chmod +x "$DEST/system/dcg/bin/batocera-wine-guns" 2>/dev/null || true

LL="$DEST/system/dcg/emulators/linuxloader"
if [ -d "$LL" ]; then
    chmod -R 755 "$LL"
fi
WAL="$DEST/system/dcg/emulators/windows-arcade-loader"
if [ -d "$WAL" ]; then
    for f in arcade-launcher arcade-launcher-gui; do
        [ -f "$WAL/$f" ] && chmod +x "$WAL/$f"
    done
fi
ok "Droits d'exécution appliqués"

SVC="$DEST/system/services/dcg_update"
if [ -f "$SVC" ]; then
    sed -i 's/\r$//' "$SVC"
    chmod +x "$SVC"
    if command -v batocera-services >/dev/null 2>&1; then
        batocera-services enable dcg_update >/dev/null 2>&1 || warn "activation du service impossible"
    fi
    ok "Service dcg_update installé"
fi

mkdir -p "$DEST/roms/teknoparrot" "$DEST/system/configs/linuxloader"
ok "Dossiers créés (roms/teknoparrot, configs/linuxloader)"

# --- 6. Préconfiguration des jeux (batocera.conf) --------------------------
step "Préconfiguration des jeux (batocera.conf)"
BATOCERA_CONF="$DEST/system/batocera.conf"
[ -f "$BATOCERA_CONF" ] || touch "$BATOCERA_CONF"

# Ajoute "clé=valeur" à la fin du fichier, uniquement si la clé n'existe pas
# déjà (une valeur déjà définie par l'utilisateur n'est jamais modifiée).
conf_add() {
    local line="$1" key="${1%%=*}"
    if awk -v k="$key=" 'index($0, k) == 1 { found = 1 } END { exit !found }' "$BATOCERA_CONF"; then
        return 1
    fi
    # S'assure que le fichier se termine par un saut de ligne
    if [ -s "$BATOCERA_CONF" ] && [ -n "$(tail -c 1 "$BATOCERA_CONF")" ]; then
        echo >> "$BATOCERA_CONF"
    fi
    if [ "$CONF_HEADER_DONE" != 1 ]; then
        printf '\n# Préconfiguration linuxloader (install DCG)\n' >> "$BATOCERA_CONF"
        CONF_HEADER_DONE=1
    fi
    printf '%s\n' "$line" >> "$BATOCERA_CONF"
}

CONF_HEADER_DONE=0
CONF_ADDED=0
CONF_SKIPPED=0

TEMP_CONF="$SRC/temp.conf"
if [ -f "$TEMP_CONF" ]; then
    sed -i 's/\r$//' "$TEMP_CONF"
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|\#*) continue ;; esac
        if conf_add "$line"; then
            CONF_ADDED=$((CONF_ADDED + 1))
        else
            CONF_SKIPPED=$((CONF_SKIPPED + 1))
        fi
    done < "$TEMP_CONF"
else
    warn "temp.conf introuvable dans l'archive : batocera.conf non modifié"
fi

if [ "$CONF_ADDED" -gt 0 ]; then
    ok "$CONF_ADDED ligne(s) ajoutée(s) à batocera.conf"
else
    ok "batocera.conf déjà à jour"
fi
[ "$CONF_SKIPPED" -gt 0 ] && ok "$CONF_SKIPPED ligne(s) déjà présente(s), conservée(s) telles quelles"

# --- 7. Vérification -------------------------------------------------------
step "Vérification"
missing=0
for f in \
    system/dcg/configgen/dcglauncher \
    system/dcg/configgen/generators/linuxloader/linuxloaderGenerator.py \
    system/dcg/configgen/generators/wine/wineGenerator.py \
    system/dcg/configgen/generators/windows-arcade-loader/windowsArcadeLoaderGenerator.py \
    system/dcg/emulators/linuxloader/linuxloader \
    system/dcg/emulators/windows-arcade-loader/arcade-launcher \
    system/dcg/emulators/windows-arcade-loader/launcher.yaml \
    system/dcg/emulators/windows-arcade-loader/wine-prefix/full.tar.gz \
    system/configs/emulationstation/es_systems_teknoparrot.cfg \
    system/configs/emulationstation/es_features_linuxloader.cfg \
    system/configs/emulationstation/es_features_windowsarcadeloader.cfg; do
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

if [ "$missing" != 1 ]; then
    cp -f "$SRC/VERSION" "$DEST/system/dcg/VERSION" 2>/dev/null || true
fi

printf "\n"
if [ "$missing" = 1 ]; then
    warn "Installation terminée, mais des fichiers sont manquants dans l'archive."
else
    printf "${B}${G}✔ Installation terminée avec succès !${N}\n"
fi
printf "  Placez vos jeux dans : ${B}%s/roms/teknoparrot${N}\n" "$DEST"

# --- Remerciements ---------------------------------------------------------
printf "\n${B}${C}╔══════════════════════════════════════════════╗${N}\n"
printf   "${B}${C}║${N}              ${B}${Y}REMERCIEMENTS${N}                   ${B}${C}║${N}\n"
printf   "${B}${C}╠══════════════════════════════════════════════╣${N}\n"
printf   "${B}${C}║${N}                                              ${B}${C}║${N}\n"
printf   "${B}${C}║${N}   Un immense merci à :                       ${B}${C}║${N}\n"
printf   "${B}${C}║${N}                                              ${B}${C}║${N}\n"
printf   "${B}${C}║${N}      ${B}${G}@Spirit${N}                                 ${B}${C}║${N}\n"
printf   "${B}${C}║${N}      ${B}${G}@Psman69${N}                                ${B}${C}║${N}\n"
printf   "${B}${C}║${N}      ${B}${G}la Team TPN${N}                             ${B}${C}║${N}\n"
printf   "${B}${C}║${N}                                              ${B}${C}║${N}\n"
printf   "${B}${C}║${N}   pour leur travail et leur passion !        ${B}${C}║${N}\n"
printf   "${B}${C}║${N}                                              ${B}${C}║${N}\n"
printf   "${B}${C}╚══════════════════════════════════════════════╝${N}\n"
