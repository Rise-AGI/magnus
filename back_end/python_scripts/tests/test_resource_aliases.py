"""Colliding imported resources keep distinct content and write authority."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from test_user_lifecycle import load_function, load_leaf, ns


context = dict(ns)
load_leaf("schemas/_user.py", context)
load_leaf("schemas/_blueprint.py", context)
User = ns["User"]
Blueprint = ns["Blueprint"]
Alias = ns["ResourceAlias"]
resolve = ns["resolve_resource_id"]


class ResourceAliasTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        ns["Base"].metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        self.db.add_all([
            User(id="primary-owner", name="primary-owner"),
            User(id="imported-owner", name="imported-owner"),
        ])
        for key, owner, code in (
            ("common", "primary-owner", "primary-code"),
            ("remote-common", "imported-owner", "imported-code"),
        ):
            self.db.add(Blueprint(
                id=key,
                title=key,
                description="example",
                code=code,
                user_id=owner,
            ))
        self.db.add(Alias(
            source_target="remote",
            kind="blueprint",
            source_id="common",
            destination_id="remote-common",
        ))
        self.db.commit()

    def test_only_matching_endpoint_kind_and_old_id_are_resolved(self):
        for kind, key, source, expected in (
            ("blueprint", "common", "remote", "remote-common"),
            ("blueprint", "common", None, "common"),
            ("blueprint", "common", "another", "common"),
            ("skill", "common", "remote", "common"),
            ("blueprint", "remote-common", "remote", "remote-common"),
        ):
            with self.subTest(kind=kind, source=source):
                self.assertEqual(resolve(self.db, kind, key, source), expected)

    def test_legacy_overwrite_updates_original_resource_after_owner_check(self):
        checked = []
        def authorize(db, user, owner_id, **kwargs):
            checked.append(owner_id)
            if user.id != owner_id:
                raise HTTPException(status_code=403)
        namespace = dict(context)
        namespace.update(
            models=SimpleNamespace(**ns),
            Depends=lambda dependency: None,
            database=SimpleNamespace(get_db=lambda: None),
            get_current_user=lambda: None,
            enforce_field_size_limits=lambda *args: None,
            blueprint_manager=SimpleNamespace(analyze_signature=lambda code: None),
            assert_can_manage_resource=authorize,
            datetime=datetime,
            timezone=timezone,
        )
        create = load_function("routers/blueprints.py", "create_blueprint", namespace)
        body = context["BlueprintCreate"](
            id="common", title="updated", description="example", code="updated-code",
        )
        with self.assertRaises(HTTPException):
            create(body, self.db, self.db.get(User, "primary-owner"), default_target="remote")
        result = create(body, self.db, self.db.get(User, "imported-owner"), default_target="remote")
        self.assertEqual(result.id, "remote-common")
        self.assertEqual(checked, ["imported-owner", "imported-owner"])
        self.assertEqual(self.db.get(Blueprint, "common").code, "primary-code")
        self.assertEqual(self.db.get(Blueprint, "remote-common").code, "updated-code")
        self.assertEqual(body.id, "common")

    def test_legacy_list_ids_round_trip_to_the_same_resource(self):
        for endpoint, expected in ((None, {"common", "remote-common"}), ("remote", {"remote-common"})):
            query = ns["exclude_shadowed_resources"](
                self.db.query(Blueprint), Blueprint, "blueprint", endpoint,
            )
            self.assertEqual(query.count(), len(expected))
            self.assertEqual({row.id for row in query}, expected)
            for row in query.order_by(Blueprint.id).limit(20):
                self.assertEqual(resolve(self.db, "blueprint", row.id, endpoint), row.id)

    def test_legacy_preference_normalizes_both_path_and_body(self):
        namespace = dict(context)
        namespace.update(
            models=SimpleNamespace(**ns),
            Depends=lambda dependency: None,
            database=SimpleNamespace(get_db=lambda: None),
            get_current_user=lambda: None,
            _compute_signature_hash=lambda code: "frozen",
            serialize_json=json.dumps,
            deserialize_json=json.loads,
            enforce_field_size_limits=lambda *args: None,
            datetime=datetime,
            timezone=timezone,
        )
        save = load_function("routers/blueprints.py", "save_blueprint_preference", namespace)
        response = save(
            "common",
            context["BlueprintPreferenceUpdate"](
                blueprint_id="common", blueprint_hash="frozen", cached_params={"example": 3},
            ),
            self.db,
            self.db.get(User, "imported-owner"),
            default_target="remote",
        )
        self.assertEqual(response.blueprint_id, "remote-common")
        self.assertEqual(response.cached_params, {"example": 3})
        self.assertEqual(self.db.query(ns["BlueprintUserPreference"]).one().blueprint_id, "remote-common")

    def test_alias_is_not_recursive_or_an_authorization_grant(self):
        self.db.add(Alias(
            source_target="remote",
            kind="blueprint",
            source_id="remote-common",
            destination_id="must-not-chain",
        ))
        self.db.commit()
        self.assertEqual(resolve(self.db, "blueprint", "common", "remote"), "remote-common")
        self.db.delete(self.db.get(Blueprint, "remote-common"))
        self.db.commit()
        self.assertIsNone(self.db.get(Blueprint, resolve(self.db, "blueprint", "common", "remote")))


if __name__ == "__main__":
    unittest.main()
