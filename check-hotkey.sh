#!/usr/bin/env bash
# check-hotkey.sh — verifica o passo 2 (atalho global com toggle).
#
# Camadas:
#   1. testes unitários da máquina de estados (sem evdev)
#   2. acesso a /dev/uinput (necessário para o teclado sintético do teste)
#   3. ponta a ponta: daemon sobe, teclado sintético envia Pause duas vezes,
#      o daemon alterna gravar/parar e tenta injetar (em --dry-run, sem tocar
#      nos seus aplicativos)
set -uo pipefail
cd "$(dirname "$0")" || exit 1
FAIL=0
hr() { printf '%s\n' "----------------------------------------"; }

echo "1) máquina de estados"
hr
if python3 test_dictate_unit.py 2>&1 | tail -3 | grep -q '^OK$'; then
  echo "  PASS  testes unitários"
else
  echo "  FAIL  testes unitários"
  FAIL=1
fi

echo
echo "2) /dev/uinput"
hr
if [ -w /dev/uinput ]; then
  echo "  PASS  gravável (dá para sintetizar teclas nos testes)"
else
  echo "  FAIL  sem escrita em /dev/uinput (grupo input?)"
  echo "        o teste ponta a ponta será pulado"
  FAIL=1
  hr
  exit "$FAIL"
fi

echo
echo "3) ponta a ponta com teclado sintético"
hr
LOG=$(mktemp)
timeout 30 python3 -u fakekey.py pause --count 2 --interval 1.5 --delay-before 3 >/dev/null 2>&1 &
FK=$!
sleep 0.8
timeout 25 python3 -u dictate.py --once --dry-run --no-sound --text 'verificação do atalho' >"$LOG" 2>&1
RC=$?
wait "$FK" 2>/dev/null

if [ "$RC" -ne 0 ]; then
  echo "  FAIL  daemon saiu com código $RC"
  FAIL=1
elif grep -q 'gravando' "$LOG" && grep -q 'injetaria' "$LOG"; then
  echo "  PASS  toggle: iniciou e parou"
  echo "  PASS  injeção alcançada (dry-run)"
else
  echo "  FAIL  o daemon não alternou os dois estados"
  sed 's/^/        /' "$LOG"
  FAIL=1
fi
rm -f "$LOG"

hr
if [ "$FAIL" -eq 0 ]; then
  echo "tudo passou"
else
  echo "houve falhas"
fi
exit "$FAIL"
