"""Тест циклу unpublish_offers (без реального браузера)."""

from __future__ import annotations

from rieltor_handler.published_offer_unpublisher import PublishedOfferUnpublisher


class _FakeUnpublisher(PublishedOfferUnpublisher):
    """Підміняє браузерний unpublish записом викликів."""

    def __init__(self, fail_ids: tuple[str, ...] = ()) -> None:
        self.calls: list[str] = []
        self._fail = set(fail_ids)

    def unpublish(self, rieltor_offer_id: str) -> bool:
        self.calls.append(rieltor_offer_id)
        return rieltor_offer_id not in self._fail


def test_unpublish_offers_all_success():
    u = _FakeUnpublisher()
    done = u.unpublish_offers(["1", "2", "3"])
    assert done == ["1", "2", "3"]
    assert u.calls == ["1", "2", "3"]


def test_unpublish_offers_skips_failures():
    u = _FakeUnpublisher(fail_ids=("2",))
    done = u.unpublish_offers(["1", "2", "3"])
    assert done == ["1", "3"]
    assert u.calls == ["1", "2", "3"]


def test_unpublish_offers_dry_run_does_nothing():
    u = _FakeUnpublisher()
    done = u.unpublish_offers(["1", "2"], dry_run=True)
    assert done == ["1", "2"]
    assert u.calls == []
