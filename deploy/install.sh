#!/usr/bin/env bash
# Установка/обновление Telegram-бота на Linux-сервере (Debian/Ubuntu, systemd).
# Запуск:  sudo bash install.sh
# Повторный запуск = обновление кода и перезапуск (токен сохраняется).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/polukeevnikita-26/gbm_for_russian_stock.git}"
BRANCH="${BRANCH:-Bot_version}"
APP_DIR=/opt/GBM_For_Russian_stock
ENV_FILE=/etc/gbm-bot.env
SERVICE=gbm-bot
APP_USER=gbmbot

[ "$(id -u)" -eq 0 ] || { echo "Запустите через sudo: sudo bash $0"; exit 1; }

echo "==> Установка системных пакетов"
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip || true

echo "==> Пользователь $APP_USER"
id "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$SRC_DIR/telegram_bot.py" ]; then
  echo "==> Код из локальной папки $SRC_DIR (GitHub не нужен)"
  if [ "$SRC_DIR" != "$APP_DIR" ]; then
    mkdir -p "$APP_DIR"
    cp -a "$SRC_DIR/." "$APP_DIR/"
  fi
else
  echo "==> Код из GitHub ($BRANCH)"
  if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch origin "$BRANCH"
    git -C "$APP_DIR" checkout -q "$BRANCH"
    git -C "$APP_DIR" reset --hard "origin/$BRANCH"
  else
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  fi
fi

echo "==> Python-окружение"
"${PYTHON:-python3}" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || {
  echo "Нужен Python 3.9+ (сейчас: $(python3 --version)). venv использует системный Python,"
  echo "поэтому поставьте новый (deadsnakes/pyenv/uv) и запустите: PYTHON=python3.11 sudo -E bash $0"
  exit 1
}
[ -d "$APP_DIR/.venv" ] || "${PYTHON:-python3}" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements-bot.txt"
chown -R "$APP_USER": "$APP_DIR"

echo "==> Токен"
if [ ! -s "$ENV_FILE" ] || ! grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$ENV_FILE"; then
  read -r -s -p "Введите токен бота от @BotFather (ввод скрыт): " TOKEN; echo
  [ -n "$TOKEN" ] || { echo "Токен пустой"; exit 1; }
  umask 077
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TOKEN" > "$ENV_FILE"
else
  echo "Токен уже сохранён в $ENV_FILE (чтобы заменить — отредактируйте файл)"
fi
chmod 600 "$ENV_FILE"

echo "==> systemd"
cp "$APP_DIR/deploy/gbm-bot.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 3
systemctl --no-pager --lines=15 status "$SERVICE" || true

echo
echo "Готово. Логи: journalctl -u $SERVICE -f"
