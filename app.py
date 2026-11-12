"""TokenScope: painel de custos via OpenRouter Analytics (beta).
Consulta um intervalo informado pelo usuário, com chave de gerenciamento.
A API devolve agregados, não o histórico de gerações individuais.
Consultas longas são divididas em janelas UTC de até 30 dias e consolidadas.
"""
from __future__ import annotations
import math
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from calendar import monthrange
import os
import urllib.error
import urllib.parse
import urllib.request
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
from flask import Flask, jsonify, render_template, request
BASE_DIR = Path(__file__).resolve().parent
PROJECT_NAME = "Instant Doctor"
# Cada projeto tem sua própria configuração. A chave antiga continua funcionando.
PROJECTS = {
    "instant_doctor": {
        "label": "Instant Doctor",
        "start_date": "2026-09-21",
        "env_key": "OPENROUTER_INSTANT_DOCTOR_MANAGEMENT_KEY",
        "legacy_env_key": "OPENROUTER_MANAGEMENT_KEY",
    },
    "two": {
        "label": "Two",
        "start_date": "2026-07-30",
        "env_key": "OPENROUTER_TWO_MANAGEMENT_KEY",
        "legacy_env_key": None,
    },
    "fracta": {
        "label": "Fracta",
        # Limite inferior de busca, não início confirmado.
        "start_date": "2025-11-12",
        "env_key": "OPENROUTER_FRACTA_MANAGEMENT_KEY",
        "legacy_env_key": None,
    },
}
# Tema visual da navegação. Não altera dados nem consultas à API.
PROJECT_THEMES = {
    "instant_doctor": "theme-instant",
    "two": "theme-two",
    "fracta": "theme-fracta",
    "all": "theme-all",
}
PROJECT_ALL = "all"
ANALYTICS_META_URL = "https://openrouter.ai/api/v1/analytics/meta"
ANALYTICS_QUERY_URL = "https://openrouter.ai/api/v1/analytics/query"
DEFAULT_START = "2025-11-12"  # Primeira atividade conhecida entre os projetos; não é criação da chave.
MAX_QUERY_DAYS = 30  # Menor que o limite de 31 dias observado em consultas reais.
MAX_QUERY_ROWS = 10000
# A API Analytics é beta; a lista de métricas é descoberta em /analytics/meta.
PALETTE = ["#7868df", "#30b1a6", "#f1ac64", "#e78eaa", "#81a7e8", "#9fc879", "#a39fae"]
app = Flask(__name__)
# Cache local por origem e intervalo; não armazena credenciais em disco.
CACHE_TTL_SECONDS = 900  # 15 minutos; evita buscar tudo novamente a cada troca de projeto.
_cache = {}
_cache_lock = threading.Lock()
MONTH_LABELS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

def cached_call(cache_id, fetcher):
    """Evita consultas repetidas ao recarregar ou alternar entre projetos."""
    now = time.monotonic()
    with _cache_lock:
        found = _cache.get(cache_id)
        if found and now - found[0] < CACHE_TTL_SECONDS:
            app.logger.info("TokenScope: usando resposta em cache")
            return found[1]
    result = fetcher()
    with _cache_lock:
        _cache[cache_id] = (time.monotonic(), result)
        # Descarte simples de entradas expiradas para evitar crescimento ilimitado.
        if len(_cache) > 180:
            moment = time.monotonic()
            for k, (t, _) in list(_cache.items()):
                if moment - t > CACHE_TTL_SECONDS:
                    _cache.pop(k, None)
    return result

def months_for_filter():
    """Meses disponíveis de novembro/2025 até o mês UTC atual."""
    current = datetime.now(timezone.utc).date()
    year, month = 2025, 11
    result = []
    while (year, month) <= (current.year, current.month):
        result.append({"value": f"{year:04d}-{month:02d}", "label": f"{MONTH_LABELS[month-1]} de {year}"})
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return list(reversed(result))

def load_local_env():
    """Lê apenas as configurações esperadas de .env, sem dependências extras.
    Variáveis definidas no ambiente têm prioridade sobre o arquivo local.
    """
    allowed = {
        "OPENROUTER_MANAGEMENT_KEY", "OPENROUTER_API_KEY_HASH",
        "OPENROUTER_INSTANT_DOCTOR_MANAGEMENT_KEY",
        "OPENROUTER_TWO_MANAGEMENT_KEY",
        "OPENROUTER_FRACTA_MANAGEMENT_KEY",
        "OPENROUTER_PROJECT_SCOPES_VERIFIED", "DATABASE_URL", "SECRET_KEY",
    }
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in allowed:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(name, value)
load_local_env()

from auth import init_auth, login_required
init_auth(app)

class OpenRouterActivityError(Exception):
    """Erro seguro para exibição no dashboard (sem expor credenciais)."""

def api_json(url, key, payload=None):
    """Requisição autenticada com diagnóstico de falhas transitórias.
    Evita mostrar ou registrar credenciais. HTTP 400/401/403 não são repetidos.
    """
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "TokenScope/2.1",
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    for attempt in range(2):
        req = urllib.request.Request(
            url, data=body, headers=headers,
            method="POST" if body is not None else "GET",
        )
        beginning = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                result = json.load(response)
            app.logger.info("OpenRouter: requisição concluída em %.1fs", time.monotonic() - beginning)
            return result
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 502, 503, 504) and attempt == 0:
                app.logger.warning("OpenRouter HTTP %s: uma nova tentativa será feita", exc.code)
                time.sleep(1)
                continue
            if exc.code == 400:
                try:
                    details = json.loads(exc.read().decode("utf-8"))
                    error_info = details.get("error", {})
                    message = error_info.get("message", "Parâmetros inválidos") if isinstance(error_info, dict) else "Parâmetros inválidos"
                except (ValueError, UnicodeError, AttributeError):
                    message = "Parâmetros inválidos ou resposta de erro não interpretável"
                raise OpenRouterActivityError(f"OpenRouter HTTP 400: {message}") from None
            messages = {
                401: "Chave de gerenciamento inválida ou expirada (HTTP 401).",
                403: "Esta conta/chave não possui acesso à API Analytics (HTTP 403).",
                408: "A API excedeu o tempo de processamento (HTTP 408).",
                429: "OpenRouter limitou as requisições (HTTP 429).",
                502: "Instabilidade temporária do OpenRouter (HTTP 502).",
                503: "Serviço OpenRouter temporariamente indisponível (HTTP 503).",
                504: "Tempo limite no servidor OpenRouter (HTTP 504).",
            }
            raise OpenRouterActivityError(messages.get(exc.code, f"OpenRouter respondeu HTTP {exc.code}.")) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            is_timeout = isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
            if attempt == 0:
                app.logger.warning("Falha temporária de conexão (timeout=%s), tentando novamente", is_timeout)
                time.sleep(1)
                continue
            if is_timeout:
                raise OpenRouterActivityError(
                    "A consulta excedeu o tempo de espera pela resposta do OpenRouter. "
                    "Tente novamente ou selecione um mês específico."
                ) from None
            raise OpenRouterActivityError(
                "Falha de conexão com o OpenRouter após duas tentativas. "
                "Verifique a rede, VPN/proxy ou a disponibilidade do serviço."
            ) from None
        except (ValueError, UnicodeError):
            raise OpenRouterActivityError("O OpenRouter retornou um JSON inválido.") from None

