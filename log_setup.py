import atexit
import json
import logging
import os
import queue
from contextvars import ContextVar, Token
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import Any


_LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("farm_log_context", default={})
_LOG_LISTENER: QueueListener | None = None
_IS_CONFIGURED = False

DEFAULT_FIELDS: dict[str, str] = {
    "event": "-",
    "command": "-",
    "user_id": "-",
    "chat_id": "-",
    "thread_id": "-",
    "trace_id": "-",
    "duration_ms": "-",
    "db_op": "-",
}


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


class ContextEnricher(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = _LOG_CONTEXT.get()
        for key, fallback in DEFAULT_FIELDS.items():
            value = context.get(key, getattr(record, key, fallback))
            if value is None or value == "":
                value = fallback
            if key == "duration_ms" and isinstance(value, (float, int)):
                value = f"{value:.2f}"
            setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", DEFAULT_FIELDS["event"]),
            "command": getattr(record, "command", DEFAULT_FIELDS["command"]),
            "user_id": getattr(record, "user_id", DEFAULT_FIELDS["user_id"]),
            "chat_id": getattr(record, "chat_id", DEFAULT_FIELDS["chat_id"]),
            "thread_id": getattr(record, "thread_id", DEFAULT_FIELDS["thread_id"]),
            "trace_id": getattr(record, "trace_id", DEFAULT_FIELDS["trace_id"]),
            "duration_ms": getattr(record, "duration_ms", DEFAULT_FIELDS["duration_ms"]),
            "db_op": getattr(record, "db_op", DEFAULT_FIELDS["db_op"]),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class NonBlockingQueueHandler(QueueHandler):
    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            try:
                # Drop one old entry and keep the latest to avoid blocking business logic.
                self.queue.get_nowait()
            except queue.Empty:
                return
            try:
                self.queue.put_nowait(record)
            except queue.Full:
                return


def bind_context(**values: Any) -> Token[dict[str, Any]]:
    context = dict(_LOG_CONTEXT.get())
    for key, value in values.items():
        if value is None:
            continue
        context[key] = value
    return _LOG_CONTEXT.set(context)


def clear_context(token: Token[dict[str, Any]]) -> None:
    _LOG_CONTEXT.reset(token)


def configure_logging() -> None:
    global _LOG_LISTENER, _IS_CONFIGURED

    if _IS_CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    console_level_name = os.getenv("LOG_CONSOLE_LEVEL", level_name).upper()
    console_level = getattr(logging, console_level_name, level)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    queue_size = _read_int("LOG_QUEUE_SIZE", default=10000, minimum=128, maximum=200000)
    file_max_bytes = _read_int("LOG_FILE_MAX_BYTES", default=10 * 1024 * 1024, minimum=1024 * 1024, maximum=200 * 1024 * 1024)
    backup_count = _read_int("LOG_BACKUP_COUNT", default=7, minimum=1, maximum=180)
    use_json = os.getenv("LOG_JSON", "0").strip().lower() in {"1", "true", "yes", "on"}

    text_format = (
        "%(asctime)s %(levelname)s %(name)s %(message)s | "
        "event=%(event)s command=%(command)s user=%(user_id)s chat=%(chat_id)s "
        "thread=%(thread_id)s trace=%(trace_id)s duration_ms=%(duration_ms)s db_op=%(db_op)s"
    )
    if use_json:
        formatter: logging.Formatter = JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    else:
        formatter = logging.Formatter(text_format, datefmt="%Y-%m-%d %H:%M:%S")

    context_filter = ContextEnricher()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)

    app_file_handler = RotatingFileHandler(
        log_dir / "farm.log",
        maxBytes=file_max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    app_file_handler.setLevel(level)
    app_file_handler.setFormatter(formatter)
    app_file_handler.addFilter(context_filter)

    error_file_handler = RotatingFileHandler(
        log_dir / "farm.error.log",
        maxBytes=file_max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)
    error_file_handler.addFilter(context_filter)

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=queue_size)
    queue_handler = NonBlockingQueueHandler(log_queue)
    queue_handler.setLevel(level)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(queue_handler)

    _LOG_LISTENER = QueueListener(
        log_queue,
        console_handler,
        app_file_handler,
        error_file_handler,
        respect_handler_level=True,
    )
    _LOG_LISTENER.start()
    _IS_CONFIGURED = True

    logging.captureWarnings(True)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    atexit.register(shutdown_logging)


def shutdown_logging() -> None:
    global _LOG_LISTENER, _IS_CONFIGURED
    if _LOG_LISTENER:
        _LOG_LISTENER.stop()
        _LOG_LISTENER = None
    _IS_CONFIGURED = False
