import argparse
import csv
import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import unicodedata
from urllib import error, parse, request


COLUMNS = [
    "orgao_comprador",
    "objeto",
    "quantidade",
    "valor_estimado",
    "data_abertura",
    "link_edital",
    "fonte",
    "data_captura",
]


@dataclass
class Licitacao:
    orgao_comprador: str
    objeto: str
    quantidade: str
    valor_estimado: str
    data_abertura: str
    link_edital: str
    fonte: str
    data_captura: str
    unique_source_id: str
    status: str = ""

    def as_csv_row(self) -> Dict[str, str]:
        return {
            "orgao_comprador": self.orgao_comprador,
            "objeto": self.objeto,
            "quantidade": self.quantidade,
            "valor_estimado": self.valor_estimado,
            "data_abertura": self.data_abertura,
            "link_edital": self.link_edital,
            "fonte": self.fonte,
            "data_captura": self.data_captura,
        }


class SourceIngestor(ABC):
    def __init__(self, name: str, source_config: Dict[str, Any], capture_date: str) -> None:
        self.name = name
        self.source_config = source_config
        self.capture_date = capture_date

    @abstractmethod
    def fetch(self) -> List[Licitacao]:
        raise NotImplementedError


class PncpIngestor(SourceIngestor):
    def fetch(self) -> List[Licitacao]:
        base_url = str(self.source_config.get("base_url", "")).strip()
        if not base_url:
            raise ValueError("Fonte PNCP habilitada sem 'base_url' no arquivo de configuracao.")

        page_size = int(self.source_config.get("page_size", 50))
        max_pages = int(self.source_config.get("max_pages", 5))
        modalidade_codes_raw = self.source_config.get("codigo_modalidade_contratacao", [10, 11, 12, 13, 14])
        timeout_seconds = int(self.source_config.get("timeout_seconds", 30))
        window_days = int(self.source_config.get("window_days", 30))
        query_chunk_days = int(self.source_config.get("query_chunk_days", 7))
        request_retries = int(self.source_config.get("request_retries", 2))
        max_consecutive_failures = int(self.source_config.get("max_consecutive_failures", 2))
        only_open = bool(self.source_config.get("only_open", True))
        fail_on_unavailable = bool(self.source_config.get("fail_on_unavailable", False))

        if page_size < 10 or max_pages <= 0:
            raise ValueError(
                "Configuracao PNCP invalida: 'page_size' deve ser >= 10 e 'max_pages' deve ser maior que zero."
            )
        if not isinstance(modalidade_codes_raw, list) or not modalidade_codes_raw:
            raise ValueError(
                "Configuracao PNCP invalida: 'codigo_modalidade_contratacao' deve ser uma lista nao vazia."
            )
        if query_chunk_days < 1:
            raise ValueError("Configuracao PNCP invalida: 'query_chunk_days' deve ser maior que zero.")
        if request_retries < 1:
            raise ValueError("Configuracao PNCP invalida: 'request_retries' deve ser maior que zero.")
        if max_consecutive_failures < 1:
            raise ValueError("Configuracao PNCP invalida: 'max_consecutive_failures' deve ser maior que zero.")

        capture = datetime.strptime(self.capture_date, "%Y-%m-%d").date()
        date_ranges = self._build_date_ranges(capture, window_days, query_chunk_days)
        modalidade_codes = [int(x) for x in modalidade_codes_raw]

        licitacoes: List[Licitacao] = []
        request_errors: List[str] = []
        successful_requests = 0
        consecutive_failures = 0
        for modalidade_code in modalidade_codes:
            for range_start, range_end in date_ranges:
                data_inicial = range_start.strftime("%Y%m%d")
                data_final = range_end.strftime("%Y%m%d")
                for page in range(1, max_pages + 1):
                    query = parse.urlencode(
                        {
                            "dataInicial": data_inicial,
                            "dataFinal": data_final,
                            "codigoModalidadeContratacao": modalidade_code,
                            "pagina": page,
                            "tamanhoPagina": page_size,
                        }
                    )
                    url = f"{base_url}?{query}"
                    try:
                        payload = self._get_json(url, timeout_seconds, request_retries)
                    except RuntimeError as exc:
                        request_errors.append(str(exc))
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            break
                        break
                    consecutive_failures = 0
                    successful_requests += 1
                    items = self._extract_items(payload)

                    if not items:
                        break

                    for item in items:
                        licitacao = self._normalize_item(item)
                        if licitacao is not None:
                            if only_open and not self._is_open_status(licitacao.status):
                                continue
                            licitacoes.append(licitacao)
                if consecutive_failures >= max_consecutive_failures:
                    break
            if consecutive_failures >= max_consecutive_failures:
                break

        if successful_requests == 0:
            details = "; ".join(request_errors[:3])
            message = (
                "Falha ao consultar PNCP em todas as tentativas."
                + (f" Detalhes: {details}" if details else "")
            )
            if fail_on_unavailable:
                raise RuntimeError(message)
            print(f"AVISO: {message}")
            return []

        return licitacoes

    def _get_json(self, url: str, timeout_seconds: int, request_retries: int) -> Any:
        req = request.Request(url=url, headers={"Accept": "application/json", "User-Agent": "licitacao-monitor/1.0"})
        attempts = request_retries
        last_exception: Optional[Exception] = None
        raw = b""
        for attempt in range(1, attempts + 1):
            try:
                with request.urlopen(req, timeout=timeout_seconds) as response:
                    status = getattr(response, "status", None)
                    if status is not None and status >= 400:
                        raise RuntimeError(f"Falha HTTP ao consultar PNCP: status {status} em {url}")
                    raw = response.read()
                    last_exception = None
                    break
            except error.HTTPError as exc:
                last_exception = exc
                if exc.code < 500 or attempt == attempts:
                    raise RuntimeError(f"Falha HTTP ao consultar PNCP: status {exc.code} em {url}") from exc
            except error.URLError as exc:
                last_exception = exc
                if attempt == attempts:
                    raise RuntimeError(f"Falha de rede ao consultar PNCP em {url}: {exc.reason}") from exc
            except TimeoutError as exc:
                last_exception = exc
                if attempt == attempts:
                    raise RuntimeError(f"Timeout ao consultar PNCP em {url}") from exc
            time.sleep(1.5 * attempt)
        if last_exception is not None:
            raise RuntimeError(f"Falha ao consultar PNCP em {url}") from last_exception

        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Resposta do PNCP nao e um JSON valido em {url}") from exc

    def _extract_items(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [i for i in payload if isinstance(i, dict)]
        if not isinstance(payload, dict):
            return []

        for key in ("data", "items", "content", "resultado", "resultados"):
            value = payload.get(key)
            if isinstance(value, list):
                return [i for i in value if isinstance(i, dict)]
            if isinstance(value, dict):
                for nested_key in ("items", "content", "resultado", "resultados"):
                    nested = value.get(nested_key)
                    if isinstance(nested, list):
                        return [i for i in nested if isinstance(i, dict)]
        return []

    def _normalize_item(self, item: Dict[str, Any]) -> Optional[Licitacao]:
        objeto = self._str_first(item, "objetoCompra", "objeto", "descricao", "descricaoObjeto")
        if not objeto:
            return None

        orgao = self._str_first(item, "nomeOrgaoEntidade", "orgaoEntidade.razaoSocial", "orgaoComprador", "orgao")
        quantidade = self._str_first(item, "quantidade", "qtd", "quantidadeTotal")
        valor_estimado = self._str_first(item, "valorTotalEstimado", "valorEstimado", "valor")
        data_abertura = self._normalize_date(
            self._str_first(item, "dataAberturaProposta", "dataAbertura", "dataInicioRecebimentoPropostas")
        )
        link_edital = self._str_first(item, "linkSistemaOrigem", "link", "url", "linkEdital")
        status = self._str_first(item, "situacaoCompraNome", "situacao", "status", "situacaoCompra")

        source_id = self._str_first(item, "numeroControlePNCP", "id", "idContratacao", "sequencialCompra")
        if source_id:
            unique_source_id = f"{self.name}:{source_id}"
        else:
            raw_key = f"{orgao}|{objeto}|{data_abertura}|{link_edital}|{self.name}"
            unique_source_id = f"{self.name}:hash:{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()}"

        return Licitacao(
            orgao_comprador=orgao,
            objeto=objeto,
            quantidade=quantidade,
            valor_estimado=valor_estimado,
            data_abertura=data_abertura,
            link_edital=link_edital,
            fonte=self.name,
            data_captura=self.capture_date,
            unique_source_id=unique_source_id,
            status=status,
        )

    def _is_open_status(self, status: str) -> bool:
        normalized = normalize_text(status)
        if not normalized:
            return True
        closed_tokens = ("encerrad", "homologad", "revogad", "cancelad", "suspens")
        if any(token in normalized for token in closed_tokens):
            return False
        open_tokens = ("abert", "receb", "divulgad", "andamento", "publicad", "proposta")
        return any(token in normalized for token in open_tokens)

    def _str_first(self, item: Dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = self._lookup(item, key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _lookup(self, item: Dict[str, Any], dotted_key: str) -> Any:
        current: Any = item
        for part in dotted_key.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def _normalize_date(self, value: str) -> str:
        raw = value.strip()
        if not raw:
            return ""
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw[:26], fmt).date().isoformat()
            except ValueError:
                continue
        if "T" in raw:
            return raw.split("T", 1)[0]
        return raw

    def _build_date_ranges(self, capture: date, window_days: int, chunk_days: int) -> List[tuple[date, date]]:
        start = capture - timedelta(days=window_days)
        ranges: List[tuple[date, date]] = []
        current = start
        while current <= capture:
            range_end = min(current + timedelta(days=chunk_days - 1), capture)
            ranges.append((current, range_end))
            current = range_end + timedelta(days=1)
        return ranges


class NotImplementedIngestor(SourceIngestor):
    def fetch(self) -> List[Licitacao]:
        raise NotImplementedError(
            f"A fonte '{self.name}' esta habilitada, mas ainda nao possui implementacao. "
            "Desabilite-a no arquivo de configuracao ou implemente um ingestor."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor diario de licitacoes de cal e calcario.")
    parser.add_argument("--config", required=True, help="Caminho do arquivo de configuracao JSON.")
    parser.add_argument("--skip-fetch", action="store_true", help="Gera saídas sem consultar fontes HTTP.")
    parser.add_argument(
        "--capture-date",
        default=date.today().isoformat(),
        help="Data de captura em formato YYYY-MM-DD. Padrao: data atual.",
    )
    return parser.parse_args()


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de configuracao nao encontrado: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Arquivo de configuracao invalido (JSON malformado): {path}") from exc


def validate_config(config: Dict[str, Any]) -> None:
    required_top_level = ("keywords", "window_days", "destinations", "output", "sources")
    for key in required_top_level:
        if key not in config:
            raise ValueError(f"Configuracao obrigatoria ausente: '{key}'")

    if not isinstance(config["keywords"], list) or not config["keywords"]:
        raise ValueError("Configuracao invalida: 'keywords' deve ser uma lista nao vazia.")
    if int(config["window_days"]) < 1:
        raise ValueError("Configuracao invalida: 'window_days' deve ser maior que zero.")

    output = config["output"]
    for key in ("daily_dir", "template_csv", "historical_csv", "state_file"):
        if key not in output or not str(output[key]).strip():
            raise ValueError(f"Configuracao invalida em 'output': campo '{key}' obrigatorio.")

    if not isinstance(config["sources"], dict):
        raise ValueError("Configuracao invalida: 'sources' deve ser um objeto.")


def ensure_csv_with_header(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()


def load_dedup_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            keys.add(line)
    return keys


def save_dedup_state(path: Path, keys: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(set(keys))
    path.write_text("\n".join(ordered) + ("\n" if ordered else ""), encoding="utf-8")


def build_sources(config: Dict[str, Any], capture_date: str) -> List[SourceIngestor]:
    source_config = config["sources"]
    window_days = int(config["window_days"])
    registry = {
        "pncp": PncpIngestor,
        "compras_gov_br": NotImplementedIngestor,
        "licitacoes_e": NotImplementedIngestor,
        "bec_sp": NotImplementedIngestor,
        "portais_estaduais_municipais": NotImplementedIngestor,
    }
    sources: List[SourceIngestor] = []
    for source_name, cfg in source_config.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Configuracao da fonte '{source_name}' deve ser um objeto.")
        enabled = bool(cfg.get("enabled", False))
        if not enabled:
            continue
        ingestor_class = registry.get(source_name)
        if ingestor_class is None:
            raise ValueError(f"Fonte '{source_name}' nao registrada no script.")
        effective_cfg = dict(cfg)
        effective_cfg.setdefault("window_days", window_days)
        sources.append(ingestor_class(source_name, effective_cfg, capture_date))
    return sources


def contains_keyword(objeto: str, keywords: List[str]) -> bool:
    normalized_objeto = normalize_text(objeto)
    return any(normalize_text(keyword) in normalized_objeto for keyword in keywords)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_text.lower().strip()


def is_within_window(data_abertura: str, capture_date: str, window_days: int) -> bool:
    if not data_abertura:
        return True
    try:
        abertura = datetime.strptime(data_abertura, "%Y-%m-%d").date()
        cap = datetime.strptime(capture_date, "%Y-%m-%d").date()
    except ValueError:
        return True
    start = cap - timedelta(days=window_days)
    return start <= abertura <= cap + timedelta(days=1)


def filter_records(records: List[Licitacao], keywords: List[str], capture_date: str, window_days: int) -> List[Licitacao]:
    filtered: List[Licitacao] = []
    for record in records:
        if not contains_keyword(record.objeto, keywords):
            continue
        if not is_within_window(record.data_abertura, capture_date, window_days):
            continue
        filtered.append(record)
    return filtered


def deduplicate(records: List[Licitacao], known_keys: set[str]) -> List[Licitacao]:
    seen_in_run: set[str] = set()
    deduped: List[Licitacao] = []
    for record in records:
        key = record.unique_source_id
        if key in known_keys or key in seen_in_run:
            continue
        seen_in_run.add(key)
        deduped.append(record)
    return deduped


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError as exc:
        raise RuntimeError(
            f"Sem permissao para gravar o arquivo diario '{path}'. Feche o arquivo no Excel e tente novamente."
        ) from exc


def append_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    ensure_csv_with_header(path)
    try:
        with path.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writerows(rows)
    except PermissionError as exc:
        raise RuntimeError(
            f"Sem permissao para atualizar o historico '{path}'. Feche o arquivo no Excel e tente novamente."
        ) from exc


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    config = load_config(config_path)
    validate_config(config)

    capture_date = args.capture_date
    try:
        datetime.strptime(capture_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Parametro '--capture-date' deve estar no formato YYYY-MM-DD.") from exc

    output_cfg = config["output"]
    daily_dir = Path(output_cfg["daily_dir"])
    template_csv = Path(output_cfg["template_csv"])
    historical_csv = Path(output_cfg["historical_csv"])
    state_file = Path(output_cfg["state_file"])

    ensure_csv_with_header(template_csv)
    ensure_csv_with_header(historical_csv)

    known_keys = load_dedup_state(state_file)

    all_records: List[Licitacao] = []
    sources = build_sources(config, capture_date)
    if not args.skip_fetch:
        if not sources:
            raise RuntimeError("Nenhuma fonte habilitada para ingestao.")
        for source in sources:
            fetched = source.fetch()
            all_records.extend(fetched)

    filtered = filter_records(all_records, config["keywords"], capture_date, int(config["window_days"]))
    deduped = deduplicate(filtered, known_keys)

    daily_path = daily_dir / f"consolidado_{capture_date}.csv"
    daily_rows = [r.as_csv_row() for r in deduped]
    write_csv(daily_path, daily_rows)

    if daily_rows:
        append_csv(historical_csv, daily_rows)

    updated_keys = set(known_keys)
    for record in deduped:
        updated_keys.add(record.unique_source_id)
    save_dedup_state(state_file, updated_keys)

    print(f"Arquivo diario gerado: {daily_path} ({len(daily_rows)} registro(s) novo(s)).")
    print(f"Historico atualizado: {historical_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
