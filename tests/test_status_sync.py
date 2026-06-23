"""Тести звірки статусів БД з реальним станом на сайті (dry-run)."""

from __future__ import annotations

import status_sync as ss


def _o(estate_id, status, rid=None, article=None):
    return {
        "estate_id": estate_id,
        "status": status,
        "rieltor_offer_id": rid,
        "article": article or f"A{estate_id}",
    }


def test_reconcile_published_and_draft_matching():
    offers = [_o(1, "posted", "1001"), _o(2, "posted", "1002")]
    r = ss.reconcile_statuses(offers, published_ids=["1001"], draft_ids=["1002"])
    assert [o["estate_id"] for o in r.published_on_site] == [1]
    assert [o["estate_id"] for o in r.draft_on_site] == [2]


def test_reconcile_posted_missing_from_site():
    offers = [_o(3, "posted", "1003")]
    r = ss.reconcile_statuses(offers, published_ids=[], draft_ids=[])
    assert [o["estate_id"] for o in r.posted_missing_from_site] == [3]


def test_reconcile_site_unknown_to_db_sorted():
    offers = [_o(4, "posted", "1004")]
    r = ss.reconcile_statuses(offers, published_ids=["1004", "9999"], draft_ids=["8888"])
    assert r.site_unknown_to_db == ["8888", "9999"]


def test_reconcile_unmatchable_without_rid():
    offers = [_o(5, "failed", None), _o(6, "new", None)]
    r = ss.reconcile_statuses(offers, published_ids=[], draft_ids=[])
    assert {o["estate_id"] for o in r.unmatchable} == {5, 6}


def test_reconcile_ignores_id_type_mismatch():
    # Site IDs may arrive as ints; DB stores strings — matching must be type-agnostic.
    offers = [_o(7, "posted", "1007")]
    r = ss.reconcile_statuses(offers, published_ids=[1007], draft_ids=[])
    assert [o["estate_id"] for o in r.published_on_site] == [7]
    assert r.site_unknown_to_db == []


def test_summary_counts():
    offers = [
        _o(1, "posted", "1001"),  # published
        _o(2, "posted", "1002"),  # draft
        _o(3, "posted", "1003"),  # missing
        _o(5, "failed", None),  # unmatchable
    ]
    r = ss.reconcile_statuses(offers, published_ids=["1001"], draft_ids=["1002"])
    c = ss.summary_counts(r)
    assert c == {
        "published_on_site": 1,
        "draft_on_site": 1,
        "posted_missing_from_site": 1,
        "site_unknown_to_db": 0,
        "unmatchable": 1,
    }
