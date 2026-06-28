from __future__ import annotations

from pipe1_license_server.admin import _new_id
from pipe1_license_server.app import _id


def test_admin_ids_fit_model_primary_key_lengths() -> None:
    prefixes = (
        "org",
        "lic",
        "key",
        "act",
        "feature",
        "quota",
        "usage",
        "ent",
        "audit",
        "trs",
        "trc",
        "sample",
    )
    for prefix in prefixes:
        assert len(_new_id(prefix)) <= 36


def test_api_ids_fit_model_primary_key_lengths() -> None:
    prefixes = (
        "act",
        "ent",
        "trs",
        "trc",
        "sample",
    )
    for prefix in prefixes:
        assert len(_id(prefix)) <= 36
