"""TokenScope: painel local de um único projeto baseado no export do OpenRouter."""
import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from flask import Flask, render_template, request, jsonify

BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = BASE_DIR / 'data' / 'openrouter_activity_2026-08-31.csv'
PROJECT_NAME = 'Projeto 1'
app = Flask(__name__)

PALETTE = ['#8b7cf6', '#37c8c1', '#f9b66b', '#f8799f', '#7eaef8', '#b2d47b', '#a39fae']
PLOT_BG = 'rgba(0,0,0,0)'


def load_data():
    """Lê export real do OpenRouter e prepara os tipos. Não depende de API."""
    if not CSV_FILE.is_file():
        raise FileNotFoundError(f'CSV não encontrado: {CSV_FILE.name}')
    df = pd.read_csv(CSV_FILE, low_memory=False)
    required = {'created_at', 'generation_id', 'tokens_prompt', 'tokens_completion',
                'model_permaslug', 'provider_name', 'cost_total'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError('Colunas ausentes no CSV: ' + ', '.join(sorted(missing)))
    df['created_at'] = pd.to_datetime(df['created_at'], errors='coerce')
    df = df.dropna(subset=['created_at']).copy()
    for col in ['tokens_prompt', 'tokens_completion', 'tokens_cached', 'tokens_reasoning',
                'cost_total', 'generation_time_ms', 'time_to_first_token_ms']:
        if col not in df:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    for col in ['provider_name', 'model_permaslug']:
        df[col] = df[col].fillna('Não informado').astype(str)
    df['total_tokens'] = df['tokens_prompt'] + df['tokens_completion']
    df['day'] = df['created_at'].dt.strftime('%Y-%m-%d')
    return df


def style_chart(fig, height=300, margin=None):
    fig.update_layout(
        template='plotly_dark', height=height, autosize=True,
        paper_bgcolor=PLOT_BG, plot_bgcolor=PLOT_BG,
        font=dict(family='Inter, system-ui, Arial', color='#a9b2c7', size=11),
        margin=margin or dict(l=12, r=12, t=20, b=25),
        legend=dict(orientation='h', y=-0.22, x=0, font=dict(size=11)),
        hoverlabel=dict(bgcolor='#1f293c', font_color='#f1f4fc', bordercolor='#35405a'),
        xaxis=dict(gridcolor='rgba(156,171,205,.09)', zeroline=False, linecolor='rgba(156,171,205,.10)'),
        yaxis=dict(gridcolor='rgba(156,171,205,.09)', zeroline=False, linecolor='rgba(156,171,205,.10)'),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={'displayModeBar': False, 'responsive': True})


def fmt_int(value):
    return f'{int(value):,}'.replace(',', '.')


def fmt_usd(value):
    return f'US$ {value:,.2f}'


def filter_data(df, args):
    start = args.get('start', '').strip()
    end = args.get('end', '').strip()
    provider = args.get('provider', '').strip()
    model = args.get('model', '').strip()
    if start:
        try:
            date = pd.to_datetime(start, format='%Y-%m-%d', errors='raise')
            df = df[df['created_at'] >= date]
        except (ValueError, TypeError):
            start = ''
    if end:
        try:
            date = pd.to_datetime(end, format='%Y-%m-%d', errors='raise')
            df = df[df['created_at'] < date + pd.Timedelta(days=1)]
        except (ValueError, TypeError):
            end = ''
    if provider:
        df = df[df['provider_name'] == provider]
    if model:
        df = df[df['model_permaslug'] == model]
    return df, dict(start=start, end=end, provider=provider, model=model)


def build_charts(df):
    daily = df.groupby('day', as_index=False)[['tokens_prompt', 'tokens_completion', 'cost_total']].sum().sort_values('day')
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily['day'], y=daily['tokens_prompt'], name='Entrada',
                             mode='lines', stackgroup='one', line=dict(color=PALETTE[0], width=2),
                             fillcolor='rgba(139,124,246,.35)'))
    fig.add_trace(go.Scatter(x=daily['day'], y=daily['tokens_completion'], name='Saída',
                             mode='lines', stackgroup='one', line=dict(color=PALETTE[1], width=2),
                             fillcolor='rgba(55,200,193,.35)'))
    fig.update_yaxes(tickformat='~s')
    usage = style_chart(fig, 310)

    costs = go.Figure()
    costs.add_trace(go.Bar(x=daily['day'], y=daily['cost_total'], marker_color=PALETTE[0],
                           hovertemplate='%{x}<br>US$ %{y:.4f}<extra></extra>'))
    costs.update_yaxes(tickprefix='$', tickformat='.2f')
    cost = style_chart(costs, 260)

    models = df.groupby('model_permaslug', as_index=False)['total_tokens'].sum().nlargest(7, 'total_tokens')
    models = models.sort_values('total_tokens')
    modelfig = go.Figure(go.Bar(x=models['total_tokens'], y=models['model_permaslug'], orientation='h',
                                marker=dict(color=PALETTE[0], cornerradius=5),
                                hovertemplate='%{y}<br>%{x:,.0f} tokens<extra></extra>'))
    modelfig.update_xaxes(tickformat='~s')
    model = style_chart(modelfig, 310, dict(l=10, r=15, t=12, b=35))

    providers = df.groupby('provider_name', as_index=False)['total_tokens'].sum().nlargest(6, 'total_tokens')
    donut = go.Figure(go.Pie(labels=providers['provider_name'], values=providers['total_tokens'],
                             hole=.70, sort=False, marker=dict(colors=PALETTE),
                             textinfo='percent', textposition='outside',
                             hovertemplate='%{label}: %{value:,.0f} tokens (%{percent})<extra></extra>'))
    donut.update_layout(showlegend=True, legend=dict(orientation='h', y=-.16, x=0),
                        annotations=[dict(text='PROVEDORES', x=.5, y=.5, showarrow=False,
                                          font=dict(size=11, color='#9da8c0'))])
    provider = style_chart(donut, 310, dict(l=10, r=10, t=12, b=15))
    return dict(usage=usage, cost=cost, model=model, provider=provider)


