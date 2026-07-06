"""Pydantic-модели для нормализованного представления данных ЕИС.

Соответствуют SPEC §7.3 («Pydantic-модели — ядро») и output-схемам
тулзов §5.1–5.5. Используются и провайдерами (для маппинга ответов
DaMIA / ГосПлан / navodki / HTML), и тулзами (для serialize в MCP-ответ).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Enums ------------------------------------------------------------------


class LawType(str, Enum):  # noqa: UP042 — keep (str, Enum) for Pydantic v2 use_enum_values
    FZ_44 = "44-fz"
    FZ_223 = "223-fz"
    PP_615 = "615-pp"


class TenderStatus(str, Enum):  # noqa: UP042
    PUBLISHED = "published"
    APPLICATIONS_OPEN = "applications_open"
    APPLICATIONS_CLOSED = "applications_closed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Platform(str, Enum):  # noqa: UP042
    """8 федеральных ЭТП по 44-ФЗ + общий fallback `other`."""

    SBERBANK_AST = "sberbank_ast"
    RTS_TENDER = "rts_tender"
    ROSELTORG = "roseltorg"
    TEK_TORG = "tek_torg"
    GPB = "gpb"
    RAD = "rad"
    FABRIKANT = "fabrikant"
    AGZ_RT = "agz_rt"
    OTHER = "other"


class ProcedureType(str, Enum):  # noqa: UP042
    ELECTRONIC_AUCTION = "electronic_auction"
    OPEN_COMPETITION = "open_competition"
    REQUEST_QUOTATIONS = "request_quotations"
    REQUEST_PROPOSALS = "request_proposals"
    SINGLE_SUPPLIER = "single_supplier"
    OTHER = "other"


class FileType(str, Enum):  # noqa: UP042
    PDF = "pdf"
    DOC = "doc"
    DOCX = "docx"
    XLS = "xls"
    XLSX = "xlsx"
    RTF = "rtf"
    ZIP = "zip"
    OTHER = "other"


# --- Core models ------------------------------------------------------------

_BaseConfig = ConfigDict(
    str_strip_whitespace=True,
    use_enum_values=True,
    populate_by_name=True,
)


class Customer(BaseModel):
    """Заказчик (из реквизитов ЕИС)."""

    model_config = _BaseConfig

    inn: str
    ogrn: str | None = None
    short_name: str
    full_name: str | None = None
    region_code: str | None = None
    region_name: str | None = None
    address: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class Document(BaseModel):
    """Файл документации тендера (ТЗ, проект договора и т.п.)."""

    model_config = _BaseConfig

    title: str
    url: str
    file_type: FileType = FileType.OTHER
    size_bytes: int | None = None


class ProtocolDecision(BaseModel):
    """Запись из протокола (одна позиция: участник, цена, статус)."""

    model_config = _BaseConfig

    supplier_inn: str | None = None
    supplier_name: str | None = None
    price_rub: Decimal | None = None
    decision: str | None = None  # "admitted", "rejected", "winner", ...
    rationale: str | None = None


class Protocol(BaseModel):
    """Протокол этапа тендера (рассмотрение заявок, итоги аукциона, и т.п.)."""

    model_config = _BaseConfig

    type: Literal["application_review", "auction_results", "contract_signing", "other"] = "other"
    date: datetime | None = None
    url: str | None = None
    decisions: list[ProtocolDecision] = Field(default_factory=list)


class Winner(BaseModel):
    """Победитель завершённого тендера."""

    model_config = _BaseConfig

    inn: str | None = None
    ogrn: str | None = None
    short_name: str | None = None
    contract_number: str | None = None
    contract_url: str | None = None


class Tender(BaseModel):
    """Карточка тендера. Возвращается `get_tender` и в массиве `search_tenders.tenders`."""

    model_config = _BaseConfig

    reg_number: str
    law_type: LawType
    title: str
    title_full: str | None = None
    okpd2_codes: list[str] = Field(default_factory=list)
    okpd2_names: list[str] = Field(default_factory=list)
    ktru_codes: list[str] = Field(default_factory=list)
    customer: Customer
    price_initial_rub: Decimal | None = None
    currency: str = "RUB"
    publish_date: datetime | None = None
    applications_deadline: datetime | None = None
    auction_date: datetime | None = None
    platform: Platform | None = None
    procedure_type: ProcedureType | None = None
    status: TenderStatus = TenderStatus.UNKNOWN
    smp_only: bool = False
    advance_payment_pct: float | None = None
    execution_period_days: int | None = None
    delivery_address: str | None = None
    documents: list[Document] = Field(default_factory=list)
    protocols: list[Protocol] = Field(default_factory=list)
    winner: Winner | None = None
    final_price_rub: Decimal | None = None
    url_eis: str
    url_xml: str | None = None
    fetched_at: datetime
    source_provider: str


class SearchMeta(BaseModel):
    """Метаданные search-запроса (для отладки)."""

    model_config = _BaseConfig

    took_ms: int = 0
    source: str = "unknown"
    cache_hit: bool = False


class SearchResult(BaseModel):
    """Ответ `search_tenders` (SPEC §5.1)."""

    model_config = _BaseConfig

    query_echo: dict[str, Any] = Field(default_factory=dict)
    total_estimated: int | None = None
    page_size: int = 0
    next_page_token: str | None = None
    tenders: list[Tender] = Field(default_factory=list)
    search_meta: SearchMeta = Field(default_factory=SearchMeta)


class TopOkpd2Item(BaseModel):
    code: str
    name: str | None = None
    count: int = 0
    volume_rub: Decimal | None = None


class TopWinnerItem(BaseModel):
    inn: str
    name: str | None = None
    wins: int = 0
    volume_rub: Decimal | None = None


class MonthlyStat(BaseModel):
    month: str  # YYYY-MM
    tenders: int = 0
    volume_rub: Decimal | None = None


class OrgHistorySummary(BaseModel):
    """Свод по заказчику или поставщику (SPEC §5.3 / §5.4)."""

    model_config = _BaseConfig

    org_inn: str
    org_role: Literal["customer", "supplier"]
    short_name: str | None = None
    region_code: str | None = None
    period_from: str  # YYYY-MM-DD
    period_to: str  # YYYY-MM-DD
    tenders_total: int = 0
    tenders_completed: int = 0
    tenders_cancelled: int = 0
    total_volume_rub: Decimal | None = None
    avg_contract_rub: Decimal | None = None
    median_contract_rub: Decimal | None = None
    smp_share_pct: float | None = None
    top_okpd2: list[TopOkpd2Item] = Field(default_factory=list)
    top_winners: list[TopWinnerItem] = Field(default_factory=list)
    top_customers: list[TopWinnerItem] = Field(default_factory=list)
    monthly_distribution: list[MonthlyStat] = Field(default_factory=list)
    rnp_status: Literal["clean", "in_rnp", "unknown"] = "unknown"
    fetched_at: datetime
    source_provider: str


# --- OKPD2 / KTRU lookup ----------------------------------------------------


class OkpdEntry(BaseModel):
    """Один матч в `lookup_okpd2`."""

    model_config = _BaseConfig

    code: str
    name: str
    type: Literal["okpd2", "ktru"] = "okpd2"
    parent_code: str | None = None
    level: int = 0
    match_score: float = Field(default=0.0, ge=0.0, le=1.0)


class LookupResult(BaseModel):
    """Ответ `lookup_okpd2` (SPEC §5.5)."""

    model_config = _BaseConfig

    query: str
    results: list[OkpdEntry] = Field(default_factory=list)


# --- Tool input filters (для search_tenders) -------------------------------


PriceRub = Annotated[float, Field(ge=0)]


class TenderSearchFilter(BaseModel):
    """Внутренняя нормализованная форма фильтров `search_tenders`.

    После валидации входных параметров MCP-тулзов аргументы кладутся
    в этот объект. Используется как ключ для cache `args_hash` и как
    структура запроса к провайдерам.
    """

    model_config = _BaseConfig

    law_type: list[LawType] = Field(
        default_factory=lambda: [LawType.FZ_44, LawType.FZ_223]
    )
    query: str | None = None
    okpd2_codes: list[str] = Field(default_factory=list)
    ktru_codes: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    customer_inn: str | None = None
    customer_ogrn: str | None = None
    platform: list[Platform] = Field(default_factory=list)
    price_min_rub: PriceRub | None = None
    price_max_rub: PriceRub | None = None
    status: list[TenderStatus] = Field(default_factory=list)
    smp_only: bool = False
    publish_date_from: str | None = None
    publish_date_to: str | None = None
    applications_deadline_from: str | None = None
    applications_deadline_to: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    next_page_token: str | None = None
