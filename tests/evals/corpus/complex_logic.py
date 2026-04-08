"""Module with complex control flow — tests drafter on non-trivial logic."""
from typing import List, Optional, Tuple
import re


def tokenize_expression(expr: str) -> List[Tuple[str, str]]:
    """Tokenize a simple arithmetic expression into (type, value) pairs.

    Supported tokens: NUMBER, PLUS, MINUS, STAR, SLASH, LPAREN, RPAREN.
    Raises ValueError on unrecognized characters.
    """
    tokens = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        elif ch.isdigit():
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
                j += 1
            tokens.append(("NUMBER", expr[i:j]))
            i = j
            continue
        elif ch == '+':
            tokens.append(("PLUS", ch))
        elif ch == '-':
            tokens.append(("MINUS", ch))
        elif ch == '*':
            tokens.append(("STAR", ch))
        elif ch == '/':
            tokens.append(("SLASH", ch))
        elif ch == '(':
            tokens.append(("LPAREN", ch))
        elif ch == ')':
            tokens.append(("RPAREN", ch))
        else:
            raise ValueError(f"Unexpected character: {ch!r}")
        i += 1
    return tokens


def evaluate(tokens: List[Tuple[str, str]]) -> float:
    """Evaluate tokenized arithmetic expression with correct precedence.

    Supports +, -, *, / and parentheses.
    """
    ...
