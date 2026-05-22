from datetime import datetime
import json
from argparse import Namespace

from app import cli
from app.repositories.search_repository import SearchRepository


def _write_profile(tmp_path, content: str):
    p = tmp_path / "profile.toml"
    p.write_text(content, encoding="utf-8")
    return p


def _args(file_path, **kwargs):
    return Namespace(
        file=str(file_path),
        reset_baseline=kwargs.get("reset_baseline", False),
        activate=kwargs.get("activate", False),
        deactivate=kwargs.get("deactivate", False),
        dry_run=kwargs.get("dry_run", False),
    )


def _prepare_db(monkeypatch, db_session):
    monkeypatch.setattr(cli, "init_db", lambda: None)

    class _Ctx:
        def __enter__(self):
            return db_session

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli, "SessionLocal", lambda: _Ctx())


def test_create_new_search_job_from_toml(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)
    file_path = _write_profile(tmp_path, 'name="n1"\nurl="https://example.com"\npoll_interval_sec=600\nis_active=true\n[filters]\nmin_area=28\n')

    cli.cmd_upsert_search_profile(_args(file_path))

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["action"] == "created"
    item = SearchRepository(db_session).get_by_name("n1")
    assert item is not None
    assert item.poll_interval_sec == 600


def test_update_existing_search_job_by_name(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    repo = SearchRepository(db_session)
    existing = repo.create("n2", "https://old")
    db_session.commit()

    file_path = _write_profile(tmp_path, 'name="n2"\nurl="https://new"\n[filters]\ncategory="flats_sale"\n')
    cli.cmd_upsert_search_profile(_args(file_path))

    db_session.refresh(existing)
    assert existing.source_url == "https://new"
    assert existing.filters_json["category"] == "flats_sale"


def test_baseline_preserved_by_default(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    repo = SearchRepository(db_session)
    existing = repo.create("n3", "https://old")
    existing.baseline_initialized = True
    db_session.commit()

    file_path = _write_profile(tmp_path, 'name="n3"\nurl="https://new"\n')
    cli.cmd_upsert_search_profile(_args(file_path))
    db_session.refresh(existing)
    assert existing.baseline_initialized is True


def test_reset_baseline(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    repo = SearchRepository(db_session)
    existing = repo.create("n4", "https://old")
    existing.baseline_initialized = True
    existing.baseline_initialized_at = datetime(2026, 5, 17, 12, 0, 0)
    db_session.commit()

    file_path = _write_profile(tmp_path, 'name="n4"\nurl="https://new"\n')
    cli.cmd_upsert_search_profile(_args(file_path, reset_baseline=True))
    db_session.refresh(existing)
    assert existing.baseline_initialized is False
    assert existing.baseline_initialized_at is None


def test_activate_and_deactivate(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    repo = SearchRepository(db_session)
    existing = repo.create("n5", "https://old")
    existing.is_active = False
    db_session.commit()

    file_path = _write_profile(tmp_path, 'name="n5"\nurl="https://new"\nis_active=false\n')
    cli.cmd_upsert_search_profile(_args(file_path, activate=True))
    db_session.refresh(existing)
    assert existing.is_active is True

    cli.cmd_upsert_search_profile(_args(file_path, deactivate=True))
    db_session.refresh(existing)
    assert existing.is_active is False


def test_dry_run_does_not_write(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)
    file_path = _write_profile(tmp_path, 'name="n6"\nurl="https://new"\n')
    cli.cmd_upsert_search_profile(_args(file_path, dry_run=True))
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "dry_run"
    assert SearchRepository(db_session).get_by_name("n6") is None


def test_validation_errors(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)

    cli.cmd_upsert_search_profile(_args(_write_profile(tmp_path, 'url="https://x"\n')))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False

    cli.cmd_upsert_search_profile(_args(_write_profile(tmp_path, 'name="x"\n')))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False

    cli.cmd_upsert_search_profile(_args(_write_profile(tmp_path, 'name="x"\nurl="https://x"\npoll_interval_sec=0\n')))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_filters_and_title_stored(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    file_path = _write_profile(
        tmp_path,
        'name="n7"\nurl="https://x"\ntitle="Human"\n[filters]\ncity="spb"\n',
    )
    cli.cmd_upsert_search_profile(_args(file_path))
    item = SearchRepository(db_session).get_by_name("n7")
    assert item.filters_json["city"] == "spb"
    assert item.filters_json["human_title"] == "Human"


def test_seed_search_unchanged(monkeypatch, db_session):
    _prepare_db(monkeypatch, db_session)
    cli.cmd_seed_search(Namespace(name="seeded", url="https://example.com", interval=180))
    item = SearchRepository(db_session).get_by_name("seeded")
    assert item is not None
    assert item.filters_json == {"seeded": True, "label": "seeded"}
