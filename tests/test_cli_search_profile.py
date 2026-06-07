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
    file_path = _write_profile(tmp_path, 'name="n1_ok"\nurl="https://www.avito.ru/test"\npoll_interval_sec=600\nis_active=true\n[filters]\nmin_area=28\n')

    cli.cmd_upsert_search_profile(_args(file_path))

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["action"] == "created"
    item = SearchRepository(db_session).get_by_name("n1_ok")
    assert item is not None
    assert item.poll_interval_sec == 600


def test_update_existing_search_job_by_name(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    repo = SearchRepository(db_session)
    existing = repo.create("n2_ok", "https://old")
    db_session.commit()

    file_path = _write_profile(tmp_path, 'name="n2_ok"\nurl="https://www.avito.ru/new"\n[filters]\ncategory="flats_sale"\n')
    cli.cmd_upsert_search_profile(_args(file_path))

    db_session.refresh(existing)
    assert existing.source_url == "https://www.avito.ru/new"
    assert existing.filters_json["category"] == "flats_sale"


def test_baseline_preserved_by_default(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    repo = SearchRepository(db_session)
    existing = repo.create("n3_ok", "https://old")
    existing.baseline_initialized = True
    db_session.commit()

    file_path = _write_profile(tmp_path, 'name="n3_ok"\nurl="https://www.avito.ru/new"\n')
    cli.cmd_upsert_search_profile(_args(file_path))
    db_session.refresh(existing)
    assert existing.baseline_initialized is True


def test_reset_baseline(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    repo = SearchRepository(db_session)
    existing = repo.create("n4_ok", "https://old")
    existing.baseline_initialized = True
    existing.baseline_initialized_at = datetime(2026, 5, 17, 12, 0, 0)
    db_session.commit()

    file_path = _write_profile(tmp_path, 'name="n4_ok"\nurl="https://www.avito.ru/new"\n')
    cli.cmd_upsert_search_profile(_args(file_path, reset_baseline=True))
    db_session.refresh(existing)
    assert existing.baseline_initialized is False
    assert existing.baseline_initialized_at is None


def test_activate_and_deactivate(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    repo = SearchRepository(db_session)
    existing = repo.create("n5_ok", "https://old")
    existing.is_active = False
    db_session.commit()

    file_path = _write_profile(tmp_path, 'name="n5_ok"\nurl="https://www.avito.ru/new"\nis_active=false\n')
    cli.cmd_upsert_search_profile(_args(file_path, activate=True))
    db_session.refresh(existing)
    assert existing.is_active is True

    cli.cmd_upsert_search_profile(_args(file_path, deactivate=True))
    db_session.refresh(existing)
    assert existing.is_active is False


def test_dry_run_does_not_write(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)
    file_path = _write_profile(tmp_path, 'name="n6_ok"\nurl="https://www.avito.ru/new"\n')
    cli.cmd_upsert_search_profile(_args(file_path, dry_run=True))
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "dry_run"
    assert SearchRepository(db_session).get_by_name("n6_ok") is None


def test_validation_errors(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)

    cli.cmd_upsert_search_profile(_args(_write_profile(tmp_path, 'url="https://www.avito.ru/x"\n')))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False

    cli.cmd_upsert_search_profile(_args(_write_profile(tmp_path, 'name="x"\n')))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False

    cli.cmd_upsert_search_profile(_args(_write_profile(tmp_path, 'name="x"\nurl="https://www.avito.ru/x"\npoll_interval_sec=0\n')))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_filters_and_title_stored(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    file_path = _write_profile(
        tmp_path,
        'name="n7_ok"\nurl="https://www.avito.ru/x"\ntitle="Human"\n[filters]\ncity="spb"\n',
    )
    cli.cmd_upsert_search_profile(_args(file_path))
    item = SearchRepository(db_session).get_by_name("n7_ok")
    assert item.filters_json["city"] == "spb"
    assert item.filters_json["human_title"] == "Human"


def test_filters_preserve_analysis_metadata(monkeypatch, db_session, tmp_path):
    _prepare_db(monkeypatch, db_session)
    file_path = _write_profile(
        tmp_path,
        'name="analysis_ok"\nurl="https://www.avito.ru/spb/kommercheskaya_nedvizhimost/"\n[filters]\nanalysis_profile="commercial_rent"\nasset_type="commercial"\ndeal_type="rent"\n',
    )

    cli.cmd_upsert_search_profile(_args(file_path))

    item = SearchRepository(db_session).get_by_name("analysis_ok")
    assert item.filters_json["analysis_profile"] == "commercial_rent"
    assert item.filters_json["asset_type"] == "commercial"
    assert item.filters_json["deal_type"] == "rent"


def test_seed_search_unchanged(monkeypatch, db_session):
    _prepare_db(monkeypatch, db_session)
    cli.cmd_seed_search(Namespace(name="seeded", url="https://www.avito.ru/test", interval=180))
    item = SearchRepository(db_session).get_by_name("seeded")
    assert item is not None
    assert item.filters_json == {"seeded": True, "label": "seeded"}


def test_validation_non_avito_url(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)
    cli.cmd_upsert_search_profile(_args(_write_profile(tmp_path, 'name="valid_name"\nurl="https://example.com"\n')))
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": False, "error_type": "validation_error", "error": "url must be a valid avito.ru URL"}


def test_validation_invalid_name(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)
    cli.cmd_upsert_search_profile(_args(_write_profile(tmp_path, 'name="Bad Name"\nurl="https://www.avito.ru/x"\n')))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "must match" in out["error"]


def test_conflicting_flags(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)
    f = _write_profile(tmp_path, 'name="valid_name"\nurl="https://www.avito.ru/x"\n')
    cli.cmd_upsert_search_profile(_args(f, activate=True, deactivate=True))
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": False, "error_type": "validation_error", "error": "--activate and --deactivate cannot be used together"}


def test_source_url_preview_truncated(monkeypatch, db_session, tmp_path, capsys):
    _prepare_db(monkeypatch, db_session)
    long_url = "https://www.avito.ru/" + ("a" * 250)
    f = _write_profile(tmp_path, f'name="preview_ok"\nurl="{long_url}"\n')
    cli.cmd_upsert_search_profile(_args(f))
    out = json.loads(capsys.readouterr().out)
    assert len(out["source_url_preview"]) == 180
    item = SearchRepository(db_session).get_by_name("preview_ok")
    assert item.source_url == long_url
