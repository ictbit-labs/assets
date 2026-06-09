"""Shared membership helpers.

Membership commands are exposed from the `users` and `groups` Typer modules to
match the requested CLI shape (`idc groups add-user`, `idc users memberships`).
This module exists as the importable home for membership-focused utilities.
"""

from __future__ import annotations

from .identitystore import IdentityStoreService


def membership_exists(service: IdentityStoreService, *, group_id: str, user_id: str) -> bool:
    return service.get_membership_id(group_id, user_id) is not None

