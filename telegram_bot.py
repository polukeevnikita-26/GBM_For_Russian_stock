#!/usr/bin/env python3
"""
Telegram-бот: принимает запрос «тикер, срок, цена аналитиков на 12 мес, барьер»,
запускает моделирование из probability_cli.run() и отвечает текстом и графиком.

Примеры сообщений:
    GAZP, 3 мес, 120, 120
    SBER, 90 дн, 350, 105%
    LKOH, 6, 8500, 7500        (срок без единицы = месяцы)

Запуск:
    export TELEGRAM_BOT_TOKEN=...   # токен от @BotFather
    python3 telegram_bot.py
"""
import asyncio
import logging
import os
import re
import tempfile

from telegram import Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from probability_cli import run

logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s: %(message)s',
                    level=logging.INFO)
log = logging.getLogger('gbm_bot')

N_SIMULATIONS = int(os.environ.get('BOT_SIMULATIONS', 50_000))
MAX_MONTHS = 24
# Расчёт тяжёлый (CPU) — выполняем по одному, остальные запросы ждут в очереди.
_calc_lock = asyncio.Lock()

HELP = (
    'Пришлите запрос в формате:\n'
    '<b>тикер, срок, цена аналитиков на 12 мес, барьер</b>\n\n'
    'Примеры:\n'
    '<code>GAZP, 3 мес, 120, 120</code>\n'
    '<code>SBER, 90 дн, 350, 105%</code>\n\n'
    '• срок: «3 мес» или «90 дн» (без единицы — месяцы)\n'
    '• барьер: цена (120) или % от текущей цены (105%)\n'
    f'• максимальный срок — {MAX_MONTHS} мес.'
)

_TERM_RE = re.compile(r'^(\d+(?:[.,]\d+)?)\s*([a-zа-я]*)\.?$', re.I)


def _num(s):
    return float(s.strip().replace(' ', '').replace(',', '.'))


def parse_query(text):
    """-> dict(ticker, months, days, target, barrier). ValueError при плохом формате."""
    parts = [p.strip() for p in re.split(r'[;\n]|(?<!\d),|,(?!\d)', text) if p.strip()]
    # запятая — разделитель полей; запятая внутри числа (3,5) не режет
    if len(parts) != 4:
        raise ValueError('Нужно 4 значения через запятую: тикер, срок, цена на 12 мес, барьер.')
    ticker, term, target, barrier = parts
    if not re.fullmatch(r'[A-Za-z0-9]{2,12}', ticker):
        raise ValueError(f'Некорректный тикер: {ticker}')
    m = _TERM_RE.match(term)
    if not m:
        raise ValueError(f'Не понял срок: «{term}». Пример: 3 мес или 90 дн.')
    value, unit = _num(m.group(1)), m.group(2).lower()
    if unit.startswith(('д', 'd')):
        months, days = None, value
    elif unit == '' or unit.startswith(('м', 'm')):
        months, days = value, None
    else:
        raise ValueError(f'Неизвестная единица срока: «{unit}» (мес/дн).')
    try:
        target_v = _num(target)
    except ValueError:
        raise ValueError(f'Цена аналитиков должна быть числом: {target}')
    barrier = barrier.replace(' ', '').replace(',', '.')
    if not re.fullmatch(r'\d+(\.\d+)?%?', barrier):
        raise ValueError(f'Барьер — число или процент (105%): {barrier}')
    eff_months = months if months is not None else days / 30.4368
    if eff_months <= 0 or eff_months > MAX_MONTHS:
        raise ValueError(f'Срок должен быть от 0 до {MAX_MONTHS} мес.')
    return dict(ticker=ticker.upper(), months=months, days=days,
                target=target_v, barrier=barrier)


def format_result(r):
    above = r['probability_price_ge_barrier'] * 100
    sign_word = 'выше' if r['barrier_price'] >= r['start_price'] else 'ниже'
    return (
        f"<b>{r['ticker']}</b> — срок {r['months']:.1f} мес "
        f"({r['horizon_periods']} торг. дн.)\n"
        f"Текущая цена: {r['start_price']:.2f}\n"
        f"Цель аналитиков (12 мес): {r['target_price_12m']:.2f} "
        f"({r['implied_12m_return_pct']:+.1f}%)\n"
        f"Барьер: {r['barrier_price']:.2f} "
        f"({r['barrier_percent_of_start']:.1f}% от текущей, {sign_word} рынка)\n\n"
        f"<b>P(цена в конце срока ≥ барьера) = {above:.1f}%</b>\n"
        f"P(цена &lt; барьера) = {100 - above:.1f}%\n"
        f"Медиана: {r['median_terminal_price']:.2f}\n"
        f"90% интервал: {r['ci_90_low']:.2f} … {r['ci_90_high']:.2f}\n"
        f"Дневная волатильность GARCH: {r['daily_vol_forecast_first_pct']:.2f}% → "
        f"{r['daily_vol_forecast_last_pct']:.2f}%\n"
        f"Симуляций: {r['n_simulations']:,}".replace(',', ' ')
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(HELP)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    try:
        q = parse_query(msg.text or '')
    except ValueError as exc:
        await msg.reply_html(f'⚠️ {exc}\n\n{HELP}')
        return

    status = await msg.reply_text(f'Считаю {q["ticker"]}… (очередь: {"занято" if _calc_lock.locked() else "свободно"})')
    chart = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    chart.close()
    try:
        async with _calc_lock:
            result = await asyncio.to_thread(
                run, ticker=q['ticker'], target_price_12m=q['target'],
                barrier_raw=q['barrier'], months=q['months'], days=q['days'],
                n_simulations=N_SIMULATIONS, out_chart=chart.name)
        with open(chart.name, 'rb') as f:
            await msg.reply_photo(f, caption=format_result(result), parse_mode='HTML')
    except Exception as exc:
        log.exception('calc failed for %s', q)
        await msg.reply_text(f'❌ Не удалось посчитать {q["ticker"]}: {exc}')
    finally:
        os.unlink(chart.name)
        await status.delete()


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise SystemExit('Задайте переменную окружения TELEGRAM_BOT_TOKEN')
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(['start', 'help'], start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()


if __name__ == '__main__':
    main()