def analytics_catalog(key, project_id=None):
    """Descobre métricas/dimensões disponíveis, sem presumir nomes da API beta."""
    response = cached_call(("meta", project_id or key), lambda: api_json(ANALYTICS_META_URL, key))
    info = response.get("data") if isinstance(response, dict) else None
    if not isinstance(info, dict):
        raise OpenRouterActivityError("Resposta inesperada de /analytics/meta.")
    names = lambda field: {x["name"] for x in info.get(field, [])
                           if isinstance(x, dict) and isinstance(x.get("name"), str)}
    metric_details = {item["name"]: item for item in info.get("metrics", [])
                      if isinstance(item, dict) and isinstance(item.get("name"), str)}
    return names("metrics"), names("dimensions"), names("granularities"), metric_details

def select_metric(available, candidates, label, required=True):
    for candidate in candidates:
        if candidate in available:
            return candidate
    if required:
        raise OpenRouterActivityError(
            f"A API Analytics não disponibilizou uma métrica compatível com {label}. "
            f"Métricas existentes: {', '.join(sorted(available)) or 'nenhuma'}. "
            "Não é seguro calcular valores financeiros usando outro campo."
        )
    return None

def date_window(args, selected_ids):
    """Datas UTC inclusivas; filtro por mês tem prioridade sobre datas."""
    first_day = min(datetime.strptime(PROJECTS[pid]["start_date"], "%Y-%m-%d").date() for pid in selected_ids)
    today = datetime.now(timezone.utc).date()
    month = (args.get("month") or "").strip()
    start_raw = (args.get("start") or "").strip()
    end_raw = (args.get("end") or "").strip()
    try:
        if month:
            if len(month) != 7 or month[4] != "-":
                raise ValueError("mês")
            year, number = [int(v) for v in month.split("-")]
            start = datetime(year, number, 1).date()
            end = datetime(year, number, monthrange(year, number)[1]).date()
        else:
            start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else first_day
            end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else today
    except (ValueError, TypeError):
        raise OpenRouterActivityError("Informe datas válidas ou selecione um mês válido.") from None
    if end > today:
        end = today
    if start > today:
        raise OpenRouterActivityError("O mês ou período selecionado ainda não começou.")
    if start > end:
        raise OpenRouterActivityError("O período está inválido: confira as datas inicial e final.")
    return start, end

def configured_key(project_id):
    """Lê a credencial associada a cada origem, sem exibir o segredo."""
    settings = PROJECTS[project_id]
    key = os.getenv(settings["env_key"], "").strip()
    if not key and settings["legacy_env_key"]:
        key = os.getenv(settings["legacy_env_key"], "").strip()
    if key == "COLOQUE_SUA_CHAVE_AQUI":
        return ""
    return key

def chosen_projects(raw):
    """Resolve o filtro; antes da chegada da Two, preserva a tela atual."""
    name = (raw or "").strip().lower()
    if not name:
        name = PROJECT_ALL if all(configured_key(pid) for pid in PROJECTS) else "instant_doctor"
    if name == PROJECT_ALL:
        missing = [PROJECTS[pid]["label"] for pid in PROJECTS if not configured_key(pid)]
        if missing:
            raise OpenRouterActivityError(
                "Configure no .env a chave dos projetos: " + ", ".join(missing)
            )
        keys = [configured_key(pid) for pid in PROJECTS]
        if len(keys) != len(set(keys)):
            raise OpenRouterActivityError(
                "Há credenciais idênticas entre os projetos; a soma poderia duplicar o consumo."
            )
        return list(PROJECTS), name
    if name not in PROJECTS:
        raise OpenRouterActivityError("Projeto inválido. Escolha Instant Doctor, Two, Fracta ou Todos os projetos.")
    if not configured_key(name):
        raise OpenRouterActivityError(
            f"A chave de gerenciamento de {PROJECTS[name]['label']} ainda não foi configurada no .env."
        )
    return [name], name

