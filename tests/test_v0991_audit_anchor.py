"""V0.9.9.1 — the audit chain head a backup manifest recorded, compared with a database
(server/audit.py compare_head, scripts/consistency_check.py --audit-head, used by
scripts/restore.sh step 4 before anything is switched).

Why it is needed: deleting the LAST audit row leaves a chain that still verifies (every remaining
link is intact), and whoever holds the integrity key can rewrite the last row with a valid MAC.
Only the head recorded elsewhere (the manifest) shows either. The end-to-end restore drills are in
tests/test_v098_backup_restore.py; these tests pin the rules on the normal test database."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from server import audit, integrity
from server.database import SessionLocal
from server.models import AuditEvent


@pytest.fixture()
def chain(client):
    """Three fresh audit rows; returns the head the "backup" recorded."""
    with SessionLocal() as db:
        db.execute(sa.text("DELETE FROM audit_events"))
        db.commit()
        for i in range(3):
            audit.append(db, "integrity.sweep", actor="script", detail={"run": i})
        db.commit()
        head = audit.head(db)
    assert head is not None
    return head


def _spec(head):
    return f"{head['id']}:{head['mac']}"


def _check(*args):
    from scripts.consistency_check import main

    return main(["--json", *args])


def test_the_spec_is_parsed_strictly():
    assert audit.parse_head_spec("absent") == audit.HEAD_ABSENT
    assert audit.parse_head_spec("none") == audit.HEAD_NONE
    assert audit.parse_head_spec("12:v1:ab:cd") == {"id": 12, "mac": "v1:ab:cd"}
    for bad in ("", "12", "x:v1:ab", ":v1:ab", "12:", "null"):
        with pytest.raises(ValueError):
            audit.parse_head_spec(bad)


def test_an_unchanged_database_matches_its_backup_head(chain, capsys):
    with SessionLocal() as db:
        result = audit.compare_head(db, audit.parse_head_spec(_spec(chain)))
    assert result == {"ok": True, "expected": chain, "found": chain, "problem": None}
    assert _check("--audit-head", _spec(chain)) == 0


def test_a_deleted_last_row_keeps_the_chain_intact_but_not_the_head(chain, capsys):
    with SessionLocal() as db:
        db.execute(sa.text("DELETE FROM audit_events WHERE id = :i"), {"i": chain["id"]})
        db.commit()
        assert audit.verify_db(db)["ok"], "precondition: the chain alone cannot see a missing last row"
        result = audit.compare_head(db, audit.parse_head_spec(_spec(chain)))
    assert not result["ok"] and result["found"]["id"] == chain["id"] - 1
    assert "rows at the end are missing" in result["problem"]
    capsys.readouterr()
    assert _check("--audit-head", _spec(chain)) == 1


def test_a_last_row_rewritten_with_the_key_keeps_the_chain_intact_but_not_the_head(chain, capsys):
    with SessionLocal() as db:
        last = db.get(AuditEvent, chain["id"])
        prev_mac, kind, actor, created = last.prev_mac, last.kind, last.actor, last.created_at
        detail = dict(last.detail, run=99)                       # the forged content
        db.execute(sa.text("DELETE FROM audit_events WHERE id = :i"), {"i": chain["id"]})
        forged_mac = integrity.sign(audit._message(prev_mac, audit._canonical(kind, actor, None, None, detail)))
        db.add(AuditEvent(id=chain["id"], created_at=created, kind=kind, actor=actor, detail=detail,
                          prev_mac=prev_mac, mac=forged_mac))
        db.commit()
        assert audit.verify_db(db)["ok"], "precondition: a row rewritten with the key verifies"
        result = audit.compare_head(db, audit.parse_head_spec(_spec(chain)))
    assert not result["ok"] and result["found"] == {"id": chain["id"], "mac": forged_mac}
    assert result["problem"] == f"row {chain['id']} differs from the backup"
    capsys.readouterr()
    assert _check("--audit-head", _spec(chain)) == 1


def test_a_row_added_after_the_backup_differs_too(chain):
    with SessionLocal() as db:
        audit.append(db, "integrity.sweep", actor="script", detail={"run": "later"})
        db.commit()
        result = audit.compare_head(db, audit.parse_head_spec(_spec(chain)))
    assert not result["ok"] and "rows were added" in result["problem"]


def test_backups_without_or_with_an_empty_head(chain, capsys):
    """Compatibility: a manifest without audit_head (before V0.9.9) is not compared; "audit_head":
    null means the backed-up database had no audit row, so the restored one must have none."""
    with SessionLocal() as db:
        assert audit.compare_head(db, audit.HEAD_ABSENT)["ok"]
        none = audit.compare_head(db, audit.HEAD_NONE)
        assert not none["ok"] and none["found"] == chain
    assert _check("--audit-head", "absent") == 0
    assert _check("--audit-head", "none") == 1
    with SessionLocal() as db:
        db.execute(sa.text("DELETE FROM audit_events"))
        db.commit()
        assert audit.compare_head(db, audit.HEAD_NONE)["ok"]
        missing = audit.compare_head(db, audit.parse_head_spec(_spec(chain)))
    assert not missing["ok"] and missing["problem"] == "the database has no audit row"
    assert _check("--audit-head", "none") == 0


def test_an_unreadable_spec_is_refused_not_ignored(client):
    with pytest.raises(SystemExit) as exc:
        _check("--audit-head", "garbage")
    assert exc.value.code == 2
