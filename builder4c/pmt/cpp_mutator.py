"""Generacion de mutantes para C++ imitando los operadores de Major.

Operadores implementados (mismos nombres que en mutants.log de Major):

  ROR  operadores relacionales:  == != < <= > >=  (incluye variantes
       TRUE/FALSE que sustituyen la comparacion completa)
  AOR  operadores aritmeticos binarios:  + - * / %
  COR  conectores logicos && y ||  (variantes LHS, RHS, TRUE/FALSE y
       cambio de operador con el operando derecho entre parentesis,
       igual que Major: a || b -> a != (b))
  LVR  literales: true<->false, 0->1, positivo->0
  STD  borrado de sentencias completas de una linea: llamadas (<CALL>)
       y asignaciones (<ASSIGN>) -> <NO-OP>

Los mutantes se generan de forma puramente textual dentro de cuerpos de
funcion; el runner los filtra despues compilando cada uno (los que no
compilan se descartan, aproximando el que Major solo produzca mutantes
tipados validos).
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from .source_extract import FunctionBlock, find_functions, mask_comments_and_strings

# precedencia no necesaria: los limites de operando se definen por conjuntos
# de tokens de parada especificos de cada operador (ver _operand_span)

ROR_REPLACEMENTS = {
    "==": ["<=", ">=", "FALSE"],
    "!=": ["<", ">", "TRUE"],
    "<":  ["!=", "<=", "FALSE"],
    "<=": ["<", "==", "TRUE"],
    ">":  ["!=", ">=", "FALSE"],
    ">=": [">", "==", "TRUE"],
}
AOR_OPS = ["+", "-", "*", "/", "%"]
COR_REPLACEMENTS = {
    "&&": ["==", "LHS", "RHS", "FALSE"],
    "||": ["!=", "LHS", "RHS", "TRUE"],
}

RELATIONAL = set(ROR_REPLACEMENTS)
LOGICAL = set(COR_REPLACEMENTS)
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
_BOUNDARY = {";", ",", "{", "}", "?", ":", "return", "case", "throw"}

_TOKEN_RE = re.compile(
    r"""
      '(?:\\.|[^'\\])*'
    | "(?:\\.|[^"\\])*"
    | \d(?:[\w.]|[eE][+-])*
    | [A-Za-z_]\w*
    | <<=|>>=|->\*|\.\.\.|::|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||\+=|-=|\*=|/=|%=|&=|\|=|\^=
    | \S
    """,
    re.VERBOSE,
)

_STD_CALL_RE = re.compile(
    r"^([A-Za-z_]\w*(?:(?:\.|->|::)[A-Za-z_~]\w*)*)\s*\(.*\)\s*;$")
_STD_ASSIGN_RE = re.compile(
    r"^([A-Za-z_]\w*(?:(?:\.|->|::)[A-Za-z_]\w*|\[[^\]]+\])*)\s*"
    r"(=|\+=|-=|\*=|/=|%=|&=|\|=|\^=)\s*[^=].*;$")
_STD_FORBIDDEN = re.compile(r"\b(return|if|else|for|while|do|switch|case|break|"
                            r"continue|throw|goto|new|delete)\b")


@dataclass(frozen=True)
class CppMutant:
    operator: str      # ROR/AOR/COR/LVR/STD
    from_desc: str
    to_desc: str
    class_name: str
    method_sig: str
    line: int          # 1-based en el fichero
    before: str
    after: str
    start_off: int     # span de reemplazo (offsets absolutos en el fichero)
    end_off: int
    replacement: str


def apply_mutant(text: str, mutant: CppMutant) -> str:
    return text[:mutant.start_off] + mutant.replacement + text[mutant.end_off:]


@dataclass
class _Tok:
    text: str
    start: int  # offset absoluto en el fichero
    end: int


def _tokenize_span(code_text: str, op_mask: str, start: int, end: int) -> List[_Tok]:
    """Tokens del rango [start,end). `code_text` conserva literales,
    `op_mask` tiene ademas los literales enmascarados (para saber si un token
    es realmente codigo y no parte de una cadena)."""
    tokens = []
    for m in _TOKEN_RE.finditer(code_text, start, end):
        tokens.append(_Tok(m.group(0), m.start(), m.end()))
    return tokens


def _is_code(tok: _Tok, op_mask: str) -> bool:
    """True si el token existe igual en la mascara sin literales (no esta
    dentro de una cadena ni comentario)."""
    return op_mask[tok.start:tok.end] == tok.text


def _operand_span(tokens: List[_Tok], op_idx: int, stop_ops: Set[str]
                  ) -> Optional[Tuple[int, int]]:
    """Indices (incluidos) de los tokens que forman los operandos izquierdo y
    derecho del operador binario en `op_idx`. Delimita por tokens de parada a
    profundidad 0 de parentesis/corchetes."""
    # izquierda
    depth = 0
    left = op_idx
    i = op_idx - 1
    while i >= 0:
        t = tokens[i].text
        if t in (")", "]"):
            depth += 1
        elif t in ("(", "["):
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (t in stop_ops or t in _BOUNDARY or t in ASSIGN_OPS):
            break
        left = i
        i -= 1
    # derecha
    depth = 0
    right = op_idx
    i = op_idx + 1
    while i < len(tokens):
        t = tokens[i].text
        if t in ("(", "["):
            depth += 1
        elif t in (")", "]"):
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (t in stop_ops or t in _BOUNDARY or t in ASSIGN_OPS):
            break
        right = i
        i += 1
    if left == op_idx or right == op_idx:
        return None
    return left, right


def _span_text(text: str, tokens: List[_Tok], first: int, last: int) -> str:
    return " ".join(text[tokens[first].start:tokens[last].end].split())


def _is_unary_context(tokens: List[_Tok], idx: int) -> bool:
    """True si el token en idx (+,-,*,&) actua como operador unario."""
    if idx == 0:
        return True
    prev = tokens[idx - 1].text
    return (prev in _BOUNDARY or prev in ASSIGN_OPS or prev in RELATIONAL
            or prev in LOGICAL or prev in ("(", "[", "!", "~")
            or prev in AOR_OPS or prev in ("&&", "||", "<<", ">>"))


def generate_file_mutants(text: str, class_name: str) -> List[CppMutant]:
    """Genera todos los mutantes candidatos de un fichero fuente C++."""
    op_mask = mask_comments_and_strings(text)                 # sin literales
    code_mask = mask_comments_and_strings(text, keep_strings=True)
    lines = text.splitlines(keepends=True)
    line_offsets = []
    off = 0
    for l in lines:
        line_offsets.append(off)
        off += len(l)

    functions = find_functions(text)
    mutants: List[CppMutant] = []
    seen: Set[Tuple] = set()

    def add(m: CppMutant) -> None:
        key = (m.operator, m.to_desc, m.start_off, m.end_off, m.replacement)
        if key not in seen:
            seen.add(key)
            mutants.append(m)

    for func in functions:
        for line_no in range(func.open_brace_line, func.close_brace_line + 1):
            if line_no - 1 >= len(lines):
                break
            l_start = line_offsets[line_no - 1]
            l_end = l_start + len(lines[line_no - 1])
            tokens = [t for t in _tokenize_span(code_mask, op_mask, l_start, l_end)
                      if t.text.strip()]
            code_tokens = [t for t in tokens if _is_code(t, op_mask)
                           or t.text[0] in "'\""]
            _gen_binary_ops(text, op_mask, code_tokens, func, class_name,
                            line_no, add)
            _gen_lvr(text, op_mask, code_tokens, func, class_name, line_no, add)
            _gen_std(text, op_mask, code_tokens, func, class_name, line_no,
                     l_start, l_end, add)
    return mutants


def _gen_binary_ops(text, op_mask, tokens, func, class_name, line_no, add):
    for idx, tok in enumerate(tokens):
        op = tok.text
        if op_mask[tok.start:tok.end] != op:
            continue  # dentro de literal
        if op in RELATIONAL:
            stop = RELATIONAL | LOGICAL | {"!"}
            table = ROR_REPLACEMENTS[op]
            kind = "ROR"
        elif op in AOR_OPS:
            if _is_unary_context(tokens, idx):
                continue
            stop = RELATIONAL | LOGICAL | {"!", "<<", ">>"}
            table = [o for o in AOR_OPS if o != op]
            kind = "AOR"
        elif op in LOGICAL:
            stop = {"&&", "||"}
            table = COR_REPLACEMENTS[op]
            kind = "COR"
        else:
            continue
        span = _operand_span(tokens, idx, stop)
        if span is None:
            continue
        left, right = span
        before = _span_text(text, tokens, left, right)
        expr_start, expr_end = tokens[left].start, tokens[right].end
        lhs = _span_text(text, tokens, left, idx - 1)
        rhs = _span_text(text, tokens, idx + 1, right)

        for repl in table:
            if repl == "TRUE":
                add(CppMutant(kind, op, "TRUE", class_name, func.signature,
                              line_no, before, "true", expr_start, expr_end,
                              "true"))
            elif repl == "FALSE":
                add(CppMutant(kind, op, "FALSE", class_name, func.signature,
                              line_no, before, "false", expr_start, expr_end,
                              "false"))
            elif repl == "LHS":
                add(CppMutant(kind, op, "LHS", class_name, func.signature,
                              line_no, before, lhs, expr_start, expr_end, lhs))
            elif repl == "RHS":
                add(CppMutant(kind, op, "RHS", class_name, func.signature,
                              line_no, before, rhs, expr_start, expr_end, rhs))
            elif kind == "COR":
                # cambio de conector: a || b -> a != (b), como Major
                after = f"{lhs} {repl} ({rhs})"
                add(CppMutant(kind, op, repl, class_name, func.signature,
                              line_no, before, after, expr_start, expr_end,
                              after))
            else:
                after = f"{lhs} {repl} {rhs}"
                add(CppMutant(kind, op, repl, class_name, func.signature,
                              line_no, before, after, tok.start, tok.end, repl))


def _gen_lvr(text, op_mask, tokens, func, class_name, line_no, add):
    for idx, tok in enumerate(tokens):
        t = tok.text
        if op_mask[tok.start:tok.end] != t:
            continue
        if t == "true":
            add(CppMutant("LVR", "TRUE", "FALSE", class_name, func.signature,
                          line_no, "true", "false", tok.start, tok.end, "false"))
        elif t == "false":
            add(CppMutant("LVR", "FALSE", "TRUE", class_name, func.signature,
                          line_no, "false", "true", tok.start, tok.end, "true"))
        elif re.fullmatch(r"\d[\w.]*", t):
            # evita mutar dimensiones de arrays o etiquetas case
            prev = tokens[idx - 1].text if idx > 0 else ""
            if prev in ("case", "["):
                continue
            value = t.rstrip("uUlLfF")
            try:
                num = float(value)
            except ValueError:
                continue
            if num == 0:
                add(CppMutant("LVR", "0", "1", class_name, func.signature,
                              line_no, t, "1", tok.start, tok.end, "1"))
            else:
                add(CppMutant("LVR", "POS", "0", class_name, func.signature,
                              line_no, t, "0", tok.start, tok.end, "0"))


def _gen_std(text, op_mask, tokens, func, class_name, line_no, l_start, l_end, add):
    stripped_masked = op_mask[l_start:l_end].strip()
    if not stripped_masked or _STD_FORBIDDEN.search(stripped_masked):
        return
    kind = None
    if _STD_CALL_RE.match(stripped_masked):
        kind = "<CALL>"
    elif _STD_ASSIGN_RE.match(stripped_masked):
        kind = "<ASSIGN>"
    if kind is None:
        return
    code_tokens = [t for t in tokens]
    if not code_tokens or code_tokens[-1].text != ";":
        return
    stmt_start = code_tokens[0].start
    stmt_end = code_tokens[-1].start  # conserva el ';'
    before = " ".join(text[stmt_start:stmt_end].split())
    add(CppMutant("STD", kind, "<NO-OP>", class_name, func.signature,
                  line_no, before, "<NO-OP>", stmt_start, stmt_end, ""))
