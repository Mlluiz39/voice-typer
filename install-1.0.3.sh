#!/usr/bin/env bash
# install-1.0.3.sh — instala/atualiza o voice-typer e recarrega o serviço.
#
# O `daemon-reload` e o `restart` no fim não são opcionais: a unidade nova vai
# para /usr/lib/systemd/user/, mas o systemd só relê o arquivo depois do reload.
# Sem isso o serviço continua rodando com a unidade antiga em memória.
set -euo pipefail
cd "$(dirname "$0")"

DEB="voice-typer_1.0.3_amd64.deb"
[ -f "$DEB" ] || { echo "pacote $DEB não encontrado"; exit 1; }

echo "==> instalando $DEB"
sudo apt install -y "./$DEB"

echo "==> recarregando unidades do usuário"
systemctl --user daemon-reload

echo "==> reiniciando o serviço"
systemctl --user restart voice-typer || true
sleep 3

echo "==> estado"
echo "  versão instalada: $(dpkg-query -W -f='${Version}' voice-typer 2>/dev/null)"
echo "  serviço:          $(systemctl --user is-active voice-typer) / $(systemctl --user is-enabled voice-typer)"

echo "==> verificando log de partida (deve mostrar vocabulário, modelo e atalho)"
journalctl --user -u voice-typer --since '15 seconds ago' --no-pager 2>/dev/null \
  | grep -vE 'switched to group|returned to group' | tail -8

echo
echo "Pronto. Aperte Pause para ditar."
