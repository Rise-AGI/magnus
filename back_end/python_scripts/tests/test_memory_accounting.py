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

    def test_limits_apply_to_each_node_after_per_cpu_normalization(self):
        tree = ast.parse((SERVER / "_magnus_config.py").read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"normalize_per_cpu_resources", "validate_cluster_limits"}]
        namespace = dict(NAMESPACE, Dict=dict, Any=object, is_local_mode=False)
        config = {
            "cluster": {"max_cpu_count": 64, "max_memory_demand": "256G", "max_time_limit": None, "max_node_count": 4, "gpus": []},
            "execution": {"slurm": {"mem_mode": "explicit", "mem_per_cpu_mb": 4096}},
        }
        namespace["magnus_config"] = config
        exec(compile(ast.Module(body=functions, type_ignores=[]), "limits", "exec"), namespace)
        validate = namespace["validate_cluster_limits"]
        request = {"cpu_count": 1, "memory_demand": "256G", "node_count": 2, "tasks_per_node": 64}
        validate(request)
        with self.assertRaisesRegex(ValueError, "CPU request per node"):
            validate(dict(request, cpu_count=2))
        validate(dict(request, node_count=None, tasks_per_node=None, cpu_count=64))

        config["execution"]["slurm"]["mem_mode"] = "per_cpu"
        request["memory_demand"] = "4G"
        namespace["normalize_per_cpu_resources"](request)
        validate(request)
        config["cluster"]["max_memory_demand"] = "128G"
        with self.assertRaisesRegex(ValueError, "Memory request per node"):
            validate(request)
        config["cluster"]["max_memory_demand"] = "512G"
        request["memory_demand"] = "5G"
        namespace["normalize_per_cpu_resources"](request)
        self.assertEqual(request["cpu_count"], 2)
        with self.assertRaisesRegex(ValueError, "CPU request per node"):
            validate(request)

        namespace["is_local_mode"] = True
        validate(request)


if __name__ == "__main__":
    unittest.main()
