# Развёртывание BIBAS

Для одной мастерской: несколько Gunicorn workers и PostgreSQL.
Начальный размер сервера определяется нагрузочным тестом и объёмом заказов.

1. Подготовить Linux-хост с Docker и Compose, домен и HTTPS reverse proxy.
2. Скопировать .env.example в .env. Создать независимые случайные секреты:
   DJANGO_SECRET_KEY и POSTGRES_PASSWORD. Не хранить .env в GitHub.
3. Указать реальный домен в DJANGO_ALLOWED_HOSTS и HTTPS origin в
   CSRF_TRUSTED_ORIGINS. Не использовать wildcard.
4. docker compose up -d db
5. docker compose build web
6. docker compose run --rm web python manage.py migrate
7. docker compose run --rm web python manage.py setup_roles
8. docker compose run --rm web python manage.py createsuperuser
9. docker compose run --rm web python manage.py check --deploy
10. Настроить reverse proxy: TLS, ограничение попыток входа для /accounts/login/
    и /admin/login/, ограничение размера запроса. Прокси должен перезаписывать,
    а не дополнять X-Forwarded-Proto. Только после этого включить TRUST_PROXY_HTTPS=1.
11. docker compose up -d web. Проверить вход, выход, права каждой роли,
    создание заказа и конкурентную оплату на тестовых данных.
12. Настроить ежедневный pg_dump в отдельное защищённое хранилище, срок хранения
    и мониторинг ошибок. Проверить восстановление на отдельной базе.

Порты PostgreSQL наружу не опубликованы. Порт приложения привязан к 127.0.0.1.
Если прокси работает в другом контейнере, настроить внутреннюю сеть явно.
Не открывать runserver в интернет. Docker volume не является резервной копией.

Миграции выполняются отдельной командой один раз на релиз, не каждым worker.
Перед обновлением — резервная копия; откат схемы проверяется на staging.
Для удалённой управляемой PostgreSQL дополнительно настроить TLS соединения
с проверкой сертификата согласно провайдеру; compose использует частную сеть.

Перед запуском на 50 сотрудников: тест 50 активных сессий, замер p95 для поиска,
создания заказа и оплаты, наблюдение за CPU/RAM/DB connections и блокировками.
CI-тест двух одновременных оплат не заменяет нагрузочный тест.
