# 🧠 Pocket Brainy

Bot de trading automatizado para **Pocket Option** com interface 100% por **Telegram**, IA de validação via **DeepSeek**, 8 estratégias configuráveis, ranking dinâmico, filtro de mercado lateral, martingale e reentrada inteligente.

> ⚠️ **Aviso legal**: este software executa operações financeiras de alto risco. A Pocket Option não possui API oficial — o cliente usado é não-oficial e pode quebrar a qualquer momento. Use **sempre** em modo simulação ou conta demo antes de operar com dinheiro real.  O uso é de inteira responsabilidade do operador.

---

## 📂 Estrutura do projeto

```
PocketBOT/
├── main.py                       # Ponto de entrada
├── requirements.txt
├── README.md
└── pocket_brainy/
    ├── core/                     # Configuração, estado e orquestrador
    ├── strategies/               # 8 estratégias + manager + ranking
    ├── telegram/                 # UI Telegram (InlineKeyboards)
    ├── broker/                   # Abstração Pocket Option + sessão
    ├── risk/                     # Stop win/loss, martingale, streak
    ├── ai/                       # Integração DeepSeek
    ├── utils/                    # Indicadores, filtro de mercado, logger
    └── data/                     # JSON: config, estratégias, ranking, histórico, sessão
```

---

## 🛠️ Pré-requisitos (macOS)

- macOS 12+ (Intel ou Apple Silicon)
- Python 3.11+
- Homebrew (recomendado)

```bash
# Se não tiver ainda:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.11
```

---

## 🚀 Instalação

```bash
cd ~/Downloads/PocketBOT

# Ambiente virtual (recomendado)
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip

# 1) Dependências principais (as versões são permissivas — funcionam em Python 3.11, 3.12 e 3.13)
pip install -r requirements.txt

# 2) Pocket Option (não-oficial, traz várias deps compartilhadas)
pip install git+https://github.com/ChipaDevTeam/PocketOptionAPI.git

# 3) Playwright (necessário para login por email/senha)
python -m playwright install chromium
```

> ❗ Se você já rodou os comandos acima e viu erro do numpy 1.26.4 tentando compilar, rode APENAS:
> ```bash
> pip install "python-telegram-bot>=21.0" "playwright>=1.42" tenacity
> python -m playwright install chromium
> ```
> As outras dependências já foram instaladas pelo pacote `pocketoptionapi-async`.

---

## ⚙️ Configuração

Na primeira execução, um arquivo `pocket_brainy/data/config.json` será criado automaticamente com valores padrão. Edite os campos obrigatórios:

```json
{
  "po_email": "seu@email.com",
  "po_password": "sua-senha",
  "po_demo": true,

  "telegram_token": "123456:ABC...",
  "telegram_chat_id": "123456789",

  "entry_amount": 2.0,
  "stop_win": 50.0,
  "stop_loss": 30.0,
  "min_payout": 80.0,
  "min_assertiveness": 65.0,
  "timeframes": ["M1", "M5"],
  "asset_mode": "auto",
  "simulation_mode": true,
  "ai_enabled": true,
  ...
}
```