@app.get('/')
def index():
    try:
        raw = load_data()
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        return render_template('index.html', error=str(exc), project=PROJECT_NAME, dashboard=None), 500

    providers = sorted(raw['provider_name'].unique().tolist())
    models = sorted(raw['model_permaslug'].unique().tolist())
    filtered, filters = filter_data(raw, request.args)
    page_arg = request.args.get('page', '1')
    try:
        page = max(1, int(page_arg))
    except ValueError:
        page = 1
    per_page = 15
    count = len(filtered)
    pages = max(1, math.ceil(count / per_page))
    page = min(page, pages)
    recent = filtered.sort_values('created_at', ascending=False).iloc[(page - 1) * per_page:page * per_page]
    records = []
    for row in recent.itertuples(index=False):
        records.append({
            'date': row.created_at.strftime('%d/%m/%Y %H:%M'),
            'model': row.model_permaslug,
            'provider': row.provider_name,
            'input': fmt_int(row.tokens_prompt),
            'output': fmt_int(row.tokens_completion),
            'cost': f'US$ {row.cost_total:.5f}',
            'latency': f'{row.generation_time_ms / 1000:.2f} s',
        })
    dashboard = None
    if count:
        input_tokens = int(filtered['tokens_prompt'].sum())
        output_tokens = int(filtered['tokens_completion'].sum())
        total_cost = float(filtered['cost_total'].sum())
        span = (filtered['created_at'].min().strftime('%d/%m/%Y'), filtered['created_at'].max().strftime('%d/%m/%Y'))
        dashboard = dict(
            total=fmt_int(input_tokens + output_tokens), input=fmt_int(input_tokens),
            output=fmt_int(output_tokens), cost=fmt_usd(total_cost),
            cost_precise=f'US$ {total_cost:,.4f}', requests=fmt_int(count),
            avg_latency=f"{filtered['generation_time_ms'].mean() / 1000:.2f} s",
            cached=fmt_int(filtered['tokens_cached'].sum()),
            reasoning=fmt_int(filtered['tokens_reasoning'].sum()),
            avg_cost=f'US$ {total_cost / count:.4f}',
            span=span, charts=build_charts(filtered),
        )
    return render_template('index.html', project=PROJECT_NAME, dashboard=dashboard,
                           error=None, filters=filters, providers=providers, models=models,
                           records=records, count=count, page=page, pages=pages,
                           first=(page - 1) * per_page + 1 if count else 0,
                           last=min(page * per_page, count), total_records=len(raw),
                           initial_date=raw['created_at'].min().strftime('%d/%m/%Y'),
                           final_date=raw['created_at'].max().strftime('%d/%m/%Y'))


@app.get('/health')
def health():
    return jsonify(status='ok', source='csv', project=PROJECT_NAME)


if __name__ == '__main__':
    app.run(debug=True)
