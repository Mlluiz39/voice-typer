#!/usr/bin/env python3
r"""refine.py — refino de texto por LLM, opcional e à prova de falha.

Por que existe: medido em uso real, o `small` erra de forma **imprevisível** —
"enorte" por "erro", "e tem a vista que" numa rodada e "Tenho em vista que" na
seguinte, minúscula depois de ponto final. Regra literal não cobre variante que
muda; o LLM julga pelo contexto.

Princípios de projeto:

1. **Falha aberta.** Qualquer erro, timeout ou resposta suspeita devolve o texto
   original. Nunca perder a ditada por causa do refino.
2. **Proteção contra reescrita.** Se a resposta for vazia, ou mudar o tamanho do
   texto por mais de ~50%, é descartada. O refino corrige; ele não resume nem
   reescreve. Esse guarda existe porque apagar conteúdo é o pior defeito possível
   numa camada de polimento.
3. **Compatível com OpenAI**, o que cobre DeepSeek, OpenAI, Groq, Ollama e
   llama.cpp servido — sem dependência extra (só stdlib).

Configuração por ambiente:
    VT_LLM_BASE_URL   (padrão https://api.deepseek.com)
    VT_LLM_MODEL      (padrão deepseek-chat)
    VT_LLM_API_KEY    (obrigatória para serviços remotos)
    VT_LLM_TIMEOUT           (padrão 20 segundos)
    VT_LLM_DISABLE_REASONING (padrão 1: envia reasoning.enabled=false)

Sobre raciocínio: medido neste roteador, o mesmo modelo gastou 1403-2642 tokens
"pensando" e chegou a 19-30 s por refino — quando o orçamento de tokens acabava
antes da resposta, vinha `content: null` e `finish_reason: length`. Enviando
`reasoning: {"enabled": false}` a mesma tarefa caiu para ~2 s com
`reasoning_tokens: 0` e qualidade igual ou melhor. Para corrigir texto não há o
que raciocinar.

Exemplos:
    # Ollama local (offline)
    VT_LLM_BASE_URL=http://127.0.0.1:11434/v1 VT_LLM_MODEL=qwen2.5:3b python3 dictate.py --refine

    # DeepSeek
    VT_LLM_API_KEY=sk-... python3 dictate.py --refine
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

# O prompt permite trocar palavra que não existe — sem isso, "enorte" (erro de
# reconhecimento) ficava intocado, porque a instrução original proibia mudar
# palavras. Medido: com a versão restritiva, o LLM só ajustava pontuação.
SYSTEM_PROMPT = (
    "Você revisa transcrições de ditado em português do Brasil. "
    "Corrija: ortografia; palavras que não existem em português ou que são "
    "claramente erro de reconhecimento de voz (troque pela palavra provável no "
    "contexto); concordância; pontuação; e maiúsculas no início de frase. "
    "Não reescreva o estilo, não resuma, não acrescente ideias e não remova "
    "informação. Preserve o vocabulário e o registro do falante. "
    "Responda apenas com o texto corrigido, sem comentários e sem aspas."
)

# Guardas contra reescrita: o refino não pode mudar o tamanho do texto assim.
MIN_RATIO = 0.55
MAX_RATIO = 1.60


def _extract_content(texto: str) -> str:
    """Extrai o texto tanto de resposta JSON quanto de stream SSE.

    Alguns roteadores ignoram `stream: false` e respondem em SSE
    (`data: {...}` por linha). Tratar só JSON quebrava silenciosamente com
    "resposta inesperada".
    """
    texto = texto.strip()
    if texto.startswith("data:"):
        partes: list[str] = []
        for linha in texto.splitlines():
            linha = linha.strip()
            if not linha.startswith("data:"):
                continue
            corpo = linha[5:].strip()
            if not corpo or corpo == "[DONE]":
                continue
            try:
                evento = json.loads(corpo)
            except json.JSONDecodeError:
                continue
            for escolha in evento.get("choices") or []:
                delta = escolha.get("delta") or {}
                if delta.get("content"):
                    partes.append(delta["content"])
                msg = escolha.get("message") or {}
                if msg.get("content") and not partes:
                    partes.append(msg["content"])
        return "".join(partes)

    corpo = json.loads(texto)
    return corpo["choices"][0]["message"]["content"]


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v else default


# Sinais de que a transcrição merece refino. Servem para não pagar 6-7 s de
# latência em toda ditada: só refina quando há indício de problema.
_SINAIS = (
    (re.compile(r"[.!?]\s+[a-zà-ÿ]"), "minúscula depois de pontuação final"),
    (re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE), "palavra repetida"),
    (re.compile(r"\s{2,}"), "espaço duplicado"),
    (re.compile(r"\b\w*[bcdfghjklmnpqrstvwxz]{4,}\w*\b", re.IGNORECASE), "sequência consonantal improvável"),
)

# Acima deste tamanho vale refinar mesmo sem sinal.
# Calibração original (com raciocínio ligado, 6-19 s por chamada): o limiar era
# 300, para não pagar caro em texto limpo. Depois de desligar o raciocínio o
# custo caiu para ~2 s, então o limiar desceu: agora vale refinar quase toda
# ditada real. Para forçar em tudo, `--refine always`.
LONG_TEXT_CHARS = 150


def needs_refine(text: str, long_text_chars: int = LONG_TEXT_CHARS) -> tuple[bool, str]:
    """Heurística: vale a pena refinar este texto?

    Medido: o refino custa 6-7 s neste roteador. Aplicar em toda ditada
    triplicaria a espera; aplicar só quando há sinal mantém a responsividade e
    ainda pega os casos ruins.
    """
    if not text.strip():
        return False, "vazio"
    if len(text) >= long_text_chars:
        return True, f"texto longo ({len(text)} caracteres)"
    for padrao, motivo in _SINAIS:
        if padrao.search(text):
            return True, motivo
    return False, "sem sinais"


class Refiner:
    """Cliente mínimo de chat completions, com falha aberta."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        transport=None,
    ) -> None:
        self.base_url = (base_url or _env("VT_LLM_BASE_URL", "https://api.deepseek.com") or "").rstrip("/")
        self.model = model or _env("VT_LLM_MODEL", "deepseek-chat") or "deepseek-chat"
        self.api_key = api_key or _env("VT_LLM_API_KEY")
        self.timeout = timeout if timeout is not None else float(_env("VT_LLM_TIMEOUT", "20") or 20)
        self.disable_reasoning = (_env("VT_LLM_DISABLE_REASONING", "1") or "1") not in ("0", "false", "no")
        # `transport` permite testar sem rede: recebe (messages) -> str
        self._transport = transport or self._http_transport

    # ------------------------------------------------------------------ HTTP
    def _http_transport(self, messages: list[dict]) -> str:
        url = f"{self.base_url}/chat/completions"
        corpo: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            # 2048 não bastava quando o modelo raciocinava: o raciocínio consumia
            # o orçamento e o texto nunca saía.
            "max_tokens": 4096,
            "stream": False,
        }
        if self.disable_reasoning:
            corpo["reasoning"] = {"enabled": False}
        payload = json.dumps(corpo).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            texto = resp.read().decode("utf-8", "replace")
        return _extract_content(texto)

    # --------------------------------------------------------------- refino
    def refine(self, text: str) -> tuple[str, bool, str]:
        """Devolve (texto_final, refinou, motivo).

        Em qualquer problema devolve o texto original com refinou=False.
        """
        if not text.strip():
            return text, False, "texto vazio"
        if not self.api_key and "127.0.0.1" not in self.base_url and "localhost" not in self.base_url:
            return text, False, "sem VT_LLM_API_KEY"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        try:
            bruto = self._transport(messages)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return text, False, f"falha de rede: {exc}"
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            return text, False, f"resposta inesperada: {exc}"

        novo = (bruto or "").strip().strip('"').strip()
        if not novo:
            return text, False, "resposta vazia"

        razao = len(novo) / max(1, len(text))
        if not (MIN_RATIO <= razao <= MAX_RATIO):
            return text, False, f"tamanho suspeito (razão {razao:.2f})"

        if novo == text:
            return text, False, "sem alterações"
        return novo, True, "ok"

    @property
    def target(self) -> str:
        return f"{self.base_url} / {self.model}"


def main(argv: list[str]) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Refina um texto por LLM (salvaguardas ativas).")
    ap.add_argument("text", nargs="+")
    ap.add_argument("--base-url")
    ap.add_argument("--model")
    args = ap.parse_args(argv)

    r = Refiner(base_url=args.base_url, model=args.model)
    texto = " ".join(args.text)
    novo, ok, motivo = r.refine(texto)
    print(f"alvo:   {r.target}")
    print(f"refinou: {ok}  ({motivo})")
    print(f"antes:  {texto}")
    if ok:
        print(f"depois: {novo}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
