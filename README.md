# GBM_For_Russian_stock

Оценка вероятности достижения ценового барьера для акций Московской биржи с помощью GARCH(1,1) и Монте-Карло симуляции методом геометрического броуновского движения (GBM).

Probability estimation of hitting a price barrier for Moscow Exchange stocks using GARCH(1,1) volatility forecasting and a Geometric Brownian Motion (GBM) Monte Carlo simulation.

---

## RU

### Описание

Ноутбук [`Вероятность цены по GARCH.ipynb`](Вероятность%20цены%20по%20GARCH.ipynb) строит прогноз дневной волатильности GARCH(1,1) и моделирует будущие траектории цены акции методом Монте-Карло, чтобы оценить вероятность того, что на дату окончания срока цена окажется не ниже (или не ниже — в зависимости от знака) заданного барьера.

### Функционал

- **Загрузка данных.** Исторические свечи (день/неделя/месяц/4ч) загружаются напрямую из ISS API Московской биржи по тикеру (`SBER`, `GAZP`, `LKOH`, `TATN` и т.д.).
- **Оценка GARCH(1,1).** На логарифмических доходностях оценивается модель `ConstantMean` + `GARCH(1,1)` с распределением ошибок Стьюдента (t-распределение), библиотека `arch`. Добавлено ограничение `alpha + beta <= GARCH_MAX_PERSISTENCE`, чтобы прогноз волатильности не расходился.
- **Path-dependent Монте-Карло.** Для каждой из `N_SIMULATIONS` траекторий условная дисперсия пересчитывается на каждом шаге по формуле GARCH: `h_t = omega + alpha * eps_{t-1}^2 + beta * h_{t-1}`, а не берётся фиксированной на весь горизонт.
- **t-шоки вместо нормальных.** Случайные шоки берутся из стандартизированного t-распределения (той же формы, что использовалась при оценке GARCH), с усечением хвостов, чтобы избежать нереалистичных выбросов цены при `exp()`.
- **Коррекция дрейфа под прогноз аналитиков.** Годовой ожидаемый рост цены (`ANALYST_FORECAST_12M_PCT`) пересчитывается в доходность одной свечи, а дрейф модели численно корректируется (через интерполяцию логарифма момента экспоненты усечённого шока) так, чтобы *средняя* смоделированная цена соответствовала прогнозу аналитиков, а не только медианная траектория.
- **Оценка вероятности и визуализация.** По итоговым ценам всех траекторий считается доля случаев, достигших барьера; строятся графики траекторий и гистограмма распределения конечной цены.

### Метод построения GBM-модели

1. **Входные данные:** тикер, срок прогноза (в месяцах), барьер (% от текущей цены), число симуляций, ожидаемая годовая доходность по прогнозу аналитиков.
2. **Оценка волатильности:** на исторических лог-доходностях оценивается GARCH(1,1) с t-распределением; получаются параметры `omega`, `alpha`, `beta`, `nu` (степени свободы) и прогноз условной дисперсии на первый шаг.
3. **Симуляция цены (дискретный аналог GBM с изменяющейся во времени волатильностью):**
   ```
   log_return_t = drift_t + sqrt(h_t) * z_t
   price_t = price_{t-1} * exp(log_return_t)
   h_t = omega + alpha * eps_{t-1}^2 + beta * h_{t-1}
   ```
   где `z_t` — усечённый стандартизированный t-шок, `drift_t` — дрейф, скорректированный так, чтобы среднее по всем траекториям соответствовало прогнозу аналитиков на конец срока.
4. **Оценка вероятности:** доля траекторий, у которых итоговая цена преодолела барьер, даёт эмпирическую оценку вероятности P(price_T ≥ barrier) (или ≤, если барьер ниже стартовой цены).

### Настраиваемые параметры (в начале ноутбука)

| Параметр | Описание |
|---|---|
| `TICKER` | Тикер акции на MOEX |
| `TIMEFRAME` | Таймфрейм свечей: `day` / `week` / `month` / `4h` |
| `MONTHS` | Срок прогноза в месяцах |
| `BARRIER_PERCENT` | Барьер, % от текущей цены |
| `N_SIMULATIONS` | Число траекторий Монте-Карло |
| `ANALYST_FORECAST_12M_PCT` | Ожидаемая доходность за 12 мес. (для коррекции дрейфа) |
| `GARCH_OBSERVATIONS` | Число исторических свечей для оценки GARCH |
| `GARCH_MAX_PERSISTENCE` | Верхний предел `alpha + beta` |
| `DAILY_VOL_FORECAST_PCT` | Опционально: своя волатильность вместо GARCH-прогноза |

