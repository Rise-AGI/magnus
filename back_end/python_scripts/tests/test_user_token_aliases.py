"""Imported credentials use real SQLite state; no live server or credentials."""
import hashlib
from types import SimpleNamespace
import unittest

from fastapi import HTTPException, status

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_user_lifecycle import load_function, load_leaf, ns


tokens = dict(ns)
load_leaf("_user_tokens.py", tokens)
User = ns["User"]
Alias = ns["UserTokenAlias"]
ScopedAlias = ns["ScopedUserTokenAlias"]
digest = tokens["token_digest"]
lookup = tokens["find_user_by_token"]
replace = tokens["replace_primary_token"]


class UserTokenAliasTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        ns["Base"].metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, autoflush=False)
        with self.sessions() as db:
            db.add(User(id="owner", name="owner", token="primary"))
            db.add(User(id="other", name="other", token="other-primary"))
            db.commit()
            db.add(Alias(
                digest=digest("imported"),
                user_id="owner",
                primary_digest=digest("primary"),
            ))
            db.commit()

    def test_existing_and_imported_tokens_identify_the_same_user(self):
        with self.sessions() as db:
            self.assertEqual(lookup(db, "primary").id, "owner")
            self.assertEqual(lookup(db, "imported").id, "owner")
            self.assertEqual(lookup(db, "other-primary").id, "other")
            self.assertIsNone(lookup(db, "missing"))
            self.assertEqual(db.get(Alias, digest("imported")).digest, hashlib.sha256(b"imported").hexdigest())

    def test_rotation_revokes_imports_and_reusing_old_primary_does_not_restore_them(self):
        with self.sessions() as db:
            replace(db, db.get(User, "owner"), "replacement")
            db.commit()
        with self.sessions() as db:
            self.assertIsNone(lookup(db, "imported"))
            self.assertIsNone(lookup(db, "primary"))
            self.assertEqual(lookup(db, "replacement").id, "owner")
            replace(db, db.get(User, "owner"), "primary")
            db.commit()
        with self.sessions() as db:
            self.assertIsNone(lookup(db, "imported"))

    def test_uncommitted_rotation_rolls_back_with_its_aliases(self):
        with self.sessions() as db:
            replace(db, db.get(User, "owner"), "replacement")
            db.rollback()
            self.assertEqual(lookup(db, "imported").id, "owner")

    def test_same_primary_does_not_revoke_imports(self):
        with self.sessions() as db:
            replace(db, db.get(User, "owner"), "primary")
            db.commit()
            self.assertEqual(lookup(db, "imported").id, "owner")

    def test_stale_or_orphaned_aliases_never_authenticate(self):
        with self.sessions() as db:
            db.get(User, "owner").token = "changed-outside-the-api"
            db.commit()
            self.assertIsNone(lookup(db, "imported"))
            db.add(Alias(digest=digest("orphan"), user_id="absent", primary_digest=digest("primary")))
            db.commit()
            self.assertIsNone(lookup(db, "orphan"))

    def add_scoped_collision(self, db):
        db.add(ScopedAlias(
            source_target="legacy",
            digest=digest("primary"),
            user_id="other",
            primary_digest=digest("other-primary"),
        ))
        db.commit()

    def test_scoped_collision_preserves_both_accounts(self):
        with self.sessions() as db:
            self.add_scoped_collision(db)
            self.assertEqual(lookup(db, "primary").id, "owner")
            self.assertEqual(lookup(db, "primary", "legacy").id, "other")
            self.assertEqual(lookup(db, "primary", "unrelated").id, "owner")
            self.assertEqual(lookup(db, "other-primary", "legacy").id, "other")

    def test_scoped_rotation_never_falls_through_or_revives(self):
        with self.sessions() as db:
            self.add_scoped_collision(db)
            replace(db, db.get(User, "other"), "new-primary")
            db.commit()
            self.assertIsNone(lookup(db, "primary", "legacy"))
            self.assertEqual(lookup(db, "primary").id, "owner")
            replace(db, db.get(User, "other"), "other-primary")
            db.commit()
            self.assertIsNone(lookup(db, "primary", "legacy"))

    def test_scoped_deletion_leaves_a_revoked_tombstone(self):
        with self.sessions() as db:
            self.add_scoped_collision(db)
            db.delete(db.get(User, "other"))
            db.commit()
            self.assertIsNone(lookup(db, "primary", "legacy"))
            self.assertIsNone(db.get(ScopedAlias, ("legacy", digest("primary"))).user_id)
            self.assertEqual(lookup(db, "primary").id, "owner")

    def test_http_auth_does_not_cache_aliases_past_rotation(self):
        cache = {}
        context = dict(ns)
        context.update(
            models=SimpleNamespace(**ns),
            is_local_auth=False,
            Depends=lambda dependency: None,
            oauth2_scheme=None,
            database=SimpleNamespace(get_db=lambda: None),
            status=status,
            _get_from_cache=cache.get,
            _add_to_cache=lambda token, user: cache.update({token: user.id}),
            find_user_by_token=lookup,
            jwt_signer=SimpleNamespace(decode_access_token=lambda token: None),
        )
        authenticate = load_function("routers/auth.py", "get_current_user", context)
        request = SimpleNamespace(headers={}, query_params={}, cookies={})
        with self.sessions() as db:
            self.assertEqual(authenticate(request, "imported", db).id, "owner")
            self.assertEqual(cache, {})
            replace(db, db.get(User, "owner"), "replacement")
            db.commit()
            with self.assertRaises(HTTPException) as error:
                authenticate(request, "imported", db)
            self.assertEqual(error.exception.status_code, 401)
            authenticate(request, "replacement", db)
            self.assertEqual(cache, {"replacement": "owner"})
            db.add(ScopedAlias(
                source_target="legacy",
                digest=digest("replacement"),
                user_id="other",
                primary_digest=digest("other-primary"),
            ))
            db.commit()
            request.headers["x-magnus-default-execution-target"] = "legacy"
            self.assertEqual(authenticate(request, "replacement", db).id, "other")
            self.assertEqual(cache, {"replacement": "owner"})
            replace(db, db.get(User, "other"), "rotated")
            db.commit()
            with self.assertRaises(HTTPException):
                authenticate(request, "replacement", db)


if __name__ == "__main__":
    unittest.main()
