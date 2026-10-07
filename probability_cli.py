#!/usr/bin/env python3
"""
CLI-обёртка над логикой ноутбука "Вероятность цены по GARCH.ipynb".

Оценивает вероятность достижения ценового барьера акцией MOEX к заданному сроку,
используя GARCH(1,1) с t-распределением ошибок и path-dependent Монте-Карло (GBM).

Пример (один тикер):
    python3 probability_cli.py SBER --target 320 --months 6 --barrier 300 \
        --out-json result.json --out-chart chart.png
    python3 probability_cli.py SBER --target 320 --days 90 --barrier 300 \
        --out-json result.json --out-chart chart.png

Срок задаётся либо в месяцах (--months), либо в днях (--days) — ровно одним из
двух флагов. Барьер можно задать либо абсолютной ценой (--barrier 300), либо
в процентах от текущей цены (--barrier 105%).

Пример (пакетный режим, таблица тикеров из Excel):
    python3 probability_cli.py --excel tickers.xlsx --out-dir results/

Файл должен содержать колонки (название не чувствительно к регистру/пробелам,
порядок любой): тикер, целевая цена на 12 мес, срок, барьер,%. Колонка "срок"
без явной единицы измерения трактуется как ДНИ; чтобы задать месяцами, назовите
колонку «срок, мес» (или «months»). Колонка "барьер,%" — всегда в процентах от
текущей цены (знак % в самих ячейках необязателен).
"""
import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from arch.univariate import ConstantMean, StudentsT
from arch.univariate.volatility import GARCH

TIMEFRAME_ALIASES = {
    'day': 'day', 'день': 'day',
    'week': 'week', 'неделя': 'week',
    'month': 'month', 'месяц': 'month',
    '4h': '4h', '4ч': '4h', '4 часа': '4h',
}
DEFAULT_PERIODS_PER_MONTH = {'day': 21, 'week': 52 / 12, 'month': 1, '4h': 63}
ISS_INTERVAL = {'day': 24, 'week': 7, 'month': 31, '4h': 60}
T_SHOCK_CLIP_QUANTILE = 0.995

_MOEX = requests.Session()
_MOEX.headers.update({
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://iss.moex.com/',
})


