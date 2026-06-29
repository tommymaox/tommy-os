# Production Checklist

- Validate `ZUYU_APP_ENV`, `ZUYU_DB_PATH`, `ZUYU_LOG_LEVEL`, and `ZUYU_STATIC_DIR` before deploy.
- Run `python -m py_compile main.py zuyu_app/*.py`.
- Run `pytest`, `ruff check zuyu_app tests`, and `mypy zuyu_app tests`.
- Run `npm run check:frontend`.
- Confirm `/api/health` and `/api/status` are healthy after deploy.
- Smoke-test event CRUD, food logging, board, and theme switching on the live host.
- Confirm browser cache refresh after deploy and check `x-request-id` headers for debugging.
- Verify Docker volume `zm-data` is mounted and backed up.
- Review client logs and server logs for startup or validation errors after first traffic.
