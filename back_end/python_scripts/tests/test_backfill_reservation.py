"""Counterexamples and release-order invariants for resource-only backfill."""
import importlib.util
import itertools
from pathlib import Path
import sys
import unittest


PATH = Path(__file__).resolve().parents[2] / "library" / "fundamental" / "scheduling.py"
SPEC = importlib.util.spec_from_file_location("backfill_reservation_test_module", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
ResourceVector = MODULE.ResourceVector
BackfillCandidate = MODULE.BackfillCandidate


class BackfillReservationTests(unittest.TestCase):
    def select(
        self,
        total,
        free,
        *demands,
    ):
        return MODULE.select_conservative_backfill(
            candidates = [
                BackfillCandidate(payload=index, demand=ResourceVector(tuple(demand)))
                for index, demand in enumerate(demands)
            ],
            cluster_total = ResourceVector(tuple(total)),
            cluster_free = ResourceVector(tuple(free)),
        )

    def test_capacity_coexistence_does_not_prove_no_delay(self):
        self.assertEqual(self.select((8,), (2,), (4,), (2,)), [])
        self.assertEqual(self.select((10,), (4,), (7,), (2,), (2,)), [])

    def test_spends_only_cumulative_surplus_while_head_waits_for_gpu(self):
        self.assertEqual(
            self.select((4, 8), (0, 8), (2, 4), (0, 3), (0, 2), (0, 1)),
            [1, 3],
        )

    def test_head_that_fits_keeps_priority_prefix(self):
        self.assertEqual(self.select((8,), (8,), (2,), (7,), (1,)), [0])

    def test_unfulfillable_head_is_not_silently_bypassed(self):
        self.assertEqual(self.select((8,), (8,), (9,), (1,)), [])

    def test_invalid_capacity_and_dimensions_fail(self):
        with self.assertRaises(ValueError):
            self.select((8,), (9,), (1,))
        with self.assertRaises(ValueError):
            self.select((8,), (8,), (1, 1))

    def test_every_small_future_release_preserves_head_feasibility(self):
        total = (3, 3)
        for free in itertools.product(range(4), repeat=2):
            for head in itertools.product(range(4), repeat=2):
                if all(h <= f for h, f in zip(head, free)):
                    continue
                later = [(0, 1), (1, 0), (1, 1), (0, 1)]
                demands = [head, *later]
                selected = self.select(total, free, *demands)
                spent = tuple(sum(demands[index][axis] for index in selected) for axis in range(2))
                for released in itertools.product(*(range(t - f + 1) for t, f in zip(total, free))):
                    if all(h <= f + r for h, f, r in zip(head, free, released)):
                        self.assertTrue(all(h <= f + r - s for h, f, r, s in zip(head, free, released, spent)))


if __name__ == "__main__":
    unittest.main()