class CappedGARCH(GARCH):
    """GARCH с ограничением alpha + beta <= max_persistence."""

    def __init__(self, *args, max_persistence, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_persistence = max_persistence

    def constraints(self):
        a, b = super().constraints()
        b[-1] = -self.max_persistence
        return a, b


def _moex_get(url, params, retries=4):
    last = None
    for attempt in range(retries):
        try:
            r = _MOEX.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise ConnectionError(
        f'ISS MOEX недоступен ({last}). Проверьте сеть/VPN с российским IP.'
    )


def load_moex_candles(ticker, interval, date_from, date_till):
    url = (f'https://iss.moex.com/iss/engines/stock/markets/shares/'
           f'securities/{ticker.upper()}/candles.json')
    chunks, start = [], 0
    while True:
        response = _moex_get(url, {
            'from': date_from.isoformat(), 'till': date_till.isoformat(),
            'interval': interval, 'start': start, 'iss.meta': 'off'
        })
        payload = response.json()['candles']
        batch = pd.DataFrame(payload['data'], columns=payload['columns'])
        if batch.empty:
            break
        chunks.append(batch)
        if len(batch) < 500:
            break
        start += len(batch)
    if not chunks:
        raise ValueError(f'ISS не вернул свечи для {ticker}. Проверьте тикер и период.')
    return pd.concat(chunks, ignore_index=True)


def parse_barrier(raw_barrier, start_price):
    """Барьер может быть абсолютной ценой ('300') или процентом ('105%')."""
    s = str(raw_barrier).strip()
    if s.endswith('%'):
        barrier_percent = float(s[:-1])
        barrier_price = start_price * barrier_percent / 100
    else:
        barrier_price = float(s)
        barrier_percent = barrier_price / start_price * 100
    return barrier_price, barrier_percent


AVG_DAYS_PER_MONTH = 30.4368

TICKER_COL_ALIASES = ['тикер', 'ticker', 'symbol', 'символ']
TARGET_COL_ALIASES = ['целевая цена на 12 мес', 'целевая цена', 'target', 'цель',
                       'target_price_12m', 'target_price']
TERM_MONTHS_COL_ALIASES = ['срок, мес', 'срок,мес', 'срок мес', 'срок(мес)',
                            'months', 'мес', 'месяцев']
TERM_DAYS_COL_ALIASES = ['срок, дней', 'срок,дней', 'срок дней', 'срок(дней)',
                          'days', 'дней']
TERM_GENERIC_COL_ALIASES = ['срок', 'term']
BARRIER_COL_ALIASES = ['барьер,%', 'барьер, %', 'барьер %', 'barrier,%', 'barrier %',
                        'барьер', 'barrier']


def _find_column(columns, aliases):
    """Ищет колонку по списку алиасов: сперва точное совпадение (без учёта регистра/
    пробелов по краям), затем — по подстроке. Возвращает исходное имя колонки или None."""
    normalized = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    for alias in aliases:
        for key, original in normalized.items():
            if alias in key:
                return original
    return None


def read_tickers_table(path):
    """Читает Excel-таблицу тикер | целевая цена на 12 мес | срок | барьер,% и
    возвращает список словарей, готовых к распаковке в run(...)."""
    df = pd.read_excel(path)

    ticker_col = _find_column(df.columns, TICKER_COL_ALIASES)
    target_col = _find_column(df.columns, TARGET_COL_ALIASES)
    barrier_col = _find_column(df.columns, BARRIER_COL_ALIASES)
    # Срок ищем только среди колонок, ещё не занятых тикером/целью/барьером — иначе
    # общий алиас вроде "мес" может случайно совпасть с "целевая цена на 12 мес".
    remaining_cols = [c for c in df.columns if c not in (ticker_col, target_col, barrier_col)]
    months_col = _find_column(remaining_cols, TERM_MONTHS_COL_ALIASES)
    days_col = None if months_col else _find_column(remaining_cols, TERM_DAYS_COL_ALIASES)
    generic_term_col = (None if (months_col or days_col)
                         else _find_column(remaining_cols, TERM_GENERIC_COL_ALIASES))

    missing = [name for name, col in [
        ('тикер', ticker_col), ('целевая цена на 12 мес', target_col),
        ('барьер,%', barrier_col),
    ] if col is None]
    if not (months_col or days_col or generic_term_col):
        missing.append('срок')
    if missing:
        raise ValueError(
            f'В файле не найдены обязательные колонки: {", ".join(missing)}. '
            f'Найденные колонки: {list(df.columns)}'
        )

    barrier_header_has_percent = '%' in str(barrier_col)

    rows = []
    for _, record in df.iterrows():
        ticker_value = record[ticker_col]
        if pd.isna(ticker_value) or not str(ticker_value).strip():
            continue

        months = days = None
        if months_col:
            months = float(record[months_col])
        elif days_col:
            days = float(record[days_col])
        else:
            days = float(record[generic_term_col])

        barrier_value = str(record[barrier_col]).strip()
        if barrier_header_has_percent and not barrier_value.endswith('%'):
            barrier_value = f'{barrier_value}%'

        rows.append({
            'ticker': str(ticker_value).strip(),
            'target_price_12m': float(record[target_col]),
            'months': months,
            'days': days,
            'barrier_raw': barrier_value,
        })
    return rows


def run(ticker, target_price_12m, barrier_raw, months=None, days=None,
        timeframe='day', n_simulations=100_000, garch_observations=1000,
        garch_max_persistence=0.995, forecast_periods_per_month=None,
        random_seed=42, out_json=None, out_chart=None):

    if (months is None) == (days is None):
        raise ValueError('Укажите срок ровно одним способом: months ИЛИ days.')
    if (months is not None and months <= 0) or (days is not None and days <= 0):
        raise ValueError('Срок должен быть положительным.')
    months = months if months is not None else days / AVG_DAYS_PER_MONTH
    if target_price_12m <= 0:
        raise ValueError('Целевая цена на 12 мес. должна быть больше нуля.')
    if garch_observations < 100:
        raise ValueError('Для GARCH укажите не менее 100 свечей (garch_observations).')
    if not 0 < garch_max_persistence < 1:
        raise ValueError('garch_max_persistence должно быть между 0 и 1.')

    timeframe = TIMEFRAME_ALIASES.get(str(timeframe).strip().lower())
    if timeframe is None:
        raise ValueError("timeframe: 'day', 'week', 'month' или '4h'.")

    periods_per_month = (DEFAULT_PERIODS_PER_MONTH[timeframe]
                          if forecast_periods_per_month is None
                          else forecast_periods_per_month)
    horizon = max(1, int(round(months * periods_per_month)))

    iss_interval = ISS_INTERVAL[timeframe]
    lookback_days = {
        'day': max(garch_observations * 3, 365),
        'week': max(garch_observations * 10, 730),
        'month': max(garch_observations * 45, 3650),
        '4h': max(garch_observations * 2, 180),
    }[timeframe]

    raw_candles = load_moex_candles(
        ticker, iss_interval, date.today() - timedelta(days=lookback_days), date.today()
    )
    raw_candles['begin'] = pd.to_datetime(raw_candles['begin'])
    raw_candles = raw_candles.sort_values('begin').drop_duplicates('begin').set_index('begin')

    if timeframe == '4h':
        candles = raw_candles.resample('4h', origin='start_day', offset='2h').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
            'value': 'sum', 'volume': 'sum'
        }).dropna(subset=['close'])
    else:
        candles = raw_candles

    candles = candles.tail(garch_observations)
    if len(candles) < garch_observations:
        raise ValueError(
            f'Для {ticker} доступно только {len(candles)} свечей выбранного периода; '
            'уменьшите garch_observations или смените период.'
        )
    prices = candles['close'].astype(float).reset_index(drop=True)
    returns_pct = (100 * np.log(prices / prices.shift(1))).dropna()

    garch = ConstantMean(
        returns_pct,
        volatility=CappedGARCH(p=1, q=1, max_persistence=garch_max_persistence),
        distribution=StudentsT(),
    )
    garch_result = garch.fit(disp='off')
    forecast = garch_result.forecast(horizon=horizon, reindex=False)
    daily_vol = np.sqrt(forecast.variance.iloc[-1].to_numpy()) / 100
    omega = garch_result.params['omega'] / 100 ** 2
    alpha = garch_result.params['alpha[1]']
    beta = garch_result.params['beta[1]']
    persistence = alpha + beta
    nu = garch_result.params['nu']

    start_price = float(prices.iloc[-1])
    barrier_price, barrier_percent = parse_barrier(barrier_raw, start_price)
    analyst_forecast_12m_pct = (target_price_12m / start_price - 1) * 100
    periods_per_year = 12 * periods_per_month
    target_log_gross_per_period = np.log1p(analyst_forecast_12m_pct / 100) / periods_per_year

    if nu <= 2:
        raise ValueError('Для стандартизации t-шоков требуется nu > 2 (переоцените GARCH).')

    rng = np.random.default_rng(random_seed)
    t_clip = (
        np.quantile(rng.standard_t(df=nu, size=2_000_000),
                    [1 - T_SHOCK_CLIP_QUANTILE, T_SHOCK_CLIP_QUANTILE])
        * np.sqrt((nu - 2) / nu)
    )
    rng = np.random.default_rng(random_seed)

    correction_rng = np.random.default_rng(random_seed + 1)
    correction_shocks = correction_rng.standard_t(df=nu, size=25_000)
    correction_shocks = np.clip(
        correction_shocks * np.sqrt((nu - 2) / nu), t_clip[0], t_clip[1]
    )
    variance_grid_max = max(1.0, 1_000 * float(np.max(daily_vol ** 2)))
    variance_grid = np.r_[0.0, np.geomspace(1e-12, variance_grid_max, 500)]
    log_exp_moment_grid = np.array([
        np.log(np.mean(np.exp(np.sqrt(h) * correction_shocks)))
        for h in variance_grid
    ])

    def analyst_drift(variance):
        if np.max(variance) > variance_grid_max:
            raise RuntimeError('Слишком большая GARCH-дисперсия для drift correction.')
        convexity_correction = np.interp(variance, variance_grid, log_exp_moment_grid)
        return target_log_gross_per_period - convexity_correction

    simulated_prices = np.empty((n_simulations, horizon))
    current_prices = np.full(n_simulations, start_price)
    current_variance = np.full(n_simulations, daily_vol[0] ** 2)
    for day in range(horizon):
        shock = rng.standard_t(df=nu, size=n_simulations)
        shock = np.clip(shock * np.sqrt((nu - 2) / nu), t_clip[0], t_clip[1])
        log_return = analyst_drift(current_variance) + np.sqrt(current_variance) * shock
        current_prices *= np.exp(log_return)
        simulated_prices[:, day] = current_prices
        innovation_sq = current_variance * shock ** 2
        current_variance = omega + alpha * innovation_sq + beta * current_variance

    terminal_prices = simulated_prices[:, -1]
    probability_above = float(np.mean(terminal_prices >= barrier_price))
    probability_below = 1 - probability_above
    median_price = float(np.median(terminal_prices))
    ci_5, ci_95 = (float(x) for x in np.quantile(terminal_prices, [0.05, 0.95]))

    result = {
        'ticker': ticker.upper(),
        'timeframe': timeframe,
        'candles_used': int(len(candles)),
        'history_range': [str(candles.index[0]), str(candles.index[-1])],
        'start_price': start_price,
        'target_price_12m': target_price_12m,
        'implied_12m_return_pct': analyst_forecast_12m_pct,
        'months': months,
        'days': months * AVG_DAYS_PER_MONTH,
        'horizon_periods': horizon,
        'barrier_price': barrier_price,
        'barrier_percent_of_start': barrier_percent,
        'n_simulations': n_simulations,
        'garch': {
            'omega': float(omega), 'alpha': float(alpha), 'beta': float(beta),
            'persistence': float(persistence), 'nu': float(nu),
            'max_persistence_limit': garch_max_persistence,
        },
        'daily_vol_forecast_first_pct': float(daily_vol[0] * 100),
        'daily_vol_forecast_last_pct': float(daily_vol[-1] * 100),
        'probability_price_ge_barrier': probability_above,
        'probability_price_lt_barrier': probability_below,
        'median_terminal_price': median_price,
        'ci_90_low': ci_5,
        'ci_90_high': ci_95,
        't_shock_clip': [float(t_clip[0]), float(t_clip[1])],
    }

    if out_chart:
        fig, axes = plt.subplots(1, 2, figsize=(15, 5))
        n_paths_to_show = min(300, n_simulations)
        axes[0].plot(simulated_prices[:n_paths_to_show].T, alpha=0.08, color='steelblue')
        axes[0].axhline(barrier_price, color='crimson', ls='--',
                         label=f'Барьер: {barrier_price:.2f}')
        axes[0].set(title=f'{ticker.upper()}: симулированные траектории цены',
                    xlabel='Период', ylabel='Цена')
        axes[0].legend()
        axes[0].grid(alpha=0.25)

        display_low, display_high = np.quantile(terminal_prices, [0.005, 0.995])
        axes[1].hist(
            terminal_prices, bins=80, range=(display_low, display_high),
            density=True, color='steelblue', alpha=0.75
        )
        axes[1].axvline(barrier_price, color='crimson', ls='--',
                         label=f'Барьер: {barrier_price:.2f}')
        axes[1].axvline(median_price, color='darkgreen', ls=':',
                         label=f'Медиана: {median_price:.2f}')
        axes[1].set_xlim(display_low, display_high)
        axes[1].set(title='Распределение цены в конце срока (99% результатов)',
                    xlabel='Цена', ylabel='Плотность')
        axes[1].legend()
        axes[1].grid(alpha=0.25)
        plt.tight_layout()
        fig.savefig(out_chart, dpi=130)
        plt.close(fig)
        result['chart_path'] = out_chart

    if out_json:
        with open(out_json, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def run_batch(excel_path, out_dir='.', **run_kwargs):
    """Прогоняет run() по каждой строке Excel-таблицы, ловит ошибки по тикеру отдельно
    (как в run_market_data.py), пишет JSON/PNG по каждому тикеру и общий сводный JSON."""
    rows = read_tickers_table(excel_path)
    if not rows:
        raise ValueError('В файле не найдено ни одной строки с тикером.')

    os.makedirs(out_dir, exist_ok=True)
    results = []
    for row in rows:
        ticker = row['ticker']
        try:
            result = run(
                ticker=ticker,
                target_price_12m=row['target_price_12m'],
                barrier_raw=row['barrier_raw'],
                months=row['months'],
                days=row['days'],
                out_json=os.path.join(out_dir, f'{ticker}_result.json'),
                out_chart=os.path.join(out_dir, f'{ticker}_chart.png'),
                **run_kwargs,
            )
            results.append(result)
        except Exception as exc:
            results.append({'ticker': ticker.upper(), 'error': str(exc)})

    summary_path = os.path.join(out_dir, 'Probability_Summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return results, summary_path


def main():
    parser = argparse.ArgumentParser(
        description='Вероятность достижения ценового барьера по GARCH + Монте-Карло (MOEX).'
    )
    parser.add_argument('ticker', nargs='?', default=None,
                         help='Тикер MOEX, например SBER, GAZP, LKOH (не нужен вместе с --excel)')
    parser.add_argument('--target', type=float, default=None,
                         help='Целевая цена аналитика на 12 месяцев (абсолютное значение)')
    parser.add_argument('--months', type=float, default=None,
                         help='Срок прогноза в месяцах (горизонт симуляции)')
    parser.add_argument('--days', type=float, default=None,
                         help='Срок прогноза в днях (альтернатива --months)')
    parser.add_argument('--barrier', default=None,
                         help='Барьер: абсолютная цена (300) или процент от текущей цены (105%%)')
    parser.add_argument('--excel', default=None,
                         help='Путь к Excel-файлу со списком тикеров: тикер | целевая цена на '
                              '12 мес | срок | барьер,%% (пакетный режим вместо ticker/--target/'
                              '--months/--days/--barrier)')
    parser.add_argument('--out-dir', default='.',
                         help='Каталог для JSON/PNG по каждому тикеру и сводки (только с --excel)')
    parser.add_argument('--timeframe', default='day', help='day/week/month/4h (по умолч. day)')
    parser.add_argument('--simulations', type=int, default=100_000, dest='n_simulations')
    parser.add_argument('--observations', type=int, default=1000, dest='garch_observations')
    parser.add_argument('--max-persistence', type=float, default=0.995, dest='garch_max_persistence')
    parser.add_argument('--seed', type=int, default=42, dest='random_seed')
    parser.add_argument('--out-json', default=None,
                         help='Путь для сохранения JSON-результата (только для одного тикера)')
    parser.add_argument('--out-chart', default=None,
                         help='Путь для сохранения PNG-графика (только для одного тикера)')
    args = parser.parse_args()

    shared_kwargs = dict(
        timeframe=args.timeframe,
        n_simulations=args.n_simulations,
        garch_observations=args.garch_observations,
        garch_max_persistence=args.garch_max_persistence,
        random_seed=args.random_seed,
    )

    if args.excel:
        try:
            results, summary_path = run_batch(
                excel_path=args.excel, out_dir=args.out_dir, **shared_kwargs
            )
        except Exception as exc:
            print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stdout)
            sys.exit(1)
        print(json.dumps({'summary_path': summary_path, 'results': results},
                          ensure_ascii=False, indent=2))
        return

    if not args.ticker:
        parser.error('Укажите тикер (позиционный аргумент) или --excel <файл>.')
    if not args.target or not args.barrier or (args.months is None and args.days is None):
        parser.error('Для одного тикера обязательны: --target, --barrier, и --months ИЛИ --days.')
    if args.months is not None and args.days is not None:
        parser.error('Укажите срок ровно одним способом: --months ИЛИ --days.')

    try:
        result = run(
            ticker=args.ticker,
            target_price_12m=args.target,
            months=args.months,
            days=args.days,
            barrier_raw=args.barrier,
            out_json=args.out_json,
            out_chart=args.out_chart,
            **shared_kwargs,
        )
    except Exception as exc:
        print(json.dumps({'ticker': args.ticker.upper(), 'error': str(exc)},
                          ensure_ascii=False), file=sys.stdout)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
