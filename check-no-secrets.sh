#!/usr/bin/env bash
# check-no-secrets.sh — roda antes de cada push.
#
# Procura padrões de chave de API e nomes de arquivo sensíveis no que o git
# versionaria. Falha com código != 0 se encontrar algo, para poder entrar em
# hook de pre-push.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
FALHAS=0

echo "1) arquivos sensíveis versionados?"
for proibido in llm.env settings.local.json amostra.wav .env; do
  if git ls-files --error-unmatch "$proibido" >/dev/null 2>&1; then
    echo "  FALHA: $proibido está versionado"
    FALHAS=1
  fi
done
[ "$FALHAS" -eq 0 ] && echo "  OK"

echo "2) padrões de chave no conteúdo versionado?"
# sk-..., chaves longas em maiúsculas, tokens Bearer literais
PADROES='sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'
ACHADOS=$(git ls-files -z 2>/dev/null | xargs -0 grep -nEI "$PADROES" 2>/dev/null | grep -v 'COLOQUE_SUA_CHAVE\|sk-\.\.\.\|sk-xxx' || true)
if [ -n "$ACHADOS" ]; then
  echo "  FALHA: possível chave encontrada"
  echo "$ACHADOS" | sed 's/^/    /' | head -10
  FALHAS=1
else
  echo "  OK"
fi

echo "3) histórico com segredos?"
if git rev-parse HEAD >/dev/null 2>&1; then
  HIST=$(git log --all -p --no-color 2>/dev/null | grep -EI "$PADROES" | grep -v 'COLOQUE_SUA_CHAVE' | head -5 || true)
  if [ -n "$HIST" ]; then
    echo "  FALHA: segredo no histórico (precisa reescrever com git filter-repo)"
    echo "$HIST" | sed 's/^/    /'
    FALHAS=1
  else
    echo "  OK"
  fi
else
  echo "  OK (sem commits)"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "nenhum segredo detectado — seguro para push"
else
  echo "NÃO faça push. Resolva acima; se já vazou, rotacione a chave."
fi
exit "$FALHAS"