def fetch_activity(start, end, key, project_id=None):
    """Consulta agregados por dia e modelo usando a chave da origem selecionada."""
    if not key:
        raise OpenRouterActivityError("Chave de gerenciamento do projeto não configurada.")
    if os.getenv("OPENROUTER_API_KEY_HASH", "").strip():
        raise OpenRouterActivityError(
            "OPENROUTER_API_KEY_HASH está preenchido, mas este código ainda não aplica "
            "filtro por chave na API Analytics beta. Não é seguro ignorar o filtro e "
            "apresentar números de outros projetos. Verifique o escopo da credencial."
        )
    available, dimensions, granularities, metric_details = analytics_catalog(key, project_id)
    if "day" not in granularities:
        raise OpenRouterActivityError("A API Analytics não disponibilizou a granularidade diária.")
    candidates = {
        "requests": (["request_count", "requests", "total_requests"], "requisições", True),
        "cost_total": (["total_usage", "openrouter_usage"], "custo de inferência (incluindo BYOK quando disponível)", True),
        "tokens_prompt": (["tokens_prompt", "prompt_tokens", "total_prompt_tokens", "input_tokens", "total_input_tokens"], "tokens de entrada", True),
        "tokens_completion": (["tokens_completion", "completion_tokens", "total_completion_tokens", "output_tokens", "total_output_tokens"], "tokens de saída", True),
        "tokens_reasoning": (["reasoning_tokens", "tokens_reasoning", "total_reasoning_tokens"], "tokens de raciocínio", False),
    }
    matched = {dest: select_metric(available, *spec) for dest, spec in candidates.items()}
    # O catálogo da API informa como apresentar cada métrica. Não exibir
    # a métrica selecionada como USD se o catálogo indicar unidade diferente.
    cost_metric = matched["cost_total"]
    if cost_metric != "total_usage":
        app.logger.warning(
            "Projeto %s: total_usage indisponível; usando %s. "
            "Os valores de custo podem não ser comparáveis entre projetos.",
            project_id or "não identificado", cost_metric,
        )
    cost_info = metric_details.get(cost_metric, {})
    display_format = str(cost_info.get("display_format") or "").lower()
    display_label = str(cost_info.get("display_label") or "").lower()
    allowed_currency = ("currency", "usd", "dollar", "money", "cost")
    if (display_format and not any(x in display_format for x in allowed_currency)
            and not any(x in display_label for x in ("cost", "spend", "usd", "usage"))):
        raise OpenRouterActivityError(
            f"A métrica '{cost_metric}' não foi identificada como custo em USD no catálogo. "
            f"Formato informado: {display_format!r}. Confirme a unidade antes de exibir valores."
        )
    app.logger.info("Analytics: custo usando %s (formato=%s, rótulo=%s)",
                    cost_metric, display_format or "não informado", display_label or "não informado")
    # Conjunto de métricas confirmado nos testes reais de setembro.
    # Métricas opcionais ficam fora até serem validadas separadamente.
    selected_dims = ["model"] if "model" in dimensions else []
    columns = [matched[name] for name in ("requests", "cost_total", "tokens_prompt", "tokens_completion")]
    matched["tokens_reasoning"] = None
    # O endpoint Analytics rejeitou consultas acima de 31 dias com estas
    # métricas/dimensões. Usamos intervalos [início, fim) de até 30 dias:
    # eles são contíguos, não se sobrepõem e cobrem a data final inclusiva.
    all_rows = []
    cursor = start
    exclusive_end = end + timedelta(days=1)
    while cursor < exclusive_end:
        chunk_end = min(cursor + timedelta(days=MAX_QUERY_DAYS), exclusive_end)
        payload = {
            "metrics": columns,
            "dimensions": selected_dims,
            "granularity": "day",
            "limit": MAX_QUERY_ROWS,
            "time_range": {
                "start": cursor.isoformat() + "T00:00:00Z",
                "end": chunk_end.isoformat() + "T00:00:00Z",
            },
        }
        try:
            response = cached_call(
                ("activity", project_id or key, cursor.isoformat(), chunk_end.isoformat(), tuple(columns), tuple(selected_dims)),
                lambda: api_json(ANALYTICS_QUERY_URL, key, payload)
            )
        except OpenRouterActivityError as exc:
            raise OpenRouterActivityError(
                f"Falha na consulta de {cursor:%d/%m/%Y} a "
                f"{(chunk_end - timedelta(days=1)):%d/%m/%Y}: {exc}"
            ) from None
        wrapper = response.get("data") if isinstance(response, dict) else None
        rows = wrapper.get("data") if isinstance(wrapper, dict) else None
        meta = wrapper.get("metadata", {}) if isinstance(wrapper, dict) else {}
        if not isinstance(rows, list):
            raise OpenRouterActivityError(
                f"Resposta inesperada entre {cursor} e {chunk_end}: "
                "campo data.data ausente ou inválido."
            )
        if not isinstance(meta, dict):
            raise OpenRouterActivityError("A API retornou metadados inválidos.")
        # Jamais juntar partes incompletas e apresentá-las como total consolidado.
        if meta.get("truncated") or len(rows) >= MAX_QUERY_ROWS:
            raise OpenRouterActivityError(
                f"A consulta de {cursor:%d/%m/%Y} a "
                f"{(chunk_end - timedelta(days=1)):%d/%m/%Y} pode estar "
                "incompleta (limite de linhas/truncamento). "
                "Selecione um período menor ou reduza a granularidade."
            )
        if "row_count" in meta and meta["row_count"] != len(rows):
            raise OpenRouterActivityError(
                f"Contagem de linhas inconsistente na consulta iniciada em {cursor}."
            )
        if any(not isinstance(row, dict) for row in rows):
            raise OpenRouterActivityError("A API retornou um agrupamento inválido.")
        app.logger.info("Analytics: %s a %s (%d grupos)",
                        cursor, chunk_end - timedelta(days=1), len(rows))
        all_rows.extend(rows)
        cursor = chunk_end
    # Os grupos devem ser disjuntos entre janelas; verifica a data retornada
    # para que ajustes futuros não produzam somas duplicadas ou incorretas.
    date_keys = ("created_at__day", "date__day", "created_at", "date", "day")
    for row in all_rows:
        date_key = next((field for field in date_keys if field in row), None)
        if date_key is None:
            raise OpenRouterActivityError("A resposta Analytics não contém a data do agrupamento.")
        timestamp = pd.to_datetime(row[date_key], utc=True, errors="coerce")
        if pd.isna(timestamp) or not (start <= timestamp.date() <= end):
            raise OpenRouterActivityError(
                "A API retornou um agrupamento fora do período selecionado."
            )
    app.logger.info("Analytics consolidada: %s a %s, %d grupos, métricas %s",
                    start, end, len(all_rows), ",".join(columns))
    return all_rows, matched, selected_dims

def fetch_extra_rows(start, end, key, project_id, metric_names, chunk_days=30):
    """Obtém métricas opcionais em blocos; falhas não interrompem o painel principal."""
    windows = []
    cursor = start
    exclusive_end = end + timedelta(days=1)
    while cursor < exclusive_end:
        stop = min(cursor + timedelta(days=chunk_days), exclusive_end)
        windows.append((cursor, stop))
        cursor = stop
    def query_window(window):
        beginning, stop = window
        payload = {
            "metrics": list(metric_names), "dimensions": ["model"],
            "granularity": "day", "limit": MAX_QUERY_ROWS,
            "time_range": {"start": beginning.isoformat() + "T00:00:00Z",
                           "end": stop.isoformat() + "T00:00:00Z"},
        }
        response = cached_call(
            ("optional", project_id, beginning.isoformat(), stop.isoformat(), tuple(metric_names)),
            lambda: api_json(ANALYTICS_QUERY_URL, key, payload),
        )
        wrapper = response.get("data", {}) if isinstance(response, dict) else {}
        rows = wrapper.get("data") if isinstance(wrapper, dict) else None
        meta = wrapper.get("metadata", {}) if isinstance(wrapper, dict) else {}
        if not isinstance(rows, list) or not isinstance(meta, dict):
            raise OpenRouterActivityError("Resposta opcional inválida")
        if meta.get("truncated") or len(rows) >= MAX_QUERY_ROWS:
            raise OpenRouterActivityError("Resposta opcional incompleta")
        if "row_count" in meta and meta["row_count"] != len(rows):
            raise OpenRouterActivityError("Contagem opcional inconsistente")
        return rows
    try:
        # A consulta principal conserva seu comportamento; métricas adicionais
        # são buscadas em paralelo para reduzir o tempo de espera inicial.
        with ThreadPoolExecutor(max_workers=min(len(windows), 4)) as executor:
            batches = list(executor.map(query_window, windows))
        return [row for batch in batches for row in batch]
    except OpenRouterActivityError as exc:
        app.logger.warning("Métrica opcional %s indisponível para %s: %s",
                           ','.join(metric_names), project_id, exc)
        return None

def extra_lookup(rows, columns):
    """Indexa a resposta por dia UTC e modelo; datas da API variam conforme métrica."""
    lookup = {}
    if rows is None:
        return lookup
    for row in rows:
        if not isinstance(row, dict):
            continue
        field = next((name for name in (
            "created_at__day", "date__day", "created_at", "date", "day") if name in row), None)
        if not field:
            continue
        date_value = pd.to_datetime(row[field], utc=True, errors="coerce")
        if pd.isna(date_value):
            continue
        k = (date_value.strftime("%Y-%m-%d"), str(row.get("model", "Não disponibilizado pela API")))
        values = lookup.setdefault(k, {})
        for column in columns:
            if column in row and row[column] is not None:
                number = pd.to_numeric(row[column], errors="coerce")
                if pd.notna(number):
                    if column == "avg_generation_time":
                        values[column] = float(number)
                    else:
                        values[column] = values.get(column, 0.0) + float(number)
    return lookup