### Установка и запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
jupyter notebook "Вероятность цены по GARCH.ipynb"
```

Для доступа к ISS MOEX может потребоваться российский IP (или VPN).

---

## EN

### Description

The notebook [`Вероятность цены по GARCH.ipynb`](Вероятность%20цены%20по%20GARCH.ipynb) ("Probability of price by GARCH") forecasts daily volatility with a GARCH(1,1) model and simulates future stock price paths via Monte Carlo to estimate the probability that the price at the end of the forecast horizon will reach a given barrier.

### Features

- **Data loading.** Historical candles (day/week/month/4h) are fetched directly from the Moscow Exchange ISS API for a given ticker (`SBER`, `GAZP`, `LKOH`, `TATN`, etc.).
- **GARCH(1,1) estimation.** A `ConstantMean` + `GARCH(1,1)` model with Student's t-distributed errors is fit on log returns using the `arch` library, with a persistence constraint `alpha + beta <= GARCH_MAX_PERSISTENCE` to keep the volatility forecast from diverging.
- **Path-dependent Monte Carlo.** For each of `N_SIMULATIONS` simulated paths, the conditional variance is updated at every step following the GARCH recursion `h_t = omega + alpha * eps_{t-1}^2 + beta * h_{t-1}`, rather than being held fixed over the horizon.
- **Student's t shocks instead of normal.** Random shocks are drawn from the same standardized t-distribution used to fit the GARCH model, with tail clipping to prevent unrealistic price blow-ups through `exp()`.
- **Drift correction to match the analyst forecast.** The expected 12-month price return (`ANALYST_FORECAST_12M_PCT`) is converted to a per-candle return, and the simulation drift is numerically corrected (via interpolation of the log moment-generating function of the clipped shock) so that the *mean* simulated price — not just the median path — matches the analyst forecast.
- **Probability estimation and visualization.** The fraction of simulated terminal prices that reach the barrier gives the empirical probability estimate; the notebook also plots sample price paths and the terminal price distribution histogram.

### GBM model construction method

1. **Inputs:** ticker, forecast horizon (months), barrier (% of current price), number of simulations, expected annual return from the analyst forecast.
2. **Volatility estimation:** GARCH(1,1) with a Student's t error distribution is fit on historical log returns, yielding `omega`, `alpha`, `beta`, `nu` (degrees of freedom) and a one-step-ahead conditional variance forecast.
3. **Price simulation (discrete GBM with time-varying volatility):**
   ```
   log_return_t = drift_t + sqrt(h_t) * z_t
   price_t = price_{t-1} * exp(log_return_t)
   h_t = omega + alpha * eps_{t-1}^2 + beta * h_{t-1}
   ```
   where `z_t` is a clipped, standardized t-shock and `drift_t` is corrected so that the average across all simulated paths matches the analyst's end-of-horizon forecast.
4. **Probability estimate:** the share of simulated paths whose terminal price crosses the barrier gives an empirical estimate of P(price_T ≥ barrier) (or ≤, if the barrier is below the starting price).

### Configurable parameters (top of the notebook)

| Parameter | Description |
|---|---|
| `TICKER` | MOEX stock ticker |
| `TIMEFRAME` | Candle timeframe: `day` / `week` / `month` / `4h` |
| `MONTHS` | Forecast horizon in months |
| `BARRIER_PERCENT` | Barrier as % of current price |
| `N_SIMULATIONS` | Number of Monte Carlo paths |
| `ANALYST_FORECAST_12M_PCT` | Expected 12-month return (for drift correction) |
| `GARCH_OBSERVATIONS` | Number of historical candles used to fit GARCH |
| `GARCH_MAX_PERSISTENCE` | Upper bound for `alpha + beta` |
| `DAILY_VOL_FORECAST_PCT` | Optional: supply your own volatility instead of the GARCH forecast |

### Installation and usage

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
jupyter notebook "Вероятность цены по GARCH.ipynb"
```

Access to the ISS MOEX API may require a Russian IP address (or VPN).

---

## Telegram-бот / Telegram bot

`telegram_bot.py` принимает сообщение вида `GAZP, 3 мес, 120, 120`
(тикер, срок, цена аналитиков на 12 мес, барьер) и отвечает вероятностью и графиком.
Срок: `3 мес` / `90 дн` (без единицы — месяцы); барьер — всегда в % от текущей цены (`120` и `120%` равнозначны).

Развёртывание на Linux-сервере (Debian/Ubuntu; нужен IP, с которого доступны iss.moex.com и api.telegram.org):
см. [`deploy/INSTALL.md`](deploy/INSTALL.md) — установка одной командой `sudo bash deploy/install.sh`.
