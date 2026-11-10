# TokenScope — Projeto 1

Dashboard local para explorar o CSV de atividade do OpenRouter do Projeto 1.
O backend usa Flask e pandas para preparar e filtrar os registros; os gráficos
são gerados com Plotly. A aplicação não envia dados a uma API nem possui upload
de arquivo.

## Executar no Windows

No PowerShell, a partir desta pasta:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Abra <http://127.0.0.1:5000>. O servidor local é iniciado em modo de depuração
quando `app.py` é executado diretamente; esse modo é destinado ao desenvolvimento,
não à publicação em produção.

## Estrutura do projeto

| Caminho | Responsabilidade |
| --- | --- |
| `app.py` | Configuração Flask, leitura e normalização do CSV, filtros, métricas e gráficos. |
| `templates/index.html` | Estrutura da página Jinja, formulário, indicadores, gráficos, tabela e paginação; também inicializa os ajustes de visualização dos gráficos no navegador. |
| `static/style.css` | Estilos, cores, disposição dos componentes e comportamento responsivo. |
| `data/openrouter_activity_2026-08-31.csv` | Fonte local dos registros de uso. |
| `requirements.txt` | Dependências Python do aplicativo. |

## Fluxo dos dados

1. `GET /` chama `load_data()`, que abre o CSV indicado por `CSV_FILE`, verifica
   as colunas obrigatórias e normaliza datas, números e valores textuais ausentes.
2. `filter_data()` aplica os parâmetros recebidos na URL. O formulário permite
   filtrar por início, fim, provedor e modelo. As datas são inclusivas; uma data
   final inclui o dia inteiro. Datas inválidas são ignoradas.
3. O backend calcula totais, médias e intervalo de datas para os registros
   filtrados. Tokens totais são a soma de entrada e saída; cache e raciocínio
   aparecem como indicadores separados.
4. `build_charts()` agrega os dados por dia, modelo e provedor e gera os
   fragmentos Plotly. A página apresenta o histórico mais recente primeiro,
   paginado em 15 registros por página. Os links de paginação preservam os filtros.
5. `index.html` combina esses valores com o template e `style.css` apresenta a
   interface responsiva. O navegador carrega Plotly.js pela CDN do Plotly e
   aplica os ajustes visuais dos gráficos de custo e provedor. O gráfico de
   modelos é transformado em barras HTML acessíveis depois que o Plotly renderiza.

## CSV esperado

O caminho é definido pela constante `CSV_FILE` no início de `app.py`. O arquivo
precisa conter estas colunas:

- `created_at`: data e hora do registro;
- `generation_id`: identificador da geração;
- `tokens_prompt` e `tokens_completion`: tokens de entrada e saída;
- `model_permaslug` e `provider_name`: modelo e provedor;
- `cost_total`: custo total em USD.

`tokens_cached`, `tokens_reasoning`, `generation_time_ms` e
`time_to_first_token_ms` são colunas numéricas opcionais; se ausentes, recebem
zero. Datas inválidas são descartadas. Colunas numéricas inválidas ou vazias
recebem zero, e nomes de provedor/modelo vazios são exibidos como “Não informado”.
Colunas obrigatórias ausentes ou um arquivo inexistente geram uma mensagem de
erro na página.

## Rotas e parâmetros

- `GET /`: apresenta o dashboard. Parâmetros opcionais: `start` e `end`
  (`AAAA-MM-DD`), `provider`, `model` e `page` (página da tabela).
- `GET /health`: retorna JSON indicando que o processo Flask está ativo e que
  a fonte configurada é CSV. A rota não valida o conteúdo do arquivo.

Quando não há registros correspondentes aos filtros, os indicadores e a tabela
são substituídos por uma mensagem de resultado vazio. O painel de erro do CSV
é retornado com HTTP 500.

## Métricas e gráficos

- Indicadores: tokens totais, entrada, saída, custo total, quantidade de
  requisições, cache, raciocínio, tempo médio de geração e custo médio por
  requisição.
- Gráfico de custos: custo diário em USD.
- Gráfico de modelos: até sete modelos com maior soma de tokens de entrada e saída.
- Gráfico de provedores: participação dos seis provedores com maior consumo.
- Histórico: data/hora, modelo, provedor, tokens de entrada e saída, custo e
  tempo de geração por requisição.

O backend também prepara uma série diária de tokens de entrada e saída
(`dashboard.charts.usage`), mas o template atual não a exibe.

## Dependências e observações

As dependências Python estão em `requirements.txt`. Para visualizar os gráficos
e carregar as fontes tipográficas, o navegador precisa acessar as CDNs do Plotly
e do Google Fonts. O CSV é carregado do disco local a cada solicitação da página;
não há banco de dados, cache ou integração de API nesta versão.