def enrich_optional_metrics(frame, start, end, project_id):
    """Adiciona cache/raciocínio e média recente sem alterar valores financeiros.
    A API limita métricas de tempo a ~31 dias. Para período histórico longo,
    utiliza apenas os 30 dias mais recentes DO intervalo selecionado.
    A média ponderada por requisições é uma estimativa de apresentação.
    """
    frame = frame.copy()
    frame["cached_tokens"] = float('nan')
    frame["tokens_reasoning"] = float('nan')
    frame["avg_generation_time_ms"] = float('nan')
    if frame.empty:
        return frame
    key = configured_key(project_id)
    optional = fetch_extra_rows(start, end, key, project_id, ("cached_tokens", "reasoning_tokens"))
    counts = extra_lookup(optional, ("cached_tokens", "reasoning_tokens"))
    if optional is not None:
        for idx, row in frame.iterrows():
            values = counts.get((row["day"], row["model_permaslug"]))
            if values:
                for field, dest in (("cached_tokens", "cached_tokens"),
                                    ("reasoning_tokens", "tokens_reasoning")):
                    if field in values:
                        frame.at[idx, dest] = values[field]
    # O endpoint aceita consultas históricas de latência por no máximo ~31 dias.
    # Seleciona os 30 dias mais recentes dentro do filtro para não somar médias.
    # Latência depende de registros brutos: dados anteriores aos últimos ~31 dias
    # podem não existir, mesmo que o custo e os tokens históricos existam.
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
    recent_start = max(start, end - timedelta(days=29), cutoff)
    latency_rows = (fetch_extra_rows(recent_start, end, key, project_id,
                                     ("avg_generation_time",)) if recent_start <= end else [])
    latencies = extra_lookup(latency_rows, ("avg_generation_time",))
    if latency_rows is not None:
        for idx, row in frame.iterrows():
            if row["created_at"].date() < recent_start:
                continue
            value = latencies.get((row["day"], row["model_permaslug"]), {}).get("avg_generation_time")
            if value is not None:
                frame.at[idx, "avg_generation_time_ms"] = value
    return frame

def weighted_generation_time(df):
    """Estimativa ponderada por requisições dos grupos com tempo disponível."""
    if "avg_generation_time_ms" not in df:
        return None
    part = df.copy()
    lat = pd.to_numeric(part["avg_generation_time_ms"], errors="coerce")
    weights = pd.to_numeric(part["requests"], errors="coerce")
    valid = lat.notna() & weights.notna() & (weights > 0)
    if not valid.any():
        return None
    return float((lat[valid] * weights[valid]).sum() / weights[valid].sum())

def normalize_activity(items, matched, selected_dims):
    """Normaliza a resposta beta, distinguindo campos não disponíveis de valores zero."""
    columns = ["day", "created_at", "model_permaslug", "provider_name", "cost_total",
               "tokens_prompt", "tokens_completion", "tokens_reasoning", "total_tokens", "requests"]
    if not items:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(items)
    date_field = next((col for col in ("created_at__day", "date__day", "created_at", "date", "day") if col in df), None)
    if not date_field:
        raise OpenRouterActivityError("A API não retornou uma coluna de dia conhecida (created_at__day/date__day).")
    df["created_at"] = pd.to_datetime(df[date_field], utc=True, errors="coerce")
    df = df.dropna(subset=["created_at"]).copy()
    df["day"] = df["created_at"].dt.strftime("%Y-%m-%d")
    for dest, source in matched.items():
        if not source:
            df[dest] = 0  # Apenas para métrica opcional; o indicador ficará 'Indisponível'.
        elif source not in df:
            raise OpenRouterActivityError(f"A resposta Analytics não contém a coluna esperada: {source}.")
        else:
            df[dest] = pd.to_numeric(df[source], errors="coerce").fillna(0)
    df["requests"] = df["requests"].clip(lower=0)
    df["model_permaslug"] = df["model"].fillna("Não informado").astype(str) if "model" in selected_dims and "model" in df else "Não disponibilizado pela API"
    df["provider_name"] = df["provider"].fillna("Não informado").astype(str) if "provider" in selected_dims and "provider" in df else "Não disponibilizado pela API"
    df["total_tokens"] = df["tokens_prompt"] + df["tokens_completion"]
    return df

def filter_data(df, args):
    """Filtro exclusivo de data, inclusivo, usando datas UTC da API."""
    start = args.get("start", "").strip()
    end = args.get("end", "").strip()
    for name, raw in (("start", start), ("end", end)):
        if raw:
            try:
                pd.to_datetime(raw, format="%Y-%m-%d", errors="raise")
            except (ValueError, TypeError):
                if name == "start":
                    start = ""
                else:
                    end = ""
    if start:
        df = df[df["day"] >= start]
    if end:
        df = df[df["day"] <= end]
    return df, {"start": start, "end": end}

def fmt_int(number):
    return f"{int(number):,}".replace(",", ".")

def fmt_usd(number, decimals=2):
    return f"US$ {number:,.{decimals}f}"

def style_chart(fig, height=320, margin=None, legend=False):
    """Estilo consistente sem substituições conflitantes no JavaScript."""
    fig.update_layout(
        template="plotly_white", height=height, autosize=True,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Sans, system-ui, Arial", color="#65728a", size=12),
        margin=margin or dict(l=57, r=22, t=22, b=46),
        showlegend=legend,
        legend=dict(orientation="h", y=-0.23, x=0, font=dict(size=11)),
        hoverlabel=dict(bgcolor="#ffffff", font_color="#242a40", bordercolor="#e4e7f2"),
        xaxis=dict(gridcolor="#edf0f6", zeroline=False, automargin=True),
        yaxis=dict(gridcolor="#edf0f6", zeroline=False, automargin=True),
        hovermode="closest",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"displayModeBar": True, "responsive": True,
                               "displaylogo": False, "scrollZoom": False,
                               "doubleClick": "reset",
                               "modeBarButtonsToRemove": ["select2d", "lasso2d", "toImage", "autoScale2d"]})

def timeline_labels(days):
    """Eixo categórico evita datas duplicadas quando só há um ou poucos pontos.
    Para períodos longos exibimos rótulos mais espaçados sem omitir dados.
    """
    dates = pd.to_datetime(days)
    return dates.dt.strftime("%d/%m").tolist()

def compact_axis(fig, axis="y", is_money=False, maximum=0):
    """Preserva centavos em custos pequenos e simplifica números grandes."""
    if is_money:
        fmt = ".4f" if maximum < 0.1 else (".3f" if maximum < 1 else ".2f")
        layout = dict(tickprefix="US$ ", tickformat=fmt, separatethousands=True,
                      rangemode="tozero", nticks=5)
    else:
        layout = dict(tickformat="~s", rangemode="tozero", nticks=5)
    (fig.update_yaxes if axis == "y" else fig.update_xaxes)(**layout)
