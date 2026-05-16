# Avito Watcher Roadmap

## Цель

Создать сервис мониторинга объявлений Авито без использования API Авито.

Сервис должен:
- отслеживать новые объявления по заданным ссылкам;
- не спамить старыми объявлениями при первом запуске;
- фиксировать изменения цены;
- анализировать объявления через LLM/локальную модель;
- отправлять уведомления в Telegram;
- управляться через Telegram-бота.

## V4 - надежный MVP

### Критично
- [ ] Добавить baseline-режим первого запуска
- [ ] Исправить дедупликацию объявлений
- [ ] Заменить нестабильный Python hash() на sha256
- [ ] Добавить error handling для Playwright, Telegram, Ollama
- [ ] Сделать LLM-score необязательным
- [ ] Реализовать price tracking

### Telegram
- [ ] /add URL
- [ ] /list
- [ ] /pause ID
- [ ] /resume ID
- [ ] /remove ID
- [ ] /status
- [ ] /run ID

### Scheduler
- [ ] Использовать индивидуальный poll_interval_sec
- [ ] Добавить next_run_at
- [ ] Добавить jitter
- [ ] Защититься от параллельных запусков

### Storage
- [ ] search_jobs.baseline_initialized
- [ ] search_jobs.last_checked_at
- [ ] search_jobs.last_success_at
- [ ] search_jobs.last_error
- [ ] search_jobs.fail_count
- [ ] listing_snapshots для истории цены
- [ ] alerts_sent для защиты от повторных уведомлений

### AI scoring
- [ ] Возвращать JSON со score, decision, summary, reasons, risks, tags
- [ ] Добавить rule-based фильтры до LLM
- [ ] Добавить fallback, если Ollama недоступна

## V5 - агент недвижимости

- [ ] Оценка выгодности объявления
- [ ] Сравнение с похожими объявлениями
- [ ] Поиск подозрительных признаков
- [ ] Классификация: инвест / семья / срочно смотреть / подозрительно
- [ ] История цен по объектам и районам
- [ ] Ежедневная сводка в Telegram
