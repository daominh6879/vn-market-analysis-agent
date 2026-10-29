"""
pipeline/assets_rule_data.py — datasets for vn-portfolio's rule engine (khung v2.2) that it had switched rules off for.

Assets (ingest/fetch_rule_data.py, bctc notes: ingest/extract_bctc_notes.py):
  block_trades_ingest       put-through vs matched per session        → block_trades          (weekdays 18:40)
  size_indices_ingest       VN100/VNMID/VNSML (+ old VNINDEX days)     → market_index_daily    (weekdays 18:40)
  index_valuation_ingest    VNINDEX P/E, P/B                           → index_valuation       (weekdays 18:40)
  foreign_room_ingest       foreign room per ticker                    → foreign_room          (weekdays 18:50)
  corporate_actions_ingest  issues with price, AGMs, trading alerts    → corporate_actions     (weekdays 18:50)
  commodities_ingest        Brent, HRC, iron ore, gas, urea, sugar     → commodity_daily       (weekdays 07:30)
  shareholders_ingest       major holders / insiders                   → shareholders          (Saturday 09:00)
  vnd_items_ingest          AGM plan, broker loans, insurer statements → vnd_financial_items   (Saturday 09:30)
  fireant_data_ingest       bank loan groups / NPL, broker loans       → fireant_financial_data (Saturday 08:00)
  fireant_holders_ingest    major holders (FireAnt)                    → shareholders          (Saturday 08:00)
  board_close_ingest        best bid/offer at close, covered warrants  → board_close_snapshot, covered_warrants (15:05)
  yield_curve_ingest        HNX government bond yield curve            → gov_bond_yields       (weekdays 17:30)
  price_history_ingest      prices from 2017, foreign flows from 2018  → ohlcv_daily, foreign_flows (Sunday 11:00)
  bctc_notes_extract        related party, bonds, CASA (PDF notes; only figures with no structured source)
                                                                       → bctc_note_facts       (Sunday 10:00)
  macro_ingest              CPI, credit / deposit growth (NSO), VCB 12-month deposit rate → macro_indicators (daily 09:00)
  insurer_lines_ingest      insurers' full statements (investment lines) → fireant_statement_lines (Saturday 08:30)
  intraday_ingest           14:25 state + ATC fill per session (DNSE 1m) → intraday_snapshot   (weekdays 15:10)
  broker_reports_ingest     broker research list, target prices (Simplize) → broker_reports    (weekdays 19:00)
  broker_forecasts_extract  EPS / NPAT forecasts read from the report PDFs → broker_forecasts  (weekdays 19:00)

data_bootstrap_sensor (pipeline/assets_market_data.py) starts each job with backfill when its table is empty.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import (
    AssetExecutionContext, Config, DefaultScheduleStatus, RetryPolicy, ScheduleDefinition, asset, define_asset_job,
)

_TZ = "Asia/Ho_Chi_Minh"
_RETRY = RetryPolicy(max_retries=2, delay=300)


class BackfillConfig(Config):
    backfill: bool = False      # True refetches the full history (the bootstrap sensor sets it on an empty table)


def _log(context: AssetExecutionContext, name: str, result: dict) -> dict:
    context.log.info(f"{name}: {result}")
    return {k: (v if isinstance(v, (int, float, str)) else str(v)[:200]) for k, v in result.items()}


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="Put-through (thỏa thuận) vs matched volume/value per ticker per session (VNDirect) → block_trades.")
def block_trades_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_block_trades
    return _log(context, "block_trades", run_block_trades(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="VN100 / VNMID / VNSML daily (VNDirect, from 2017) and VNINDEX/VN30/HNX30 sessions before the SSI feed → market_index_daily.")
def size_indices_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_size_indices
    return _log(context, "size_indices", run_size_indices(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="VNINDEX P/E and P/B per session (VNDirect, from 2017-12) → index_valuation.")
def index_valuation_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_index_valuation
    return _log(context, "index_valuation", run_index_valuation(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="Foreign total room and room left per ticker per session (VNDirect) → foreign_room.")
def foreign_room_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_foreign_room
    return _log(context, "foreign_room", run_foreign_room(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="VNDirect corporate actions: share issues with price/shares, AGMs, trading and margin alerts → corporate_actions.")
def corporate_actions_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_corporate_actions
    return _log(context, "corporate_actions", run_corporate_actions(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="Front-month futures closes (yfinance): Brent, HRC steel, iron ore, natural gas, urea, sugar → commodity_daily.")
def commodities_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_commodities
    return _log(context, "commodities", run_commodities(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="Major shareholders and insiders per ticker (VNDirect) → shareholders.")
def shareholders_ingest(context: AssetExecutionContext) -> dict:
    from ingest.fetch_rule_data import run_shareholders
    return _log(context, "shareholders", run_shareholders())


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="VNDirect line items: AGM business plan (model 33), broker loans (89/90), insurer statements (411/412) → vnd_financial_items.")
def vnd_items_ingest(context: AssetExecutionContext) -> dict:
    from ingest.fetch_rule_data import run_vnd_items
    return _log(context, "vnd_items", run_vnd_items())


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="FireAnt quarterly values: bank loan groups 1–5, NPL, loan-loss reserves; broker loans and sector-wide broker loans → fireant_financial_data.")
def fireant_data_ingest(context: AssetExecutionContext) -> dict:
    from ingest.fetch_rule_data import run_fireant_data
    return _log(context, "fireant_data", run_fireant_data())


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="FireAnt major holders (fresher report dates than VNDirect) → shareholders (source fireant).")
def fireant_holders_ingest(context: AssetExecutionContext) -> dict:
    from ingest.fetch_rule_data import run_fireant_holders
    return _log(context, "fireant_holders", run_fireant_holders())


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="ohlcv_daily / foreign_flows sessions before each ticker's first stored day, from VNDirect (2017 / 2018-08 on); never touches existing rows.")
def price_history_ingest(context: AssetExecutionContext) -> dict:
    from ingest.fetch_rule_data import run_price_history
    return _log(context, "price_history", run_price_history())


@asset(group_name="rule_data", retry_policy=RetryPolicy(max_retries=3, delay=120),
       description="After the close: HOSE board best bid/offer (7.6-A dư mua trần) → board_close_snapshot; covered warrants with last trading day (7.6-D) → covered_warrants.")
def board_close_ingest(context: AssetExecutionContext) -> dict:
    from ingest.fetch_rule_data import run_board_close
    return _log(context, "board_close", run_board_close())


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="HNX government bond yield curve (par / spot by tenor) → gov_bond_yields (6.5-C dynamic COE).")
def yield_curve_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_yield_curve
    return _log(context, "yield_curve", run_yield_curve(backfill=config.backfill))


class NotesConfig(Config):
    force: bool = False         # True re-reads reports that already have facts


@asset(group_name="rule_data", retry_policy=RetryPolicy(max_retries=1, delay=600),
       description="LLM reads the notes of each indexed annual BCTC: related-party receivables/loans, bonds due in 12 months, bank loan groups and deposits → bctc_note_facts.")
def bctc_notes_extract(context: AssetExecutionContext, config: NotesConfig) -> dict:
    from ingest.extract_bctc_notes import run
    return _log(context, "bctc_notes", run(force=config.force))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="Cục Thống kê CPI / credit releases (sentence quoted, post time = published) and Vietcombank's 12-month deposit rate → macro_indicators (3.3 thắt chặt / nới lỏng, 5.8).")
def macro_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_macro
    return _log(context, "macro", run_macro(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="FireAnt full balance sheet / income statement of the insurers → fireant_statement_lines (5.8 lợi suất đầu tư, tài sản rủi ro).")
def insurer_lines_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_insurer_lines
    return _log(context, "insurer_lines", run_insurer_lines(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=RetryPolicy(max_retries=3, delay=300),
       description="After the close: 1-minute bars → state at 14:25 and the ATC fill per session → intraday_snapshot (2.3-D same_day_atc).")
def intraday_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_intraday
    return _log(context, "intraday", run_intraday(backfill=config.backfill))


@asset(group_name="rule_data", retry_policy=_RETRY,
       description="Simplize broker research list (broker, date, target price, PDF) → broker_reports.")
def broker_reports_ingest(context: AssetExecutionContext, config: BackfillConfig) -> dict:
    from ingest.fetch_rule_data import run_broker_reports
    return _log(context, "broker_reports", run_broker_reports(backfill=config.backfill))


@asset(group_name="rule_data", deps=[broker_reports_ingest], retry_policy=RetryPolicy(max_retries=1, delay=600),
       description="LLM reads the forecast table of each new broker report; figures kept only when the PDF row puts them under that year → broker_forecasts (6.5-B consensus).")
def broker_forecasts_extract(context: AssetExecutionContext) -> dict:
    from ingest.extract_broker_forecasts import run
    return _log(context, "broker_forecasts", run())


block_trades_job = define_asset_job("block_trades_job", selection=[block_trades_ingest])
index_data_job = define_asset_job("index_data_job", selection=[size_indices_ingest, index_valuation_ingest])
foreign_room_job = define_asset_job("foreign_room_job", selection=[foreign_room_ingest])
corporate_actions_job = define_asset_job("corporate_actions_job", selection=[corporate_actions_ingest])
commodities_job = define_asset_job("commodities_job", selection=[commodities_ingest])
shareholders_job = define_asset_job("shareholders_job", selection=[shareholders_ingest])
vnd_items_job = define_asset_job("vnd_items_job", selection=[vnd_items_ingest])
bctc_notes_job = define_asset_job("bctc_notes_job", selection=[bctc_notes_extract])
fireant_data_job = define_asset_job("fireant_data_job", selection=[fireant_data_ingest, fireant_holders_ingest])
price_history_job = define_asset_job("price_history_job", selection=[price_history_ingest])
board_close_job = define_asset_job("board_close_job", selection=[board_close_ingest])
yield_curve_job = define_asset_job("yield_curve_job", selection=[yield_curve_ingest])
macro_job = define_asset_job("macro_job", selection=[macro_ingest])
insurer_lines_job = define_asset_job("insurer_lines_job", selection=[insurer_lines_ingest])
intraday_job = define_asset_job("intraday_job", selection=[intraday_ingest])
broker_job = define_asset_job("broker_job", selection=[broker_reports_ingest, broker_forecasts_extract])


def _schedule(job, cron: str, name: str) -> ScheduleDefinition:
    return ScheduleDefinition(job=job, cron_schedule=cron, name=name, default_status=DefaultScheduleStatus.RUNNING,
                              execution_timezone=_TZ)


rule_data_schedules = [
    _schedule(block_trades_job, "40 18 * * 1-5", "block_trades_1840"),
    _schedule(index_data_job, "40 18 * * 1-5", "index_data_1840"),
    _schedule(foreign_room_job, "50 18 * * 1-5", "foreign_room_1850"),
    _schedule(corporate_actions_job, "50 18 * * 1-5", "corporate_actions_1850"),
    _schedule(commodities_job, "30 7 * * 1-5", "commodities_0730"),
    _schedule(shareholders_job, "0 9 * * 6", "shareholders_sat_0900"),
    _schedule(vnd_items_job, "30 9 * * 6", "vnd_items_sat_0930"),
    _schedule(bctc_notes_job, "0 10 * * 0", "bctc_notes_sun_1000"),
    _schedule(fireant_data_job, "0 8 * * 6", "fireant_data_sat_0800"),
    _schedule(price_history_job, "0 11 * * 0", "price_history_sun_1100"),      # new listings / moved tickers
    _schedule(board_close_job, "5 15 * * 1-5", "board_close_1505"),             # board frozen after ATC / put-through
    _schedule(yield_curve_job, "30 17 * * 1-5", "yield_curve_1730"),
    _schedule(macro_job, "0 9 * * *", "macro_0900"),                           # NSO posts ~08:00 on release days
    _schedule(insurer_lines_job, "30 8 * * 6", "insurer_lines_sat_0830"),
    _schedule(intraday_job, "10 15 * * 1-5", "intraday_1510"),                 # after the ATC bar (14:45)
    _schedule(broker_job, "0 19 * * 1-5", "broker_1900"),
]
rule_data_jobs = [block_trades_job, index_data_job, foreign_room_job, corporate_actions_job, commodities_job,
                  shareholders_job, vnd_items_job, bctc_notes_job, fireant_data_job, price_history_job,
                  board_close_job, yield_curve_job, macro_job, insurer_lines_job, intraday_job, broker_job]
rule_data_assets = [block_trades_ingest, size_indices_ingest, index_valuation_ingest, foreign_room_ingest,
                    corporate_actions_ingest, commodities_ingest, shareholders_ingest, vnd_items_ingest,
                    bctc_notes_extract, fireant_data_ingest, fireant_holders_ingest, price_history_ingest,
                    board_close_ingest, yield_curve_ingest, macro_ingest, insurer_lines_ingest, intraday_ingest,
                    broker_reports_ingest, broker_forecasts_extract]
