# back_end/python_scripts/execute_mathematica.py
import os
import magnus
import argparse
from magnus import call_service


def execute_mathematica(
    code: str,
    timeout: float,
)-> str:
    
    return call_service(
        service_id = "mma-mcp",
        payload = {"code": code},
        protocol = "mcp",
        tool_name = "execute_mathematica",
        timeout = timeout,
    )


def main():
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", type=str, required=True)
    parser.add_argument("--code", type=str, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    magnus.configure(address=args.address)
    
    result = execute_mathematica(args.code, args.timeout)

    result_path = os.environ.get("MAGNUS_RESULT")
    assert result_path is not None, "Environment variable MAGNUS_RESULT is not set."
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(result)


if __name__ == "__main__":
    
    main()