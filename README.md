# TokenScope — Dashboard de Análise de Tokens de IA

Dashboard web desenvolvido com **Python e Flask** para visualizar e analisar o consumo de modelos de inteligência artificial no **Projeto 1**, a partir de um relatório CSV exportado do **OpenRouter**.

> **Status:** versão inicial para análise histórica por CSV. A atualização automática por API ainda não foi implementada.

## Funcionalidades

- Indicadores de tokens de entrada, saída e total, número de gerações e custo em USD.
- Métricas adicionais de tokens em cache, tokens de raciocínio, custo médio por geração e tempo médio de geração.
- Gráficos de consumo diário de tokens, evolução diária dos custos, modelos com maior consumo e distribuição por provedor.
- Filtros por período, modelo e provedor.
- Histórico das gerações, ordenado das mais recentes para as mais antigas, com **15 registros por página**.
- Layout responsivo com tema claro.
- Endpoint simples de verificação: `GET /health`.

## Tecnologias

- Python 3
- Flask
- pandas
- Plotly
- HTML, CSS e Jinja2

O Plotly é carregado no navegador por CDN; portanto, é necessária conexão com a internet para carregar os gráficos, a menos que a biblioteca seja disponibilizada localmente.

## Estrutura do projeto

```text
dashboard_ai/
├── app.py
├── requirements.txt
├── README.md
├── data/
│   └── openrouter_activity_2026-08-31.csv
├── templates/
│   └── index.html
└── static/
    └── style.css
```

A pasta `data/` e o CSV precisam estar presentes para que a tela principal seja carregada. Se o CSV não estiver no repositório, solicite o arquivo autorizado ao responsável pelo projeto e coloque-o nesse caminho, **sem alterar o nome**.

## Como executar (Windows / VS Code / PowerShell)

**1. Clone o repositório**

```powershell
git clone https://github.com/crislysilva/dashboard_ai.git
cd dashboard_ai
```

**2. Crie o ambiente virtual** (somente na primeira execução)

```powershell
python -m venv .venv
```

**3. Ative o ambiente virtual**

```powershell
.\.venv\Scripts\Activate.ps1
```

Se o PowerShell bloquear a ativação, execute `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` e tente o comando de ativação novamente.

**4. Instale as dependências**

```powershell
python -m pip install -r requirements.txt
```

**5. Confirme que o CSV está em** `data/openrouter_activity_2026-08-31.csv`.

**6. Inicie a aplicação**

```powershell
python app.py
```

**7. Acesse** http://127.0.0.1:5000

Para verificar rapidamente o servidor, acesse http://127.0.0.1:5000/health. O retorno esperado é um JSON com `status: "ok"`.

Para encerrar, pressione `Ctrl + C` no terminal.

### Execuções seguintes

Na pasta do projeto, basta executar:

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

## Fonte de dados

O arquivo esperado é `data/openrouter_activity_2026-08-31.csv`, exportado do OpenRouter. O sistema utiliza campos como:

| Campo do CSV | Uso no dashboard |
| --- | --- |
| `created_at` | Datas e agrupamento diário |
| `generation_id` | Identificador da geração |
| `model_permaslug` | Modelo utilizado |
| `provider_name` | Provedor da geração |
| `tokens_prompt` | Tokens de entrada |
| `tokens_completion` | Tokens de saída |
| `tokens_cached` | Tokens em cache |
| `tokens_reasoning` | Tokens de raciocínio |
| `cost_total` | Custo em USD |
| `generation_time_ms` | Tempo de geração, convertido de ms para segundos |

**Cálculos:** o total de tokens é a soma dos tokens de entrada e saída; o custo total é a soma de `cost_total`; e a quantidade de gerações corresponde ao número de linhas válidas após os filtros. Tokens em cache e de raciocínio são métricas complementares e não devem ser somados novamente ao total sem considerar como o provedor os contabiliza.

Os filtros e gráficos utilizam os registros do CSV carregados pelo backend. **Os dados não são atualizados em tempo real** nesta versão.

## Como testar

1. Abra a página inicial e confira se os cartões e gráficos carregam.
2. Altere os filtros de período, provedor e modelo e selecione **Aplicar filtros**.
3. Utilize **Limpar** para retornar à visão completa.
4. Navegue pelas páginas do histórico de gerações.
5. Verifique se as datas, os custos e os totais exibidos são coerentes com o CSV.
6. Acesse `/health` para verificar a resposta básica do servidor.

## Segurança e privacidade

**Atenção antes de publicar o CSV:** o relatório original pode conter identificadores de gerações, identificadores de usuários e nomes de chaves de API (`generation_id`, `user`, `api_key_name`), além de informações operacionais e financeiras. O nome de uma chave não é sua chave secreta, mas ainda pode ser informação interna.

- Obtenha autorização para publicar os dados em um repositório **público**.
- Se não houver autorização, compartilhe o CSV por um canal interno aprovado ou use um repositório privado, respeitando as políticas da empresa.
- Nunca faça commit de segredos reais, tokens de autenticação ou arquivos `.env` com credenciais.
- Não versione a pasta `.venv/`, arquivos `__pycache__/` e artefatos temporários.
- O servidor Flask em modo `debug=True` é destinado ao desenvolvimento local, não à produção.

## Próximos passos

- Integrar uma API autorizada para obter novos dados automaticamente.
- Persistir e atualizar os registros de forma controlada, evitando duplicação.
- Ampliar comparações de custo, desempenho e uso por modelo/provedor.
- Adicionar testes automatizados e configuração adequada para implantação.

---

**Projeto 1 — TokenScope** · Versão inicial baseada em CSV do OpenRouter.
