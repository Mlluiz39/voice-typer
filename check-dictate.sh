#!/usr/bin/env bash
# check-dictate.sh — verifica o passo 3 (atalho -> gravação -> transcrição -> injeção).
#
# Camadas:
#   1. testes unitários de todas as partes (sem GUI, sem rede, sem microfone)
#   2. acesso a /dev/uinput, necessário ao teclado sintético do teste
#   3. ponta a ponta do atalho com texto fixo (não precisa do modelo)
#   4. ponta a ponta com transcrição real, se houver amostra.wav
#
# A camada 4 usa --dry-run: valida o caminho de transcrição sem tocar nos seus
# aplicativos.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
FAIL=0
PY=".venv/bin/python"
hr() { printf '%s\n' "----------------------------------------"; }

echo "1) testes unitários"
hr
for t in test_inject_unit.py test_dictate_unit.py test_record_unit.py test_stt_unit.py test_correct_unit.py test_refine_unit.py; do
  if [ -f "$t" ]; then
    if "$PY" "$t" 2>&1 | tail -3 | grep -q '^OK$'; then
      printf '  PASS  %s\n' "$t"
    else
      printf '  FAIL  %s\n' "$t"; FAIL=1
    fi
  fi
done

echo
echo "2) /dev/uinput"
hr
if [ -w /dev/uinput ]; then
  echo "  PASS  gravável"
else
  echo "  FAIL  sem escrita em /dev/uinput (grupo input?)"; FAIL=1
  hr; exit "$FAIL"
fi

echo
echo "3) atalho com texto fixo (sem modelo)"
hr
LOG=$(mktemp)
timeout 25 "$PY" fakekey.py pause --count 2 --interval 1.2 --delay-before 2.5 >/dev/null 2>&1 &
FK=$!
sleep 0.4
timeout 20 python3 dictate.py --once --text 'verificação do passo 3' --dry-run --no-sound >"$LOG" 2>&1
RC=$?
wait "$FK" 2>/dev/null
if [ "$RC" -eq 0 ] && grep -q 'verificação do passo 3' "$LOG"; then
  echo "  PASS  toggle e injeção alcançados"
else
  echo "  FAIL  ciclo incompleto (rc=$RC)"; sed 's/^/        /' "$LOG"; FAIL=1
fi
rm -f "$LOG"

echo
echo "4) transcrição real"
hr
if [ ! -f amostra.wav ]; then
  echo "  SKIP  sem amostra.wav (grave com: python3 record.py --seconds 12 --out amostra.wav)"
else
  LOG=$(mktemp)
  timeout 35 "$PY" fakekey.py pause --count 2 --interval 1.5 --delay-before 5 >/dev/null 2>&1 &
  FK=$!
  sleep 0.4
  timeout 30 python3 dictate.py --once --audio-file amostra.wav --dry-run --no-sound >"$LOG" 2>&1
  RC=$?
  wait "$FK" 2>/dev/null
  if [ "$RC" -eq 0 ] && grep -q 'texto:' "$LOG" && grep -q 'dry-run' "$LOG"; then
    echo "  PASS  transcreveu e chegou à injeção"
    grep 'texto:' "$LOG" | sed 's/^/        /'
  else
    echo "  FAIL  transcrição não completou (rc=$RC)"; sed 's/^/        /' "$LOG"; FAIL=1
  fi
  rm -f "$LOG"
fi

hr
if [ "$FAIL" -eq 0 ]; then echo "tudo passou"; else echo "houve falhas"; fi
exit "$FAIL"
