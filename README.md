# TokenScope

Dashboard interno para acompanhar **custo, tokens e requisições de IA** dos
projetos **Instant Doctor**, **Two** e **Fracta**.

- Os dados de consumo vêm da API **OpenRouter Analytics (beta)**, consultada
  com uma chave de gerenciamento por projeto.
- O acesso exige **login**. As contas ficam em um banco **PostgreSQL** e são
  criadas apenas pelo administrador, pelo terminal (não há página de cadastro).
- Stack: Flask, Flask-Login, Flask-SQLAlchemy, Flask-WTF (CSRF), pandas e
  Plotly.

> A API Analytics devolve **agregados por dia e modelo**, não gerações
> individuais. Por isso a tabela de histórico mostra grupos diários, não
> requisições uma a uma.

---

## Sumário

1. [Requisitos](#requisitos)
2. [Instalação](#instalação)
3. [Configuração do `.env`](#configuração-do-env)
4. [Banco de dados e usuários](#banco-de-dados-e-usuários)
5. [Executar](#executar)
6. [Usando o dashboard](#usando-o-dashboard)
7. [Estrutura do projeto](#estrutura-do-projeto)
8. [Como funciona](#como-funciona)
9. [Rotas](#rotas)
10. [Segurança](#segurança)
11. [Problemas comuns](#problemas-comuns)
12. [Limitações conhecidas](#limitações-conhecidas)

---

## Requisitos

- Python 3.10 ou superior
- PostgreSQL acessível a partir da máquina que executa o app
- Uma chave de gerenciamento do OpenRouter para cada projeto
- Acesso à internet (API do OpenRouter, CDN do Plotly e Google Fonts)

## Instalação

No PowerShell, a partir da pasta `dashboard_ai`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Dependências (`requirements.txt`): Flask, Flask-Login, Flask-SQLAlchemy,
Flask-WTF, psycopg 3, pandas e Plotly.

## Configuração do `.env`

Crie um arquivo `.env` na pasta `dashboard_ai`:

```env
# Banco de dados (obrigatório)
DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/tokenscope_db

# Chaves de gerenciamento do OpenRouter
OPENROUTER_INSTANT_DOCTOR_MANAGEMENT_KEY=sk-or-...
OPENROUTER_TWO_MANAGEMENT_KEY=sk-or-...
OPENROUTER_FRACTA_MANAGEMENT_KEY=sk-or-...

# Opcional
# SECRET_KEY=uma-string-aleatoria-com-pelo-menos-32-caracteres
```

| Variável | Obrigatória | Uso |
| --- | --- | --- |
| `DATABASE_URL` | Sim | Conexão PostgreSQL (`postgresql+psycopg://` ou `postgresql://`). SQLite é recusado. |
| `OPENROUTER_INSTANT_DOCTOR_MANAGEMENT_KEY` | Por projeto | Chave do Instant Doctor. |
| `OPENROUTER_TWO_MANAGEMENT_KEY` | Por projeto | Chave da Two. |
| `OPENROUTER_FRACTA_MANAGEMENT_KEY` | Por projeto | Chave da Fracta. |
| `OPENROUTER_MANAGEMENT_KEY` | Não | Nome antigo, ainda aceito como alternativa para o Instant Doctor. |
| `SECRET_KEY` | Não | Segredo das sessões (mínimo de 32 caracteres). Se ficar vazio, o app gera um e o salva em `instance/.secret_key`. |
| `SESSION_COOKIE_SECURE` | Não | Use `1` quando o app for servido por HTTPS. |
| `FLASK_DEBUG` | Não | Use `1` para ativar o modo de depuração (somente em desenvolvimento). |
| `OPENROUTER_API_KEY_HASH` | Não | Reservada. Se estiver preenchida, as consultas são **bloqueadas**, porque o filtro por chave ainda não foi implementado. |

Observações:

- O app lê somente as variáveis da tabela acima. As variáveis já definidas no
  ambiente do sistema têm prioridade sobre o `.env`.
- `.env` e `instance/` estão no `.gitignore`. **Nunca faça commit das chaves.**
- A visão "Todos os projetos" exige as três chaves e recusa chaves repetidas,
  para não somar o mesmo consumo duas vezes.

## Banco de dados e usuários

Na inicialização, o app cria a tabela `users` caso ela não exista. Essa tabela
tem as colunas `id`, `name`, `email` e `password_hash`. Somente o hash da senha
é gravado.

As contas são administradas com `manage_users.py`, executado em um terminal com
acesso ao PostgreSQL:

```powershell
# Uma única vez, em bancos criados pela versão de usuário único:
python manage_users.py migrate

# Criar uma conta (pede nome, e-mail e senha; a senha não aparece na tela)
python manage_users.py criar

# Listar as contas (sem senhas)
python manage_users.py listar
```

- O comando `migrate` remove a restrição antiga `ck_tokenscope_single_user` e
  ajusta a sequência de IDs. Os usuários existentes são preservados.
- Regras para criar uma conta: nome com 2 a 120 caracteres, e-mail único e
  senha com 12 a 128 caracteres.
- Não grave senhas diretamente com SQL e não envie senhas por chat.

## Executar

```powershell
.venv\Scripts\Activate.ps1
python app.py
```

Abra <http://127.0.0.1:5000/login> e entre com uma conta criada pelo
`manage_users.py`.

## Usando o dashboard

**Filtros**

- **Projeto:** Todos os projetos, Instant Doctor, Two ou Fracta. Cada projeto
  tem um tema de cor próprio.
- **Mês:** seleciona um mês inteiro. Tem prioridade sobre as datas.
- **De / Até:** um período em datas UTC, com os dois dias incluídos. Uma data
  "Até" no futuro é limitada a hoje.

**Indicadores (KPIs):** custo total, tokens (entrada, saída e total),
requisições, tokens em cache, tokens de raciocínio, tempo médio de geração e
custo médio por requisição.

**Gráficos**

- Custo, tokens de entrada e saída, e requisições ao longo do tempo. Em
  períodos longos, os dados são agrupados por semana ou por mês.
- Tokens por modelo e custo por modelo.
- **Custos dos últimos 15 dias:** aparece em todas as visões e termina na data
  "Até", sem depender da data "De". Dias sem custo também são mostrados.
- Apenas na visão "Todos": comparativo entre projetos e modelos usados em mais
  de um projeto.

**Histórico:** uma tabela de grupos diários por modelo, com tokens, custo e
tempo, mostrando 15 linhas por página.

## Estrutura do projeto

```
dashboard_ai/
├── app.py                       # App Flask: API OpenRouter, cache, agregações, KPIs e gráficos
├── auth.py                      # Login, sessões, CSRF, modelo User e limite de tentativas
├── manage_users.py              # Comandos de terminal: migrate, criar e listar
├── requirements.txt
├── templates/
│   ├── index.html               # Dashboard (filtros, KPIs, gráficos e tabela)
│   └── login.html               # Tela de login
├── static/
│   ├── style.css                # Estilos do dashboard e temas por projeto
│   └── auth.css                 # Estilos da tela de login
├── instance/                    # Gerada automaticamente (.secret_key); fora do Git
└── .env                         # Configuração privada; fora do Git
```

## Como funciona

1. **Inicialização.** `app.py` carrega o `.env` e chama `init_auth()`, que
   configura o banco, o CSRF, o login e os cookies de sessão. As sessões
   duram 8 horas.
2. **Projeto e período.** `chosen_projects()` e `date_window()` definem quais
   projetos e quais datas consultar. Cada projeto tem um `start_date` em
   `PROJECTS`, e nenhuma consulta começa antes dessa data.
3. **Catálogo.** `analytics_catalog()` consulta `/api/v1/analytics/meta` para
   descobrir as métricas, dimensões e granularidades disponíveis, porque a API
   é beta e pode mudar.
4. **Consulta.** `fetch_activity()` consulta `/api/v1/analytics/query` com
   granularidade diária, agrupando por modelo. Períodos longos são divididos em
   janelas de até 30 dias (`MAX_QUERY_DAYS`). Na visão "Todos", os projetos
   são consultados em paralelo.
5. **Métricas opcionais.** `enrich_optional_metrics()` adiciona cache,
   raciocínio e tempo médio de geração quando a API os oferece. As métricas de
   tempo usam somente os 30 dias mais recentes do período, com média ponderada
   pelo número de requisições.
6. **Cache.** As respostas da API ficam em memória por 15 minutos
   (`CACHE_TTL_SECONDS`). As credenciais não são gravadas em disco.
7. **Exibição.** `build_charts()`, `project_comparison()` e
   `costs_last_15_days_chart()` montam os gráficos Plotly, e o `index.html`
   renderiza a página.

## Rotas

Todas as rotas, exceto `/login`, exigem uma sessão autenticada.

| Rota | Descrição |
| --- | --- |
| `GET /` | Dashboard. Parâmetros: `project` (`instant_doctor`, `two`, `fracta` ou `all`), `month` (`AAAA-MM`), `start` e `end` (`AAAA-MM-DD`) e `page`. |
| `GET, POST /login` | Tela de login. Permite 5 tentativas falhas a cada 15 minutos por IP e e-mail. |
| `POST /sair` | Encerra a sessão. |
| `GET /health` | JSON com `status` e com quais projetos têm chave configurada (sem mostrar a chave). |

Se `project` ficar vazio, o app usa `all` quando as três chaves existem e
`instant_doctor` nos outros casos. Erros de configuração ou da API aparecem na
página com HTTP 503, sem expor credenciais.

## Segurança

- As senhas são gravadas com hash (`werkzeug.security`), e todos os
  formulários têm proteção CSRF.
- Os cookies de sessão usam `HttpOnly` e `SameSite=Lax`. Com HTTPS, defina
  `SESSION_COOKIE_SECURE=1`.
- O limite de tentativas de login fica em memória: ele é zerado quando o app
  reinicia e não é compartilhado entre processos.
- Não exponha o PostgreSQL nem o `.env` à internet. Restrinja quem pode
  executar o `manage_users.py`.
- Para produção, use HTTPS, um `SECRET_KEY` fixo e um servidor WSGI (por
  exemplo, `waitress` no Windows) em vez do servidor de desenvolvimento do
  Flask.

## Problemas comuns

| Mensagem | Causa e solução |
| --- | --- |
| `Configure DATABASE_URL no .env...` | Falta `DATABASE_URL` no `.env`. |
| `DATABASE_URL precisa usar PostgreSQL` | A URL não começa com `postgresql://` ou `postgresql+psycopg://`. |
| `SECRET_KEY precisa ter pelo menos 32 caracteres` | Aumente o `SECRET_KEY` ou remova a variável para o app gerar um. |
| `Configure no .env a chave dos projetos: ...` | A visão "Todos" precisa das três chaves. |
| `Há credenciais idênticas entre os projetos` | Dois projetos estão usando a mesma chave. |
| `Não foi possível criar a conta. Execute a migração primeiro...` | Execute `python manage_users.py migrate`. |
| `Muitas tentativas. Tente novamente em 15 minutos.` | O limite de tentativas de login foi atingido. Aguarde ou reinicie o app. |

## Limitações conhecidas

- A API Analytics do OpenRouter está em **beta**: nomes de métricas e limites
  podem mudar.
- Os valores são agregados e podem diferir um pouco do painel oficial do
  OpenRouter. Confira os valores no painel oficial quando forem usados para
  faturamento.
- A data de início da Fracta (`2025-11-12`) é apenas um limite inferior de
  busca. Ela não confirma quando o uso começou.
- Não há página de cadastro nem de recuperação de senha. As contas são
  administradas somente pelo terminal.
