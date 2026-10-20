#!/usr/bin/env bash
# build.sh — monta o .deb do voice-typer.
#
# O venv é embutido no pacote, construído contra o Python do sistema
# (/usr/bin/python3.12) e copiado para o caminho final. Venv é relocável desde
# que se invoque `.venv/bin/python` diretamente (não os scripts de `bin/`, cujos
# shebangs apontam para o caminho de build).
set -euo pipefail

VERSAO="${1:-1.0.0}"
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

DEST="$STAGE/opt/voice-typer"
mkdir -p "$DEST" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/lib/systemd/user" \
         "$STAGE/DEBIAN"

echo "1/5 copiando código"
for f in dictate.py inject.py stt.py record.py refine.py correct.py detect_key.py \
         fakekey.py bench.py voice_typer_gui.py settings.json vocabulary.txt \
         corrections.txt README.md; do
  cp "$PROJ/$f" "$DEST/"
done
cp "$PROJ/llm.env" "$DEST/llm.env.example" 2>/dev/null || :
# a chave nunca vai no pacote
sed -i 's/^VT_LLM_API_KEY=.*/VT_LLM_API_KEY=COLOQUE_SUA_CHAVE_AQUI/' "$DEST/llm.env.example" 2>/dev/null || :

echo "2/5 preparando venv (Python do sistema)"
if [ ! -x "$PROJ/.pkgvenv/bin/python" ]; then
  uv venv --python /usr/bin/python3.12 "$PROJ/.pkgvenv" -q
  uv pip install --python "$PROJ/.pkgvenv/bin/python" faster-whisper -q
fi
cp -a "$PROJ/.pkgvenv" "$DEST/.venv"
# limpar caches para reduzir tamanho
find "$DEST/.venv" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || :

echo "3/5 launchers, desktop e ícone"
cat > "$STAGE/usr/bin/voice-typer" <<'LAUNCH'
#!/bin/sh
exec /opt/voice-typer/.venv/bin/python -u /opt/voice-typer/dictate.py "$@"
LAUNCH
cat > "$STAGE/usr/bin/voice-typer-gui" <<'LAUNCH'
#!/bin/sh
# GUI usa o Python do SISTEMA (PyGObject/GTK3 não estão no venv)
exec /usr/bin/python3 /opt/voice-typer/voice_typer_gui.py "$@"
LAUNCH
chmod 755 "$STAGE/usr/bin/voice-typer" "$STAGE/usr/bin/voice-typer-gui"

cp "$PROJ/packaging/assets/voice-typer.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/voice-typer.svg"
cp "$PROJ/packaging/assets/voice-typer.desktop" "$STAGE/usr/share/applications/voice-typer.desktop"

echo "4/5 unidade systemd de usuário"
sed "s|@PREFIX@|/opt/voice-typer|g" "$PROJ/packaging/assets/voice-typer.service" \
  > "$STAGE/usr/lib/systemd/user/voice-typer.service"

echo "4b/5 normalizando permissões"
# dpkg não gosta de arquivos graváveis pelo grupo, e o exemplo precisa ser legível
chmod -R go-w "$STAGE"
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -name '*.py' -exec chmod 755 {} +
find "$STAGE" -type f \( -name '*.txt' -o -name '*.json' -o -name '*.md' -o -name '*.svg' -o -name '*.desktop' -o -name '*.service' -o -name '*.example' \) -exec chmod 644 {} +
chmod 755 "$STAGE/usr/bin/voice-typer" "$STAGE/usr/bin/voice-typer-gui"
chmod 644 "$STAGE/usr/lib/systemd/user/voice-typer.service" \
          "$STAGE/usr/share/applications/voice-typer.desktop" \
          "$STAGE/usr/share/icons/hicolor/scalable/apps/voice-typer.svg"

echo "5/5 controle do pacote"
TAM=$(du -sk "$STAGE" | cut -f1)
sed -e "s|@VERSAO@|$VERSAO|" -e "s|@TAMANHO@|$TAM|" \
    "$PROJ/packaging/DEBIAN/control.in" > "$STAGE/DEBIAN/control"
for f in postinst prerm postrm; do
  cp "$PROJ/packaging/DEBIAN/$f" "$STAGE/DEBIAN/$f"
  chmod 755 "$STAGE/DEBIAN/$f"
done

SAIDA="$PROJ/voice-typer_${VERSAO}_amd64.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$SAIDA" >/dev/null
echo
echo "gerado: $SAIDA ($(du -h "$SAIDA" | cut -f1))"
