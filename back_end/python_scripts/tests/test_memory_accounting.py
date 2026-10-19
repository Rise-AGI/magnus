"""Memory unit/rounding regressions without loading site configuration."""
import ast
from pathlib import Path
import unittest


SERVER = Path(__file__).resolve().parents[2] / "server"
NAMESPACE = {}
exec(compile((SERVER / "_size_utils.py").read_text(encoding="utf-8"), "_size_utils.py", "exec"), NAMESPACE)


class MemoryAccountingTests(unittest.TestCase):
    def test_unsuffixed_memory_uses_mib_without_changing_storage(self):
        self.assertEqual(NAMESPACE["parse_memory_bytes"]("4000"), 4000 * 1024**2)
        self.assertEqual(NAMESPACE["_parse_size_string"]("4000"), 4000)

    def test_core_rounding_never_discards_fractional_mib(self):
        effective = NAMESPACE["effective_cpu_count_per_cpu"]
        self.assertEqual(effective(1, "4000M", 4000), 1)
        self.assertEqual(effective(1, "4000.5M", 4000), 2)
        self.assertEqual(effective(1, "4001", 4000), 2)
        self.assertEqual(effective(3, "4001", 4000), 3)

    def test_invalid_memory_and_zero_sentinel(self):
        for value in ("-1", "-1G", "", "NaN", "InfG", "1e10000G", "1e1000000G"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                NAMESPACE["parse_memory_bytes"](value)
        self.assertEqual(NAMESPACE["parse_memory_bytes"]("0"), 0)
        self.assertEqual(NAMESPACE["parse_memory_bytes"]("0.0000000001M"), 1)

    def test_unsuffixed_request_cannot_bypass_site_memory_limit(self):
        tree = ast.parse((SERVER / "_magnus_config.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "validate_cluster_limits")
        namespace = dict(NAMESPACE, Dict=dict, Any=object, is_local_mode=False)
        namespace["magnus_config"] = {"cluster": {"max_cpu_count": 64, "max_memory_demand": "4G"}}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "limits", "exec"), namespace)
        with self.assertRaisesRegex(ValueError, "memory_demand=.*exceeds"):
            namespace["validate_cluster_limits"]({"cpu_count": 1, "memory_demand": "8192", "gpu_count": 0})
        with self.assertRaisesRegex(ValueError, "gpu_count must not"):
            namespace["validate_cluster_limits"]({"cpu_count": 1, "memory_demand": "1G", "gpu_count": -1})
        namespace["magnus_config"]["cluster"]["max_memory_demand"] = "1.5M"
        with self.assertRaisesRegex(ValueError, "memory_demand=.*exceeds"):
            namespace["validate_cluster_limits"]({"cpu_count": 1, "memory_demand": "1.1M", "gpu_count": 0})


if __name__ == "__main__":
    unittest.main()
