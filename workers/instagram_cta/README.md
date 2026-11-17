# Instagram CTA collector

Собирает публичные username авторов комментариев, которые ответили ключевым
словом под свежими Reels указанных конкурентов. Результат сохраняется в
`instagram_leads.csv`; Java-ядро для обычного сбора не требуется.

## Настройка и запуск (PowerShell)

```powershell
cd C:\Users\user\Desktop\qorsa_leadgen
python -m venv .venv
.\.venv\Scripts\python -m pip install -r workers\instagram_cta\requirements.txt
```

1. Заполнить `IG_USERNAME`, `IG_PASSWORD` и при необходимости `IG_PROXY` в
   `workers/instagram_cta/.env`. Использовать отдельный Instagram-аккаунт.
2. Добавить username конкурентов без `@` в `accounts` файла `targets.yml`.
3. Один раз интерактивно создать сессию:

```powershell
.\.venv\Scripts\python -m workers.instagram_cta.bootstrap_login
```

4. Запустить сбор:

```powershell
.\.venv\Scripts\python -m workers.instagram_cta.main
```

Повторные запуски обновляют строки в CSV по стабильному Instagram user id и
не плодят дубли. `SEND_TO_CORE=true` в `.env` дополнительно включает отправку
в `/api/v1/companies/ingest`.

Код использует неофициальный Instagram API. Соблюдайте применимые правила
платформы и законы о персональных данных; не используйте результат для
массового нежелательного контакта.
