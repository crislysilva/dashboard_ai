# TokenScope — Projeto 1

Dashboard Flask de análise dos dados do CSV do OpenRouter referente ao Projeto 1.

## Execução (Windows / PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Acesse http://127.0.0.1:5000.

O arquivo `data/openrouter_activity_2026-08-31.csv` já está incluído e é carregado automaticamente. Não há upload de CSV nem integração de API nesta etapa. O backend possui filtros por período, provedor e modelo e tabela paginada.

## Estrutura

- `app.py`: Flask e agregações com pandas
- `templates/index.html`: interface Jinja2
- `static/style.css`: estilo responsivo
- `data/`: CSV do Projeto 1

A biblioteca Plotly é carregada via CDN; para visualizar os gráficos é necessária conexão à internet. A integração com API será adicionada posteriormente.
