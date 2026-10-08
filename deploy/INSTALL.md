# Установка бота на сервер со своего ноутбука

Нужно: Linux-сервер (Debian/Ubuntu) с sudo, SSH-доступ к нему и токен бота от @BotFather.
Сервер должен достукиваться до `iss.moex.com` и `api.telegram.org` (из-за MOEX — желателен российский IP).

## 1. Подключитесь к серверу с ноутбука

- **macOS / Linux** — терминал:
  ```bash
  ssh ПОЛЬЗОВАТЕЛЬ@АДРЕС_СЕРВЕРА
  ```
- **Windows** — PowerShell (там уже есть `ssh`) или PuTTY:
  ```powershell
  ssh ПОЛЬЗОВАТЕЛЬ@АДРЕС_СЕРВЕРА
  ```
  Если вход по ключу: `ssh -i путь\к\ключу ПОЛЬЗОВАТЕЛЬ@АДРЕС_СЕРВЕРА`.

## 2. Запустите установку (уже на сервере)

```bash
curl -fsSL https://raw.githubusercontent.com/polukeevnikita-26/gbm_for_russian_stock/Bot_version/deploy/install.sh -o install.sh
sudo bash install.sh
```

Скрипт спросит токен (ввод скрыт) и сам: поставит пакеты, склонирует код в
`/opt/GBM_For_Russian_stock`, создаст venv, установит зависимости, сохранит токен в
`/etc/gbm-bot.env` (права 600) и запустит systemd-сервис `gbm-bot` с автозапуском.

> Если репозиторий приватный, `curl` по raw-ссылке не сработает: скопируйте файл с ноутбука —
> `scp deploy/install.sh ПОЛЬЗОВАТЕЛЬ@АДРЕС_СЕРВЕРА:~/` — и задайте URL с токеном GitHub
> или deploy-ключом: `sudo REPO_URL=git@github.com:... bash install.sh`.

## 3. Проверьте

- В Telegram найдите своего бота, отправьте `/start`, затем `GAZP, 3 мес, 120, 120`.
- Логи: `journalctl -u gbm-bot -f`
- Статус: `systemctl status gbm-bot`

## Обновление / управление

```bash
sudo bash install.sh          # подтянуть новую версию кода и перезапустить (токен остаётся)
sudo systemctl restart gbm-bot
sudo systemctl stop gbm-bot
```

Сменить токен: отредактируйте `/etc/gbm-bot.env`, затем `sudo systemctl restart gbm-bot`.

## Быстрый тест на самом ноутбуке (без сервера)

Нужен Python 3.10+ и доступ к MOEX.

```bash
git clone -b Bot_version https://github.com/polukeevnikita-26/gbm_for_russian_stock.git
cd gbm_for_russian_stock
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-bot.txt
export TELEGRAM_BOT_TOKEN=...                            # Windows PowerShell: $env:TELEGRAM_BOT_TOKEN="..."
python telegram_bot.py
```
