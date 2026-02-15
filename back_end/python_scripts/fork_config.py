# back_end/python_scripts/fork_config.py
#
# Fork a YAML config with optional key overrides (dot-notation, type-inferred).
#
# Usage:
#   python fork_config.py --source src.yaml --target dst.yaml [key.path=value ...]
#
# Examples:
#   python fork_config.py --source configs/magnus_config.yaml --target configs/magnus_config_develop.yaml \
#       server.port=8002 server.develop_mode=true
#
#   python fork_config.py --source configs/magnus_config.yaml --target /tmp/test_config.yaml \
#       server.host=0.0.0.0

import argparse
import sys
from typing import Any, Dict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from library.fundamental.yaml_tools import load_from_yaml
from library.fundamental.externals import ruamel_yaml


def parse_value(
    raw_value: str,
    target_type: type,
) -> Any:
    if target_type is bool:
        if raw_value.lower() in ("true", "1", "yes", "on"):
            return True
        if raw_value.lower() in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"Cannot parse '{raw_value}' as boolean")

    if target_type is type(None):
        raise TypeError("Cannot infer target type from a null value in source.")

    return target_type(raw_value)


def set_nested_value(
    config: Dict[str, Any],
    path: str,
    raw_value: str,
) -> None:
    keys = path.split(".")
    cursor = config

    for key in keys[:-1]:
        if key not in cursor:
            raise KeyError(f"Path node '{key}' not found in configuration.")

        cursor = cursor[key]

        if not isinstance(cursor, dict):
            raise TypeError(f"Key '{key}' is not a dictionary container.")

    final_key = keys[-1]
    if final_key not in cursor:
        raise KeyError(f"Target key '{final_key}' not found in configuration.")

    current_value = cursor[final_key]
    target_type = type(current_value)

    cursor[final_key] = parse_value(raw_value, target_type)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=str,
        required=True,
    )
    parser.add_argument(
        "--target",
        type=str,
        required=True,
    )
    parser.add_argument(
        "overrides",
        nargs="*",
    )
    args = parser.parse_args()

    if not args.source.endswith(".yaml") or not args.target.endswith(".yaml"):
        raise NotImplementedError("Only .yaml files are supported currently.")

    config = load_from_yaml(args.source)

    for item in args.overrides:
        if "=" not in item:
            raise ValueError(f"Invalid override format: {item}. Use key=value")

        path, raw_value = item.split("=", 1)
        set_nested_value(config, path, raw_value)

    yaml_handler = ruamel_yaml(typ="safe")
    yaml_handler.default_flow_style = False
    with open(args.target, "w", encoding="utf-8") as file:
        yaml_handler.dump(config, file)


if __name__ == "__main__":
    main()
