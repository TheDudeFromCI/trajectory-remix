"""Numerical expressions evaluated by simpleeval with an explicit math allowlist."""

import ast
import math
from simpleeval import SimpleEval
from ._simulation import result, failure

name = "calculator"
description = "Calculate a numeric expression. Supports +, -, *, /, //, %, **, pi, e, tau, and sin/cos/tan, asin/acos/atan/atan2, sqrt, log/log10/log2, exp, degrees/radians, abs, round, min, max, floor, ceil. Angles are radians."
schema = {
    "type": "object",
    "properties": {"expression": {"type": "string", "minLength": 1, "maxLength": 4096}},
    "required": ["expression"],
    "additionalProperties": False,
}
FUNCTIONS = {
    key: getattr(math, key)
    for key in (
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "sqrt",
        "log",
        "log10",
        "log2",
        "exp",
        "degrees",
        "radians",
        "floor",
        "ceil",
    )
}
FUNCTIONS.update(abs=abs, round=round, min=min, max=max)


def run(args, state, windows):
    try:
        tree = ast.parse(args["expression"], mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 256:
            raise ValueError("Expression exceeds 256 syntax nodes")
        # simpleeval provides the evaluator and bounded arithmetic. Only numeric
        # literals and ordinary function calls belong to this calculator's grammar.
        allowed = (
            ast.Expression,
            ast.Constant,
            ast.BinOp,
            ast.UnaryOp,
            ast.Call,
            ast.Name,
            ast.Load,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.UAdd,
            ast.USub,
        )
        for node in ast.walk(tree):
            if (
                not isinstance(node, allowed)
                or isinstance(node, ast.Constant)
                and type(node.value) not in (int, float)
            ):
                raise ValueError(
                    "Only numeric arithmetic and listed math functions are supported"
                )
        value = SimpleEval(
            functions=FUNCTIONS,
            names={"pi": math.pi, "e": math.e, "tau": math.tau},
            allowed_attrs={},
        ).eval(args["expression"])
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Calculation must produce a finite real number")
        if isinstance(value, int) and value.bit_length() > 16384:
            raise ValueError("Integer result exceeds 16384 bits")
        return result({"value": value})
    except Exception as error:
        return failure(error)