# Cores padronizadas no dashboard inteiro.
PROJECT_COLORS = {"Instant Doctor": "#26AA9D", "Two": "#7768DC", "Fracta": "#3785E8"}
PROJECT_LIGHT_COLORS = {"Instant Doctor": "#8AD9CE", "Two": "#B8ADF5", "Fracta": "#9BC6FA"}

def abbreviated(value):
    value = float(value)
    if value >= 1_000_000_000:
        return f"{value/1_000_000_000:.2f} bi".replace('.', ',')
    if value >= 1_000_000:
        return f"{value/1_000_000:.2f} mi".replace('.', ',')
    if value >= 1_000:
        return f"{value/1_000:.1f} mil".replace('.', ',')
    return fmt_int(value)

def grouped_time_series(df, fields):
    """Escolhe granularidade visual sem modificar os totais agregados.
    Até 45 dias: diário; 46-100 dias: semanal; acima: mensal.
    Rótulos não repetem em períodos longos nem inventam dias sem dados.
    """
    first = pd.to_datetime(df['day']).min()
    last = pd.to_datetime(df['day']).max()
    n_days = (last - first).days + 1
    if n_days > 100:
        kind = 'mensal'
        period = pd.to_datetime(df['day']).dt.to_period('M').astype(str)
        label = lambda p: f"{p[5:7]}/{p[:4]}"
    elif n_days > 45:
        kind = 'semanal'
        period = (pd.to_datetime(df['day']) - pd.to_timedelta(pd.to_datetime(df['day']).dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')
        label = lambda p: pd.to_datetime(p).strftime('%d/%m')
    else:
        kind = 'diário'
        period = df['day']
        label = lambda p: pd.to_datetime(p).strftime('%d/%m')
    work = df.assign(_period=period)
    columns = ['_period'] + (['project_name'] if 'project_name' in work else [])
    daily = work.groupby(columns, as_index=False)[fields].sum().sort_values('_period')
    daily['label'] = daily['_period'].map(label)
    return daily, kind

def time_chart_axes(fig, labels, money=False, maximum=0):
    fig.update_xaxes(type='category', categoryorder='array', categoryarray=labels,
                     tickangle=0, nticks=min(len(labels), 9), showgrid=False,
                     tickfont=dict(size=11))
    # Um ou dois dias não devem ficar espremidos em um eixo de meses.
    if len(labels) <= 3:
        fig.update_xaxes(range=[-.7, max(len(labels)-1, 0)+.7])
    compact_axis(fig, is_money=money, maximum=maximum)
    fig.update_yaxes(gridcolor='#edf0f6')

def project_legend(fig, legend=True):
    fig.update_layout(
        legend=dict(orientation='h', x=0, y=1.12, xanchor='left', yanchor='bottom',
                    font=dict(size=12)), showlegend=legend,
        hovermode='x unified', bargap=.26, bargroupgap=.09,
    )

def build_charts(df):
    """Em Todos, cada série pertence a UM projeto. Na visão individual, usa suas cores.
    Gráficos temporais condensam períodos longos em semanas ou meses, sem
    alterar o total do indicador nem mascarar a origem dos dados.
    """
    is_all = df['project_name'].nunique() > 1
    # Uma consulta Todos em agosto contém apenas Two; continua sendo exibida
    # no modo comparativo ao usar a flag de contexto atribuída na rota.
    is_all = bool('_view_mode' in df and df['_view_mode'].iloc[0] == PROJECT_ALL)
    names = [settings["label"] for settings in PROJECTS.values()] if is_all else [str(df['project_name'].iloc[0])]
    ordered = names
    df = df.drop(columns=['_view_mode'], errors='ignore').copy()
    time_data, resolution = grouped_time_series(
        df, ['cost_total','tokens_prompt','tokens_completion','requests'])
    labels = list(dict.fromkeys(time_data['label']))
    charts = {'period_label': {'diário':'dia','semanal':'semana','mensal':'mês'}[resolution],
              'compare_mode': is_all}
    def by_project(field, period_df=time_data):
        return (period_df.pivot_table(index='label',columns='project_name',
                values=field,aggfunc='sum',fill_value=0)
                .reindex(index=labels, columns=ordered,fill_value=0))
    def amounts(fig, metric, chart_type='bar'):
        table = by_project(metric)
        for name in ordered:
            values=table[name].to_numpy()
            color=PROJECT_COLORS[name]
            common=dict(x=labels,y=values,name=name,
                        hovertemplate=f'<b>{name}</b><br>%{{x}}<br>'+('US$ %{y:,.4f}' if metric=='cost_total' else '%{y:,.0f}')+'<extra></extra>')
            if chart_type=='line':
                fig.add_trace(go.Scatter(**common,mode='lines+markers',
                     line=dict(color=color,width=2.8),marker=dict(size=6)))
            else:
                labels_text = ([f'US$ {v:,.2f}' if metric == 'cost_total' else abbreviated(v)
                                for v in values] if len(labels) <= 5 else None)
                fig.add_trace(go.Bar(**common,marker_color=color,
                    width=.36 if is_all else .55,
                    text=labels_text,textposition='outside',cliponaxis=False))
        project_legend(fig,legend=is_all)
        if len(labels) <= 5:
            fig.update_layout(bargap=.28)
        project_legend(fig,legend=is_all)
        time_chart_axes(fig,labels,money=metric=='cost_total',
                        maximum=float(table.to_numpy().max()))
        if chart_type=='bar': fig.update_layout(barmode='group')
        return fig
    costs=go.Figure()
    if is_all:
        amounts(costs,'cost_total',chart_type='line' if len(labels)>14 else 'bar')
    else:
        amounts(costs,'cost_total',chart_type='line' if len(labels)>10 else 'bar')
    charts['cost']=style_chart(costs,345,dict(l=78,r=18,t=51,b=44),legend=is_all)
    tokens=go.Figure()
    if is_all:
        table=time_data.assign(total_tokens=lambda t:t['tokens_prompt']+t['tokens_completion'])
        pivot=table.pivot_table(index='label',columns='project_name',values='total_tokens',aggfunc='sum',fill_value=0).reindex(index=labels,columns=ordered,fill_value=0)
        for name in ordered:
            tokens.add_trace(go.Bar(x=labels,y=pivot[name],name=name,
                marker_color=PROJECT_COLORS[name],
                hovertemplate=f'<b>{name}</b><br>%{{x}}<br>%{{y:,.0f}} tokens<extra></extra>'))
        tokens.update_layout(barmode='group')
        project_legend(tokens)
        time_chart_axes(tokens,labels,maximum=float(pivot.to_numpy().max()))
    else:
        name=ordered[0]
        part=time_data.groupby('label',as_index=False)[['tokens_prompt','tokens_completion']].sum().set_index('label').reindex(labels,fill_value=0)
        for field,title,color in [('tokens_prompt','Entrada',PROJECT_COLORS[name]),('tokens_completion','Saída',PROJECT_LIGHT_COLORS[name])]:
            tokens.add_trace(go.Bar(x=labels,y=part[field],name=title,marker_color=color,
                  hovertemplate=f'<b>{title}</b><br>%{{x}}<br>%{{y:,.0f}} tokens<extra></extra>'))
        tokens.update_layout(barmode='group')
        project_legend(tokens)
        time_chart_axes(tokens,labels,maximum=float(part.to_numpy().max()))
    charts['usage']=style_chart(tokens,345,dict(l=70,r=18,t=51,b=47),legend=True)
    requests=go.Figure()
    amounts(requests,'requests',chart_type='line' if len(labels)>20 else 'bar')
    charts['requests_daily']=style_chart(requests,335,dict(l=70,r=20,t=48,b=42),legend=is_all)
    def model_comparison(metric, money=False):
        top=(df.groupby('model_permaslug')[metric].sum().nlargest(7))
        models=top.index.tolist()[::-1]
        fig=go.Figure()
        if is_all:
            grouped=(df.groupby(['model_permaslug','project_name'],as_index=False)[metric].sum())
            for name in ordered:
                piece=grouped[grouped['project_name']==name].set_index('model_permaslug')
                amounts=[float(piece.at[model,metric]) if model in piece.index else 0 for model in models]
                fig.add_trace(go.Bar(x=amounts,y=models,name=name,orientation='h',
                         marker_color=PROJECT_COLORS[name],
                         hovertemplate=f'<b>{name}</b><br>%{{y}}<br>'+('US$ %{x:,.4f}' if money else '%{x:,.0f} tokens')+'<extra></extra>'))
            fig.update_layout(barmode='group')
        else:
            name=ordered[0]
            vals=top.reindex(models).values
            fig.add_trace(go.Bar(x=vals,y=models,name=name,orientation='h',
                marker_color=PROJECT_COLORS[name],
                text=[f'US$ {v:,.2f}' if money else abbreviated(v) for v in vals],
                textposition='outside',cliponaxis=False,
                hovertemplate='%{y}<br>'+('US$ %{x:,.4f}' if money else '%{x:,.0f} tokens')+'<extra></extra>'))
        fig.update_yaxes(type='category',categoryorder='array',categoryarray=models,
                         automargin=True,showgrid=False,tickfont=dict(size=11))
        maximum=float(top.max()) if len(top) else 0
        compact_axis(fig,axis='x',is_money=money,maximum=maximum)
        fig.update_xaxes(range=[0,max(maximum*1.27,0.000001 if money else 1)],
                         gridcolor='#edf0f6')
        project_legend(fig,legend=is_all)
        height=max(345,len(models)*62+90)
        return style_chart(fig,height,dict(l=280,r=60,t=52 if is_all else 18,b=44),legend=is_all)
    charts['model']=model_comparison('total_tokens')
    charts['cost_by_model']=model_comparison('cost_total',money=True)
    # Mostrar também modelos COMUNS, mesmo que não estejam no Top 7 geral.
    charts['shared_models'] = None
    charts['shared_models_count'] = 0
    if is_all:
        count_by_model = df.groupby('model_permaslug')['project_name'].nunique()
        shared = count_by_model[count_by_model >= 2].index
        charts['shared_models_count'] = len(shared)
        if len(shared):
            shared_data = df[df['model_permaslug'].isin(shared)].groupby(
                ['model_permaslug','project_name'],as_index=False)['total_tokens'].sum()
            top_names = (shared_data.groupby('model_permaslug')['total_tokens']
                         .sum().nlargest(8).index.tolist()[::-1])
            fig = go.Figure()
            for name in ordered:
                values = (shared_data[shared_data['project_name']==name]
                          .set_index('model_permaslug')['total_tokens']
                          .reindex(top_names,fill_value=0).tolist())
                fig.add_trace(go.Bar(x=values,y=top_names,orientation='h',name=name,
                    marker_color=PROJECT_COLORS[name],
                    hovertemplate=f'<b>{name}</b><br>%{{y}}<br>%{{x:,.0f}} tokens<extra></extra>'))
            fig.update_layout(barmode='group')
            fig.update_yaxes(type='category',categoryorder='array',categoryarray=top_names,
                             automargin=True,showgrid=False)
            compact_axis(fig,axis='x')
            project_legend(fig)
            charts['shared_models'] = style_chart(
                fig,max(350,70*len(top_names)+95),
                dict(l=285,r=40,t=54,b=50),legend=True)
    return charts

def project_comparison(df):
    """Compara projetos, inclusive quando um deles não possui atividade no mês."""
    grouped = df.groupby("project_name", as_index=False)[
        ["cost_total", "tokens_prompt", "tokens_completion", "requests"]
    ].sum() if not df.empty else pd.DataFrame(columns=["project_name", "cost_total", "tokens_prompt", "tokens_completion", "requests"])
    by_name = grouped.set_index("project_name")
    labels = [settings["label"] for settings in PROJECTS.values()]
    colors = [PROJECT_COLORS[name] for name in labels]
    def vals(column):
        return [float(by_name.at[name, column]) if name in by_name.index else 0.0 for name in labels]
    charts = {}
    for key, column, money in [
        ("cost", "cost_total", True),
        ("tokens", None, False),
        ("requests", "requests", False),
    ]:
        values = ([sum(t) for t in zip(vals("tokens_prompt"), vals("tokens_completion"))]
                  if column is None else vals(column))
        fig = go.Figure(go.Bar(
            x=labels, y=values, marker_color=colors, width=.5,
            text=[(f"US$ {v:,.2f}" if v >= .01 else f"US$ {v:,.4f}") if money
                  else (f"{v/1_000_000:.2f} mi" if v >= 1_000_000 else f"{v:,.0f}") for v in values],
            textposition="outside", cliponaxis=False,
            hovertemplate="%{x}<br>" + ("US$ %{y:,.4f}" if money else "%{y:,.0f}") + "<extra></extra>",
        ))
        fig.update_xaxes(type="category", showgrid=False)
        compact_axis(fig, is_money=money, maximum=max(values or [0]))
        fig.update_yaxes(range=[0, max(values + [.000001]) * 1.28])
        charts[key] = style_chart(fig, height=310, margin=dict(l=80, r=23, t=43, b=50))
    # Evolução comparativa: barras diárias lado a lado, no mesmo eixo de datas.
    per_day = (df.groupby(["day", "project_name"], as_index=False)["cost_total"].sum()
               if not df.empty else pd.DataFrame(columns=["day", "project_name", "cost_total"]))
    evolution = go.Figure()
    for name, color in zip(labels, colors):
        part = per_day[per_day["project_name"] == name]
        evolution.add_trace(go.Bar(
            x=part["day"], y=part["cost_total"], name=name, marker_color=color,
            hovertemplate=f"{name}<br>%{{x}}<br>US$ %{{y:.4f}}<extra></extra>"
        ))
    evolution.update_layout(barmode="group", bargap=.30, hovermode="x unified")
    # Em julho só há um ou dois dias: eixo categórico e colunas visíveis.
    unique_days = sorted(per_day['day'].unique().tolist()) if not per_day.empty else []
    if len(unique_days) <= 12:
        for trace in evolution.data:
            trace.x = [pd.to_datetime(v).strftime('%d/%m') for v in trace.x]
            trace.width = .35 if len(unique_days) <= 3 else None
        evolution.update_xaxes(type="category", categoryorder="array",
                               categoryarray=[pd.to_datetime(v).strftime('%d/%m') for v in unique_days],
                               showgrid=False)
        if len(unique_days) <= 3:
            evolution.update_xaxes(range=[-.7,max(len(unique_days)-1,0)+.7])
    else:
        evolution.update_xaxes(type="date", tickformat="%d/%m", nticks=9, showgrid=False)
    compact_axis(evolution, is_money=True, maximum=float(per_day["cost_total"].max()) if not per_day.empty else 0)
    charts["evolution"] = style_chart(evolution, height=340, margin=dict(l=78, r=20, t=18, b=54), legend=True)
    comparison = []
    for name in labels:
        cost = float(by_name.at[name, "cost_total"]) if name in by_name.index else 0.0
        tokens = sum(float(by_name.at[name, c]) for c in ("tokens_prompt", "tokens_completion")) if name in by_name.index else 0
        req = float(by_name.at[name, "requests"]) if name in by_name.index else 0
        comparison.append({"name": name, "cost": fmt_usd(cost), "tokens": fmt_int(tokens), "requests": fmt_int(req)})
    total_cost = sum(float(by_name.at[name, "cost_total"]) if name in by_name.index else 0.0
                     for name in labels)
    for item in comparison:
        value = float(by_name.at[item["name"], "cost_total"]) if item["name"] in by_name.index else 0.0
        item["cost_share"] = round(value / total_cost * 100, 1) if total_cost > 0 else 0.0
        item["color_class"] = next(
            pid for pid, settings in PROJECTS.items() if settings["label"] == item["name"]
        )
    return charts, comparison

def costs_last_15_days_chart(df, end_date, selected_ids=None):
    """Barras de custo diário dos projetos selecionados nos 15 dias até 'Até' (inclusive).

    A lista de datas é completa, inclusive dias sem consumo. Usa a mesma
    métrica cost_total (total_usage) do painel financeiro.
    """
    first_day = end_date - timedelta(days=14)
    dates = pd.date_range(first_day, end_date, freq="D")
    days = dates.strftime("%Y-%m-%d").tolist()
    labels = dates.strftime("%d/%m").tolist()
    if df.empty:
        totals = pd.DataFrame(columns=["day", "project_name", "cost_total"])
    else:
        totals = df.groupby(["day", "project_name"], as_index=False)["cost_total"].sum()
    fig = go.Figure()
    for project_id in (selected_ids if selected_ids is not None else PROJECTS):
        name = PROJECTS[project_id]["label"]
        series = (totals[totals["project_name"] == name]
                  .set_index("day")["cost_total"].reindex(days, fill_value=0).astype(float))
        fig.add_trace(go.Bar(
            x=labels, y=series.tolist(), name=name,
            marker_color=PROJECT_COLORS[name],
            customdata=days,
            hovertemplate=f"<b>{name}</b><br>Dia: %{{customdata}}<br>Custo: US$ %{{y:,.4f}}<extra></extra>"
        ))
    fig.update_layout(
        barmode="group", bargap=0.23, bargroupgap=0.045,
        hovermode="x unified",
    )
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=labels,
                     title_text="Dia (UTC)", showgrid=False, tickangle=0,
                     automargin=True)
    maximum = float(totals["cost_total"].max()) if not totals.empty else 0.0
    compact_axis(fig, is_money=True, maximum=maximum)
    return style_chart(fig, height=400, margin=dict(l=85, r=24, t=30, b=85), legend=True)


@app.get("/")
@login_required
def index():
    """Mostra agregados da API e mantém nomes das variáveis Jinja atuais.
    A tabela contém grupos diários agregados, não gerações individuais.
    """
    # Campos vazios mantêm a visão geral e não obrigam seleção de datas.
    filters = {"start": request.args.get("start", "").strip(),
               "end": request.args.get("end", "").strip(),
               "project": request.args.get("project", "").strip(),
               "month": request.args.get("month", "").strip()}
    project_label = PROJECT_NAME
    try:
        chosen, project_selection = chosen_projects(filters["project"])
        start, end = date_window(request.args, chosen)
        project_label = ("Todos os projetos" if project_selection == PROJECT_ALL
                         else PROJECTS[project_selection]["label"])
        matched = {"tokens_reasoning": None}
        def load_project(project_id):
            project_start = datetime.strptime(PROJECTS[project_id]["start_date"], "%Y-%m-%d").date()
            query_start = max(start, project_start)
            if query_start > end:
                partial = pd.DataFrame(columns=[
                    "day", "created_at", "model_permaslug", "provider_name", "cost_total",
                    "tokens_prompt", "tokens_completion", "tokens_reasoning", "total_tokens", "requests",
                    "cached_tokens", "avg_generation_time_ms",
                ])
                local_matched = {"tokens_reasoning": None}
            else:
                try:
                    items, local_matched, selected_dims = fetch_activity(
                        query_start, end, configured_key(project_id), project_id
                    )
                    partial = normalize_activity(items, local_matched, selected_dims)
                    partial = enrich_optional_metrics(partial, query_start, end, project_id)
                except OpenRouterActivityError as exc:
                    raise OpenRouterActivityError(
                        f"Falha ao consultar {PROJECTS[project_id]['label']}: {exc}"
                    ) from None
            partial["project_id"] = project_id
            partial["project_name"] = PROJECTS[project_id]["label"]
            return partial, local_matched
        # Para a visão geral, as duas fontes são independentes e podem ser consultadas em paralelo.
        if len(chosen) > 1:
            with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
                outcomes = list(pool.map(load_project, chosen))
        else:
            outcomes = [load_project(chosen[0])]
        frames = [value[0] for value in outcomes]
        matched = outcomes[-1][1]
        # Compare todos os pares de origens antes de somar dados.
        check_cols = ["day", "model_permaslug", "requests", "tokens_prompt",
                      "tokens_completion", "cost_total"]
        for left_idx in range(len(frames)):
            for right_idx in range(left_idx + 1, len(frames)):
                if frames[left_idx].empty or frames[right_idx].empty:
                    continue
                a = frames[left_idx][check_cols].sort_values(check_cols[:2]).reset_index(drop=True)
                b = frames[right_idx][check_cols].sort_values(check_cols[:2]).reset_index(drop=True)
                if a.equals(b):
                    raise OpenRouterActivityError(
                        "Dois projetos retornaram exatamente os mesmos agregados. "
                        "Confirme o isolamento das credenciais antes de somar."
                    )
        # Não concatene DataFrames vazios com tipos genéricos: isso converte números em object
        # e causa erro 500 em gráficos de meses anteriores ao início de um projeto.
        present_frames = [frame for frame in frames if not frame.empty]
        if present_frames:
            raw = pd.concat(present_frames, ignore_index=True)
        else:
            raw = frames[0].copy()
        for col in ("requests", "cost_total", "tokens_prompt", "tokens_completion", "total_tokens"):
            raw[col] = pd.to_numeric(raw[col], errors="coerce").fillna(0)
        # Ausência de métrica opcional NÃO equivale a zero.
        for col in ("tokens_reasoning", "cached_tokens", "avg_generation_time_ms"):
            if col not in raw:
                raw[col] = float('nan')
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
        filtered = raw
        last_15_chart = None
        last_15_start = end - timedelta(days=14)
        # O campo 'De' continua controlando os indicadores antigos. Para o
        # gráfico de 15 dias, buscamos separadamente somente dias anteriores
        # a 'De' quando esse campo restringe a consulta principal.
        existing = filtered[(filtered["day"] >= last_15_start.isoformat()) &
                            (filtered["day"] <= end.isoformat())].copy()
        extra_frames = []
        if start > last_15_start:
            def extra_15_days(project_id):
                project_start = datetime.strptime(PROJECTS[project_id]["start_date"], "%Y-%m-%d").date()
                missing_start = max(last_15_start, project_start)
                missing_end = min(start - timedelta(days=1), end)
                if missing_start > missing_end:
                    return None
                try:
                    items, metric_map, dimensions = fetch_activity(
                        missing_start, missing_end, configured_key(project_id), project_id
                    )
                    result = normalize_activity(items, metric_map, dimensions)
                except OpenRouterActivityError as exc:
                    raise OpenRouterActivityError(
                        f"Falha ao consultar os últimos 15 dias de {PROJECTS[project_id]['label']}: {exc}"
                    ) from None
                result["project_id"] = project_id
                result["project_name"] = PROJECTS[project_id]["label"]
                return result
            with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
                extra_frames = [frame for frame in pool.map(extra_15_days, chosen)
                                if frame is not None and not frame.empty]
        pieces = ([existing] if not existing.empty else []) + extra_frames
        last_15_data = (pd.concat(pieces, ignore_index=True) if pieces
                        else pd.DataFrame(columns=["day", "project_name", "cost_total"]))
        last_15_chart = costs_last_15_days_chart(last_15_data, end, chosen)
        scopes_verified = True  # Identidade de projeto informada pelo responsável; valores ainda dependem de conferência no painel oficial.
    except OpenRouterActivityError as exc:
        return render_template("index.html", project=project_label, dashboard=None,
                               error=str(exc), filters=filters, providers=[], models=[],
                               project_options=PROJECTS, selected_project=filters["project"],
                               theme=PROJECT_THEMES.get(filters["project"], "theme-all"),
                               month_options=months_for_filter(), period_start="", period_end=""), 503
    page_arg = request.args.get("page", "1")
    try:
        page = max(1, int(page_arg))
    except (ValueError, TypeError):
        page = 1
    count = len(filtered)  # Quantidade de grupos, não quantidade de requisições.
    per_page = 15
    pages = max(1, math.ceil(count / per_page))
    page = min(page, pages)
    recent = (filtered.sort_values(["created_at", "model_permaslug"], ascending=[False, True])
              .iloc[(page - 1) * per_page:page * per_page])
    records = [{
        "date": row.created_at.strftime("%d/%m/%Y"),
        "project": row.project_name,
        "model": row.model_permaslug,
        "provider": row.provider_name,
        "input": fmt_int(row.tokens_prompt),
        "output": fmt_int(row.tokens_completion),
        "cost": fmt_usd(row.cost_total, 5),
        "latency": "—",  # Não solicitamos latência na Analytics.
        "requests": fmt_int(row.requests),
    } for row in recent.itertuples(index=False)]
    dashboard = None
    compare_charts = None
    compare_rows = None
    comparison_note = None
    if project_selection == PROJECT_ALL:
        compare_charts, compare_rows = project_comparison(filtered)
        present = set(filtered['project_name'].unique()) if not filtered.empty else set()
        missing_labels = [cfg['label'] for cfg in PROJECTS.values() if cfg['label'] not in present]
        if missing_labels:
            comparison_note = ("Sem atividade retornada neste período para: "
                               + ", ".join(missing_labels) + ".")
    if not filtered.empty:
        input_tokens = int(filtered["tokens_prompt"].sum())
        output_tokens = int(filtered["tokens_completion"].sum())
        requests_total = int(filtered["requests"].sum())
        total_cost = float(filtered["cost_total"].sum())
        dashboard = dict(
            total=fmt_int(input_tokens + output_tokens),
            input=fmt_int(input_tokens), output=fmt_int(output_tokens),
            cost=fmt_usd(total_cost), cost_precise=fmt_usd(total_cost, 4),
            requests=fmt_int(requests_total),
            avg_latency=(f"{weighted_generation_time(filtered) / 1000:.2f} s"
                         if weighted_generation_time(filtered) is not None else "Sem dados no período"),
            latency_note=("Estimativa ponderada por requisições, com dados disponíveis "
                          "dos últimos 30 dias UTC; não representa necessariamente todo o histórico"),
            cached=(fmt_int(filtered["cached_tokens"].sum())
                    if filtered["cached_tokens"].notna().any() else "Não disponível no período"),
            reasoning=(fmt_int(filtered["tokens_reasoning"].sum())
                       if filtered["tokens_reasoning"].notna().any() else "Não disponível no período"),
            avg_cost=fmt_usd(total_cost / requests_total, 4) if requests_total else "—",
            span=(filtered["created_at"].min().strftime("%d/%m/%Y"),
                  filtered["created_at"].max().strftime("%d/%m/%Y")),
            charts=build_charts(filtered.assign(_view_mode=project_selection)),
        )
    return render_template(
        "index.html", project=project_label, error=None, dashboard=dashboard,
        filters=filters, providers=[], models=[], records=records,
        project_options=PROJECTS, selected_project=project_selection,
        theme=PROJECT_THEMES.get(project_selection, "theme-all"),
        scopes_verified=scopes_verified, compare_charts=compare_charts,
        comparison_note=comparison_note,
        last_15_chart=last_15_chart, last_15_start=(end - timedelta(days=14)).strftime("%d/%m/%Y"),
        last_15_end=end.strftime("%d/%m/%Y"),
        compare_rows=compare_rows, month_options=months_for_filter(),
        all_projects_ready=all(configured_key(pid) for pid in PROJECTS),
        count=count, page=page, pages=pages,
        first=((page - 1) * per_page + 1) if count else 0,
        last=min(page * per_page, count),
        total_records=int(raw["requests"].sum()) if not raw.empty else 0,
        initial_date=raw["created_at"].min().strftime("%d/%m/%Y") if not raw.empty else "—",
        final_date=raw["created_at"].max().strftime("%d/%m/%Y") if not raw.empty else "—",
        source_name="OpenRouter Analytics (beta)",
        source_note=f"Período consultado: {start} a {end} (UTC)",
        period_start=start.isoformat(), period_end=end.isoformat(),
        is_aggregated=True,
    )

@app.get("/health")
@login_required
def health():
    return jsonify(status="ok", source="openrouter_analytics_api",
                   projects={pid: bool(configured_key(pid)) for pid in PROJECTS})

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1")
