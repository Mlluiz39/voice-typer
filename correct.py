#!/usr/bin/env python3
r"""correct.py — correções literais e regex aplicadas após a transcrição.

Existe porque alguns erros de ASR são **sistemáticos**: a mesma expressão sai
errada toda vez. Para esses, uma substituição literal é melhor que um LLM —
custo zero de latência, determinística e auditável.

Dois formatos, uma regra por linha (`#` comenta):

    e tem a vista que => tendo em vista que      # literal
    re:t(enho|endo)\s+em\s+vista => tendo em vista   # expressão regular

O formato literal casa com o texto exato e respeita fronteira de palavra. Isso o
torna seguro, mas limitado: medido no uso real, o mesmo erro de ASR **muda de
forma a cada rodada** (`e tem a vista que` -> `Tenho em vista que`), então
capturar variantes exige regex.

Regras literais são aplicadas antes das regex.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "corrections.txt"


def load_corrections(path: str | Path | None = DEFAULT_PATH) -> list[tuple[re.Pattern, str]]:
    """Lê o arquivo e compila cada par. Ignora linhas inválidas em silêncio.

    Linhas iniciadas por `re:` são tratadas como expressão regular; as demais são
    literais. Literais vêm primeiro na lista para terem precedência.
    """
    if path is None:
        return []
    p = Path(path)
    if not p.exists():
        return []

    literais: list[tuple[re.Pattern, str]] = []
    regexes: list[tuple[re.Pattern, str]] = []
    for linha in p.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=>" not in linha:
            continue
        errado, certo = linha.split("=>", 1)
        errado, certo = errado.strip(), certo.strip()
        if not errado:
            continue

        if errado.startswith("re:"):
            try:
                padrao = re.compile(errado[3:].strip(), re.IGNORECASE)
            except re.error:
                continue
            regexes.append((padrao, certo))
            continue

        # fronteira de palavra quando o termo começa/termina em caractere de palavra
        esq = r"\b" if re.match(r"\w", errado) else ""
        dir_ = r"\b" if re.search(r"\w$", errado) else ""
        try:
            padrao = re.compile(f"{esq}{re.escape(errado)}{dir_}", re.IGNORECASE)
        except re.error:
            continue
        literais.append((padrao, certo))

    # literais primeiro (seguros), e dentro de cada grupo os padrões mais longos
    # antes, para "a b" não comer "a b c"
    literais.sort(key=lambda r: len(r[0].pattern), reverse=True)
    regexes.sort(key=lambda r: len(r[0].pattern), reverse=True)
    return literais + regexes


def _replace(padrao: re.Pattern, certo: str, text: str) -> tuple[str, int]:
    """Substitui preservando a maiúscula inicial e sem contar no-op.

    Preservar a caixa importa: em início de frase a correção trocava
    "Tendo em vista que" por "tendo em vista que", estragando o texto.
    """
    disparos = 0

    def repl(m: re.Match) -> str:
        nonlocal disparos
        achado = m.group(0)
        novo = certo
        if achado[:1].isupper() and novo[:1].islower():
            novo = novo[:1].upper() + novo[1:]
        if novo != achado:
            disparos += 1
        return novo

    return padrao.sub(repl, text), disparos


def apply_corrections(text: str, regras: list[tuple[re.Pattern, str]]) -> tuple[str, int]:
    """Aplica as regras. Devolve o texto corrigido e quantas regras dispararam."""
    if not text or not regras:
        return text, 0
    total = 0
    for padrao, certo in regras:
        text, n = _replace(padrao, certo, text)
        total += n
    return text, total


def main(argv: list[str]) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Aplica correções literais a um texto.")
    ap.add_argument("text", nargs="+", help="texto a corrigir")
    ap.add_argument("--file", default=str(DEFAULT_PATH))
    args = ap.parse_args(argv)

    regras = load_corrections(args.file)
    if not regras:
        print(f"nenhuma regra carregada de {args.file}", file=sys.stderr)
        return 1
    texto = " ".join(args.text)
    corrigido, n = apply_corrections(texto, regras)
    print(f"regras: {len(regras)}  disparos: {n}")
    print(f"antes:    {texto}")
    print(f"depois:   {corrigido}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
