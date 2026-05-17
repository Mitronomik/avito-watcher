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
- [x] Добавить baseline-режим первого запуска
- [x] Исправить дедупликацию объявлений
- [x] Заменить нестабильный Python hash() на sha256 fallback external_id
- [ ] Добавить error handling для Playwright, Telegram, Ollama
- [x] Сделать LLM-score необязательным и добавить fallback, если LLM/Ollama недоступна
- [x] Реализовать price tracking через price snapshots

### Telegram
- [x] /add URL
- [x] /list
- [x] /pause ID
- [x] /resume ID
- [ ] /remove ID
- [x] /status
- [ ] /run ID

### Scheduler
- [x] Использовать индивидуальный poll_interval_sec
- [x] Добавить next_run_at
- [ ] Добавить jitter
- [ ] Защититься от параллельных запусков через worker lock

### Storage
- [x] search_jobs.baseline_initialized
- [x] search_jobs.last_checked_at
- [x] search_jobs.last_success_at
- [x] search_jobs.last_error
- [x] search_jobs.fail_count
- [x] listing_snapshots для истории цены
- [x] alerts_sent для защиты от повторных уведомлений

### AI scoring
- [x] Возвращать JSON со score, decision, summary, reasons, risks, tags
- [x] Добавить rule-based фильтры до LLM
- [x] Добавить fallback, если Ollama недоступна

### Alerts
- [x] Базовая защита от повторных уведомлений через alerts_sent
- [ ] Price drop alerts

### Validation and parser robustness
- [ ] Real Avito E2E validation
- [ ] Parser robustness: captcha / empty-results classification

## Pre-production hardening

- [ ] Playwright error classification
- [ ] Empty result detection
- [ ] Parser DOM regression tests with saved HTML fixtures
- [ ] Worker lock to prevent parallel processing of the same job
- [ ] Scheduler jitter to avoid predictable polling bursts
- [ ] Real dry-run checklist for safe validation against Avito pages

## V5 - агент недвижимости

- [ ] Оценка выгодности объявления
- [ ] Сравнение с похожими объявлениями
- [ ] Поиск подозрительных признаков
- [ ] Классификация: инвест / семья / срочно смотреть / подозрительно
- [ ] История цен по объектам и районам
- [ ] Ежедневная сводка в Telegram
