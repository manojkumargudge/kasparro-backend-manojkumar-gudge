import json
import logging

import pytest

from core.logger import JsonLogFormatter


def _format(level: int, msg: str, **kwargs) -> dict:
    logger = logging.getLogger("t")
    record = logger.makeRecord("t", level, __file__, 1, msg, (), kwargs.pop("exc_info", None), extra=kwargs)
    return json.loads(JsonLogFormatter().format(record))


@pytest.mark.parametrize(
    "level,severity",
    [(logging.DEBUG, "DEBUG"), (logging.INFO, "INFO"), (logging.WARNING, "WARNING"), (logging.ERROR, "ERROR"),
     (logging.CRITICAL, "CRITICAL")],
)  # fmt: skip
def test_cloud_logging_severity(level, severity):
    entry = _format(level, "hello")
    assert entry["severity"] == severity
    assert entry["message"] == "hello"
    assert entry["time"].endswith("+00:00")


def test_app_import_installs_json_logging_for_uvicorn():
    import api.app

    logging.getLogger("uvicorn.error").handlers = [logging.NullHandler()]  # what uvicorn's dictConfig does
    api.app.create_app()
    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonLogFormatter) for h in root.handlers)
    assert logging.getLogger("uvicorn.error").handlers == []
    assert logging.getLogger("uvicorn.access").propagate is True


def test_extra_fields_and_exceptions_are_structured():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        entry = _format(logging.ERROR, "failed", event="etl_run_finished", source="csv", exc_info=sys.exc_info())
    assert (entry["event"], entry["source"]) == ("etl_run_finished", "csv")
    assert "ValueError: boom" in entry["exception"]