Como obter o **Telegram Token**:
1. Fale com o [@BotFather](https://t.me/botfather).
2. `/newbot`, escolha um nome e handle.
3. Copie o token retornado.

Como obter o **Chat ID**:
1. Envie uma mensagem para o seu novo bot.
2. Acesse `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. Copie o valor de `chat.id`.

Quase todas as configurações também podem ser feitas **pelos botões** do Telegram (/menu).

---

## ▶️ Como rodar

```bash
cd ~/Downloads/PocketBOT
source .venv/bin/activate     # se usou venv
python main.py
```

No Telegram, envie:

```
/start
```

Menu principal:
- ▶️ Iniciar Bot
- ⏹️ Parar Bot
- ⚙️ Configurações
- 🧠 Estratégias
- 📊 Status
- 📈 Resultados
- 🏆 Ranking
- 🔄 Reconectar

---

## 🧠 Estratégias incluídas

| # | Estratégia            | Pesos (M1 / M5 / M15) |
|---|-----------------------|------------------------|
| 1 | RSI + EMA             | 1.0 / 1.2 / 1.3        |
| 2 | Alligator + RSI + MACD| 0.9 / 1.3 / 1.4        |
| 3 | Suporte / Resistência | 0.8 / 1.5 / 1.5        |
| 4 | MHI (3 velas)         | 1.5 / 1.0 / 0.8        |
| 5 | MACD + Parabolic SAR  | 1.0 / 1.2 / 1.3        |
| 6 | Bollinger + RSI       | 1.3 / 1.3 / 1.1        |
| 7 | Multi-Filtro (EMA50+EMA9/21+RSI+MACD+ADX) | 0.9 / 1.4 / 1.5 |
| 8 | Breakout              | 0.9 / 1.3 / 1.4        |

### Sistema de score
`score_final = score_base × peso_timeframe + bônus_ranking`

Confluência: RSI +1, EMA +1, MACD +1, Tendência +2, ADX forte +1.

### Ranking dinâmico
Após ≥5 operações reais, estratégias com winrate ≥ 65% ganham bônus (até +2.0 pts).
Estratégias com winrate < 50% sofrem penalização (até -2.0 pts).

### Filtro anti-loss (mercado lateral)
Quando `ADX < 20` **e** largura das Bollinger estreita:
- Bloqueia: RSI+EMA, Alligator, MACD+SAR, MultiFiltro, Breakout.
- Permite: MHI, Bollinger+RSI, Suporte/Resistência.

### Reentrada inteligente (não-martingale cego)
Após LOSS, o bot **não entra imediatamente**: aguarda um novo sinal que passe
por todos os critérios (score mínimo, estratégia válida, confirmação da IA).

---

## 🤖 IA (DeepSeek)

- Endpoint: `https://api.deepseek.com/chat/completions`
- Chave padrão hardcoded conforme solicitado (ver `pocket_brainy/ai/deepseek.py → get_api_key()`).
- Pode ser sobrescrita via `export DEEPSEEK_API_KEY=sk-...` antes de rodar.

A IA valida cada sinal **antes** da execução e retorna:
- Decisão: `OPERAR` ou `IGNORAR`
- Confiança (0–100%)
- Justificativa curta

---

## 📁 Persistência

| Arquivo                               | Conteúdo                          |
|---------------------------------------|-----------------------------------|
| `pocket_brainy/data/config.json`      | Todas as configurações            |
| `pocket_brainy/data/strategies.json`  | Toggle ativo/inativo por estratégia|
| `pocket_brainy/data/ranking.json`     | Performance histórica             |
| `pocket_brainy/data/history.json`     | Histórico completo de operações   |
| `pocket_brainy/data/session.json`     | Sessão da Pocket Option (SSID)    |
| `pocket_brainy/data/logs/*.log`       | Logs rotativos                    |

---

## 🧪 Modo simulação

Com `simulation_mode: true`, o bot roda todo o pipeline (análise + IA + ranking),
mas **não envia ordens reais** — os resultados são simulados via probabilidade
proporcional à confiança. Ideal para testar configurações antes de usar real.

---

## 🧯 Troubleshooting

- **"Não foi possível capturar SSID"**: revise email/senha ou rode `python -m playwright install chromium`. Se continuar falhando, use o modo mock (qualquer credencial inválida aciona fallback automático) e habilite `simulation_mode`.
- **Telegram não responde**: verifique token e chat_id em `config.json`.
- **DeepSeek retornando erro**: se não tiver créditos na chave hardcoded, exporte sua própria chave com `export DEEPSEEK_API_KEY=...`.

---

## 📜 Licença

MIT — use por sua conta e risco.
