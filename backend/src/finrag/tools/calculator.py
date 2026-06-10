"""calculator(expression) — arithmetic on figures the model pulled from context.

Why this exists: LLMs are unreliable at multi-digit arithmetic (growth rates,
margins, sums across years). Far better to have the model *extract* the numbers
and delegate the math to real code.

Why not eval(): `eval("__import__('os').system('...')")` is remote code
execution. A figure could even arrive via a prompt-injected chunk. So we parse
to an AST and walk it, permitting ONLY numeric literals and arithmetic
operators — every other node type (names, calls, attributes, subscripts)
raises. This is an allowlist, not a blocklist: anything we didn't explicitly
permit is rejected by default.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

# Allowlisted operators → their implementing functions. Anything not here
# (e.g. bitwise, matmul) is rejected.
_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Guardrail: cap exponent magnitude so `10 ** 10**9` can't pin a CPU / OOM.
_MAX_EXPONENT = 1000


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"Only numeric literals allowed, got {node.value!r}")
        return float(node.value)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Operator {type(node.op).__name__} not allowed")
        return op(_eval_node(node.operand))
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Operator {type(node.op).__name__} not allowed")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ValueError(f"Exponent {right} exceeds limit {_MAX_EXPONENT}")
        return op(left, right)
    # Any other node — Name, Call, Attribute, Subscript, etc. — is rejected.
    raise ValueError(f"Expression element {type(node).__name__} not allowed")


def calculator(expression: str) -> dict[str, Any]:
    """Evaluate an arithmetic `expression` and return the numeric result.

    Supports + - * / // % ** and parentheses over numeric literals only.
    Returns {"result": <float>} on success or {"error": <message>} on failure
    — tools return errors as data (not exceptions) so the agent can read the
    message and retry rather than crashing the graph.
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree)
        return {"result": result}
    except ZeroDivisionError:
        return {"error": "division by zero"}
    except (ValueError, SyntaxError) as e:
        return {"error": str(e)}


if __name__ == "__main__":
    # Sanity: valid arithmetic, plus rejection of an injection attempt.
    for expr in [
        "(383285 - 394328) / 394328 * 100",   # YoY % change
        "200583 + 85200",                       # iPhone + Services
        "2 ** 4000",                            # exponent guard
        "__import__('os').system('echo pwned')",  # must be rejected
    ]:
        print(f"{expr!r:50s} -> {calculator(expr)}")
