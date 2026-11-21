from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase

from backup_manager.convergence import logical_identity, plan_union, transfer, verify_transfer
from backup_manager.convergence import NativeSnapshot


def snapshot(char, *, original=None, tags=()):
    return NativeSnapshot(char * 64, original, "a" * 64, "2025-01-01T00:00:00+00:00", "host", ("/data",), tags)


class ConvergenceTests(TestCase):
    def setUp(self):
        self.a = snapshot("1")
        self.copied = snapshot("2", original=self.a.id)
        self.independent = snapshot("3")
        self.rewritten = replace(self.copied, id="4" * 64, tags=("rewritten",))
        self.registrations = {name: SimpleNamespace(key=name, generation=1) for name in "abc"}

    def test_origin_and_metadata_are_both_required(self):
        self.assertEqual(logical_identity(self.a), logical_identity(self.copied))
        self.assertNotEqual(logical_identity(self.a), logical_identity(self.independent))
        self.assertNotEqual(logical_identity(self.a), logical_identity(self.rewritten))

    def test_pending_can_replan_to_alternative_and_blocked_cannot(self):
        views = {"a": (self.a,), "b": (self.copied,), "c": ()}
        union = {}
        first, _ = plan_union(views, self.registrations, union, set(), set())
        self.assertEqual((first.source, first.destination), ("a", "c"))
        requirement, = first.requirements
        # The first source failed before launch or lost its snapshot.  A
        # fresh peer representation can take over the still-pending work.
        rejected = {("a", requirement.id, self.a.id)}
        alternative, _ = plan_union(views, self.registrations, union, set(), rejected)
        self.assertEqual(alternative.source, "b")
        blocked, pending = plan_union(views, self.registrations, union, {requirement.id}, rejected)
        self.assertIsNone(blocked)
        self.assertEqual(pending, ())
        # A failed refresh cannot silently remove previously required history.
        lost, pending = plan_union({"c": ()}, self.registrations, union, set(), set())
        self.assertIsNone(lost)
        self.assertEqual(pending, (requirement,))

    def test_copy_confirmation_requires_exact_source_and_destination_representation(self):
        plan = transfer("a", "c", self.registrations["c"], (self.a,))
        self.assertEqual(verify_transfer(plan, (), (self.copied,)), ())
        self.assertEqual(verify_transfer(plan, (self.a,), (self.independent, self.rewritten)), ())
        self.assertEqual(verify_transfer(plan, (self.a,), (self.copied,)), ((self.a.id, self.copied.id),))

    def test_repository_generation_changes_requirement_identity(self):
        first = transfer("a", "c", self.registrations["c"], (self.a, self.copied))
        self.assertEqual(len(first.requirements), 1)
        self.assertEqual(len(first.snapshots), 2)
        second = transfer("a", "c", SimpleNamespace(key="c", generation=2), (self.a,))
        self.assertNotEqual(first.requirements[0].id, second.requirements[0].id)

    def test_native_alias_and_membership_with_full_metadata(self):
        from backup_manager.convergence import representation, representation_index

        retained = replace(self.copied, tags=("first", "second"), username="user", uid=1000, gid=1001,
                           excludes=("one", "two"))
        rewrite = replace(retained, id="7" * 64, original=retained.id,
                          tags=("second", "first"), time="2025-01-01T05:30:00+05:30")
        self.assertNotEqual(logical_identity(retained), logical_identity(rewrite))
        self.assertEqual(representation(rewrite, representation_index((retained,))), retained)
        self.assertIsNone(representation(retained, representation_index((rewrite,))))
        self.assertIsNone(representation(rewrite, representation_index((replace(retained, gid=1002),))))
        self.assertIsNone(representation(rewrite, representation_index((replace(retained, excludes=("two", "one")),))))
        self.assertIsNone(representation(
            replace(rewrite, time="2025-01-01T05:30:00.000000001+05:30"), representation_index((retained,)),
        ))
        duplicate = replace(rewrite, tags=("first", "first"))
        self.assertEqual(representation(duplicate, representation_index((retained,))), retained)

    def test_prelaunch_replanning_can_choose_another_local_representation(self):
        duplicate = replace(self.copied, id="7" * 64)
        views = {"a": (self.copied, duplicate), "c": ()}
        union = {}
        first, _ = plan_union(views, self.registrations, union, set(), set())
        requirement, = first.requirements
        second, _ = plan_union(
            views, self.registrations, union, set(), {("a", requirement.id, self.copied.id)},
        )
        self.assertEqual(second.snapshots, (duplicate,))
        self.assertEqual(second.requirements, first.requirements)

    def test_disappeared_success_is_incomplete_without_repeating_transfer(self):
        views = {"a": (self.a,), "c": ()}
        union = {}
        first, _ = plan_union(views, self.registrations, union, set(), set())
        requirement, = first.requirements
        retry, pending = plan_union(views, self.registrations, union, set(), set(), {requirement.id})
        self.assertIsNone(retry)
        self.assertEqual(pending, (requirement,))
