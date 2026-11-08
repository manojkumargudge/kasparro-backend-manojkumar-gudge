import pytest
from sqlalchemy.exc import OperationalError

import core.migrations
from scripts import check_db, init_db


def test_init_db_applies_migrations(capsys):
    init_db.main()
    assert "migrations applied" in capsys.readouterr().out


def test_init_db_gives_up(monkeypatch):
    def down() -> None:
        raise OperationalError("x", {}, Exception("down"))

    monkeypatch.setattr(core.migrations, "run_migrations", down)
    monkeypatch.setattr(init_db.time, "sleep", lambda s: None)
    monkeypatch.setenv("DB_INIT_ATTEMPTS", "2")
    with pytest.raises(SystemExit):
        init_db.main()


def test_check_db_masks_password(capsys):
    assert check_db.main() == 0
    out = capsys.readouterr().out
    assert "connected to database" in out
    assert ":postgres@" not in out and "***" in out


def test_check_db_reports_errors(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secretpw@127.0.0.1:1/nope")
    assert check_db.main() == 1
    assert "secretpw" not in capsys.readouterr().out.split("ERROR")[0]
    monkeypatch.delenv("DATABASE_URL")
    assert check_db.main() == 1
