# back_end/python_scripts/tests/test_services/test_mma_mcp.py
import random
import asyncio
from magnus import call_service_async
from pywheels import run_tasks_concurrently_async


cases = [
    "2 + 2",
    "Integrate[x^2 * Sin[x], x]",
    "Simplify[Sin[x]^2 + Cos[x]^2]",
    "Solve[x^2 - 5x + 6 == 0, x]",
    "Prime[100]",
    "Det[{{1, 2}, {3, 4}}]",
    "N[Pi, 50]",
    "D[Exp[x] * Sin[x], x]",
]


async def execute_mathematica(
    code: str,
)-> str:

    result = await call_service_async(
        service_id = "mma-mcp",
        payload = {
            "code": code
        },
        timeout = 300.0,
    )
    
    if isinstance(result, dict) and "text" in result:
        return str(result["text"])
    return str(result)


async def main():
    
    N = min(5, len(cases))
    sampled_cases = random.sample(cases, N)
    task_inputs = [(code, ) for code in sampled_cases]
    
    print(f"🚀 Starting {N} concurrent tasks via Magnus SDK...")

    results = await run_tasks_concurrently_async(
        task=execute_mathematica,
        task_indexers=list(range(N)),
        task_inputs=task_inputs,
    )
    
    print("\n" + "=" * 50)
    
    for idx, result in results.items():
        original_code = task_inputs[idx][0]
        
        if isinstance(result, Exception):
            print(f"Task {idx} (Input: {original_code}) Failed:\n❌ {result}")
        else:
            print(f"Task {idx} (Input: {original_code}):\n✅ {result.strip()}")
            
        print("-" * 50)


if __name__ == "__main__":
    
    asyncio.run(main())