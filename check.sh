#!/usr/bin/env bash
# check.sh — verificação do injetor em três camadas.
#
#   1. testes unitários da lógica pura (determinísticos, sem GUI)
#   2. diagnóstico do ambiente (wtype, wl-copy, protocolo do compositor)
#   3. captura real: abre janela descartável, injeta, confere o que chegou
#
# A camada 3 depende de foco de janela, que é do ambiente e não do código.
# Se ela falhar, confira se a janela abriu na frente e rode de novo.
set -uo pipefail

cd "$(dirname "$0")" || exit 1
KITTY="${KITTY:-$HOME/.local/bin/kitty}"
FAIL=0

hr() { printf '%s\n' "----------------------------------------"; }

# ---------------------------------------------------------------- 1. unitários
echo "1) lógica pura"
hr
if python3 test_inject_unit.py 2>&1 | tail -3 | grep -q '^OK$'; then
  echo "  PASS  testes unitários"
else
  echo "  FAIL  testes unitários"
  FAIL=1
fi

# ------------------------------------------------------------- 2. diagnóstico
echo
echo "2) ambiente"
hr
if ./inject.py --check; then
  echo "  PASS  ambiente"
else
  echo "  FAIL  ambiente"
  FAIL=1
fi

# ---------------------------------------------------------- 3. captura real
# Abre uma janela nova, injeta um caso, lê o que a janela capturou.
# Sempre mata por PID: `pkill -f padrao` casaria com a linha de comando
# deste próprio script e o mataria.
capture_case() {
  local name="$1" expected="$2"; shift 2
  local out="/tmp/vt-capture-$$.txt"
  local before after got

  rm -f "$out"
  "$KITTY" --title "VTCHECK$$" sh -c \
    "IFS= read -r line; printf '%s' \"\$line\" > '$out'" 2>/dev/null &
  local pid=$!
  sleep 4   # mapear a janela e receber foco

  if ! timeout 25 ./inject.py "$@" >/dev/null 2>&1; then
    echo "  FAIL  $name (injetor retornou erro)"
    kill -TERM "$pid" 2>/dev/null
    FAIL=1
    return
  fi

  wtype -k Return 2>/dev/null
  sleep 1.5
  got=$(cat "$out" 2>/dev/null || true)
  kill -TERM "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  rm -f "$out"
  sleep 1.5  # deixa o compositor reacomodar o foco antes do próximo caso

  if [ "$got" = "$expected" ]; then
    echo "  PASS  $name"
  else
    echo "  FAIL  $name"
    printf '        esperado: %s\n        obtido:   %s\n' "$expected" "$got"
    FAIL=1
  fi
}

echo
echo "3) captura real em janela terminal"
hr
capture_case "type com acentuação" "coração não você ação" --method type 'coração não você ação'
capture_case "type com símbolos"   "— “aspas” 100% 😀"     --method type '— “aspas” 100% 😀'
capture_case "paste em terminal"   "colado: ação 100%"     --method paste --terminal 'colado: ação 100%'

hr
if [ "$FAIL" -eq 0 ]; then
  echo "tudo passou"
else
  echo "houve falhas (se foram só na camada 3, verifique o foco da janela)"
fi
exit "$FAIL"
