# Contribuindo

Obrigado pelo interesse. Este documento explica como preparar o ambiente, rodar
as verificações e o que observar antes de abrir um pull request.

## Ambiente

```bash
git clone <repo> && cd voice-typer
uv venv .venv --python /usr/bin/python3.12
uv pip install --python .venv/bin/python faster-whisper

# acesso a /dev/input para o atalho global (refaça o login depois)
sudo usermod -aG input "$USER"
```

O comando `voice-typer --doctor` verifica se tudo está no lugar antes de você
começar a investigar qualquer coisa.

A interface gráfica usa o **Python do sistema** (PyGObject/GTK3), não o venv — o
venv existe para o faster-whisper. Para testá-la:

```bash
python3 voice_typer_gui.py --settings
```

## Antes de abrir PR

```bash
./check-dictate.sh      # 68 testes + ponta a ponta
./check-no-secrets.sh   # nenhum segredo versionado
```

`check-dictate.sh` cobre quatro camadas: testes unitários, `/dev/uinput`, atalho
com texto fixo e transcrição real (essa última é pulada se não houver
`amostra.wav`).

Se você mexeu na interface, regere as imagens do README:

```bash
python3 docs/capture-screenshots.py
```

## Como os testes são organizados

| Arquivo | Cobre |
| --- | --- |
| `test_inject_unit.py` | normalização de texto, fatiamento, escolha de backend |
| `test_dictate_unit.py` | máquina de estados do atalho (autorepeat, bounce) |
| `test_record_unit.py` | classificação de áudio (mudo / saturado / ok) |
| `test_stt_unit.py` | decodificação de WAV e conversão |
| `test_correct_unit.py` | correções literais e regex, preservação de caixa |
| `test_refine_unit.py` | refino com transporte falso, todas as salvaguardas |

Testes que dependem de GUI ou de foco de janela são intencionalmente separados nos
scripts `check-*`, porque não são determinísticos. `fakekey.py` cria um teclado
sintético via `uinput` para testar o atalho sem precisar de alguém apertando teclas.

## Princípios do projeto

**Falha aberta em tudo que é opcional.** Se o refino falhar, o texto corrigido por
regra é injetado. Se a colagem falhar, tenta digitar. Uma ditada nunca deve ser
perdida por causa de uma camada de qualidade.

**Medir antes de otimizar.** Várias decisões aqui contrariam a intuição: 4 threads
é melhor que 8; o modelo `medium` é dominado pelos vizinhos; digitar perde
caracteres onde colar não perde. Cada uma está no README com o número que a
sustenta. Se for mudar uma, traga a medição.

**Config pessoal não entra no repositório.** `vocabulary.txt`, `corrections.txt` e
`llm.env` do projeto são templates. Termos e chaves do usuário ficam em
`~/.config/voice-typer/` e são combinados na execução.

**Não roubar foco.** Nada que apareça na tela durante a gravação pode receber foco:
o texto iria para o overlay em vez do campo do usuário. A bandeja existe por isso.

## Reportando problemas

Inclua a saída de:

```bash
voice-typer --doctor
```

E, se for sobre transcrição, a linha correspondente de
`~/.local/state/voice-typer/dictations.jsonl` (ela mostra transcrição, método
usado, se o refino aplicou e por quê). **Remova dados pessoais antes de colar** —
o campo `transcript` contém o que você ditou.

Para problemas de áudio, `voice-typer --keep-audio /tmp/ditada.wav` preserva a
captura para análise.

## Licença

Ao contribuir, você concorda que sua contribuição é licenciada sob a MIT, como o
resto do projeto.
