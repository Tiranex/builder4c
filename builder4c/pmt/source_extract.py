"""Extraccion de metodos/funciones desde codigo fuente Java o C++.

Reproduce el formato de codigo del dataset Java de referencia:
  - lineas del metodo sin indentacion (strip por linea)
  - desde la linea de la firma hasta la ultima linea de contenido
    (la llave de cierre final NO se incluye)

Tambien implementa el tokenizador "PMT" usado en las columnas
BeforePMT/AfterPMT: tokens separados por ", " con coma final,
p.ej.  c == '\\n'  ->  "c, ==, '\\n',"
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Cadenas/comentarios se enmascaran para que llaves u operadores dentro de
# literales no confundan al analizador. Java y C++ comparten esta sintaxis.
_MASK_CHAR = " "

# palabras clave que descartan al "nombre" pegado a la lista de parametros
_NOT_FUNCTION_NAMES = {"if", "for", "while", "switch", "catch", "return",
                       "sizeof", "do", "else", "new", "delete", "throw",
                       "synchronized", "assert", "case"}
# lo unico admisible entre el ')' final de los parametros y la '{':
# cualificadores C++/Java (const, noexcept, override, throws X, tipo de
# retorno trailing, lista de inicializacion de constructor...)
_HEADER_TAIL_RE = re.compile(
    r"\s*(const|noexcept(\s*\([^)]*\))?|override|final|mutable"
    r"|->\s*[\w:<>&,*\s]+|throws\s+[\w.,\s<>]+|:[^{;]*|\s)*$"
)


def mask_comments_and_strings(text: str, keep_strings: bool = False) -> str:
    """Devuelve una copia de `text` con comentarios (y, salvo `keep_strings`,
    literales de cadena/caracter) sustituidos por espacios; se conservan los
    saltos de linea y por tanto los offsets y numeros de linea."""
    out = list(text)
    i, n = 0, len(text)
    state = "code"  # code | line_comment | block_comment | string | char
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                out[i] = out[i + 1] = _MASK_CHAR
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                out[i] = out[i + 1] = _MASK_CHAR
                i += 2
                continue
            if ch == '"':
                # C++11 raw string: R"delim( ... )delim" (con prefijos u8R, LR...)
                if i > 0 and text[i - 1] == "R" and \
                        (i < 2 or not (text[i - 2].isalnum() or text[i - 2] == "_")
                         or text[i - 3:i - 1] in ("u8",) or text[i - 2] in "uUL"):
                    open_paren = text.find("(", i + 1, i + 18)
                    delim = text[i + 1:open_paren] if open_paren != -1 else None
                    if delim is not None and re.fullmatch(r'[^\s()\\"]*', delim):
                        close = text.find(")" + delim + '"', open_paren + 1)
                        if close != -1:
                            end = close + len(delim) + 2
                            if not keep_strings:
                                for k in range(i + 1, end - 1):
                                    if text[k] != "\n":
                                        out[k] = _MASK_CHAR
                            i = end
                            continue
                state = "string"
                i += 1
                continue
            if ch == "'":
                # C++14 separador de digitos: 1'000'000, 0xFF'FF, 0b1010'0101
                j = i - 1
                while j >= 0 and (text[j].isalnum() or text[j] in "'."):
                    j -= 1
                if j + 1 < i and text[j + 1].isdigit() and nxt.isalnum():
                    i += 1
                    continue
                state = "char"
                i += 1
                continue
            i += 1
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
            else:
                out[i] = _MASK_CHAR
            i += 1
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                out[i] = out[i + 1] = _MASK_CHAR
                state = "code"
                i += 2
                continue
            if ch != "\n":
                out[i] = _MASK_CHAR
            i += 1
        else:  # string o char
            quote = '"' if state == "string" else "'"
            if ch == "\\":
                if not keep_strings:
                    out[i] = _MASK_CHAR
                    if i + 1 < n and text[i + 1] != "\n":
                        out[i + 1] = _MASK_CHAR
                i += 2
                continue
            if ch == quote:
                state = "code"
            elif ch != "\n" and not keep_strings:
                out[i] = _MASK_CHAR
            i += 1
    return "".join(out)


@dataclass
class FunctionBlock:
    name: str
    signature: str        # nombre(tipos) normalizado, p.ej. gcd(int,int)
    header_line: int      # linea (1-based) donde empieza la firma
    open_brace_line: int  # linea de la llave de apertura
    close_brace_line: int # linea de la llave de cierre
    body_start_off: int   # offset del caracter tras la llave de apertura
    body_end_off: int     # offset de la llave de cierre


def _normalize_params(params: str) -> str:
    """'const std::string& s, int n' -> 'const std::string&,int'."""
    params = params.strip()
    if not params or params == "void":
        return ""
    parts = []
    depth = 0
    current = ""
    for ch in params:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    types = []
    for p in parts:
        p = " ".join(p.split())
        # quita el nombre del parametro (ultimo identificador simple) y defaults
        p = p.split("=")[0].strip()
        m = re.match(r"^(.*?)\s*\b([A-Za-z_]\w*)$", p)
        if m and m.group(1).strip():
            p = m.group(1).strip()
        types.append(p.replace(" ", ""))
    return ",".join(types)


def find_functions(text: str) -> List[FunctionBlock]:
    """Localiza bloques de funcion/metodo mediante emparejado de llaves sobre
    el texto enmascarado. Heuristica valida para Java y C++ convencional."""
    masked = mask_comments_and_strings(text)
    line_of = _line_index(text)

    functions: List[FunctionBlock] = []
    stack: List[int] = []
    # offset del ultimo delimitador que cierra una "cabecera" potencial
    last_boundary = 0
    boundary_stack: List[int] = []
    depth_paren = 0

    for i, ch in enumerate(masked):
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
        elif ch == ";" and depth_paren == 0:
            last_boundary = i + 1
        elif ch == "{" and depth_paren == 0:
            header = text[last_boundary:i]
            header_masked = masked[last_boundary:i]
            stack.append(i)
            boundary_stack.append(last_boundary)
            fn = _parse_function_header(header, header_masked)
            if fn is not None:
                name, sig, rel_off = fn
                header_line = line_of[last_boundary + rel_off]
                functions.append(FunctionBlock(
                    name=name,
                    signature=sig,
                    header_line=header_line,
                    open_brace_line=line_of[i],
                    close_brace_line=-1,       # se rellena al cerrar
                    body_start_off=i + 1,
                    body_end_off=-1,
                ))
                functions[-1]._open_off = i    # marca temporal
            last_boundary = i + 1
        elif ch == "}" and depth_paren == 0:
            if stack:
                open_off = stack.pop()
                boundary_stack.pop()
                for f in functions:
                    if getattr(f, "_open_off", None) == open_off:
                        f.close_brace_line = line_of[i]
                        f.body_end_off = i
            last_boundary = i + 1

    return [f for f in functions if f.body_end_off != -1]


def _parse_function_header(header: str, header_masked: str) -> Optional[Tuple[str, str, int]]:
    """Si `header` termina con la firma de una funcion devuelve
    (nombre, firma_normalizada, offset_relativo_del_inicio).

    Se ancla desde el final: el ultimo `)` de la cabecera debe cerrar la
    lista de parametros (solo cualificadores despues) y justo antes del `(`
    correspondiente debe haber un identificador que no sea palabra clave de
    control. Asi la cabecera puede arrastrar restos previos (macros,
    `while (0)` de un do-while, etc.) sin invalidar la deteccion."""
    if "(" not in header_masked:
        return None
    close_idx = header_masked.rfind(")")
    if close_idx == -1:
        return None
    if not _HEADER_TAIL_RE.fullmatch(header_masked[close_idx + 1:]):
        return None
    # empareja el '(' que abre la lista de parametros
    depth = 0
    open_idx = -1
    for i in range(close_idx, -1, -1):
        c = header_masked[i]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                open_idx = i
                break
    if open_idx <= 0:
        return None
    m = re.search(r"([~A-Za-z_][\w:~<>]*)\s*$", header_masked[:open_idx])
    if not m:
        return None
    name = m.group(1).split("::")[-1]
    if name in _NOT_FUNCTION_NAMES:
        return None
    params = header[open_idx + 1:close_idx]
    signature = f"{name}({_normalize_params(params)})"
    # la firma empieza en la linea donde aparece el nombre de la funcion
    # (evita arrastrar includes o codigo previo dentro de la cabecera)
    rel_off = header.rfind("\n", 0, m.start(1)) + 1
    rel_off += len(header[rel_off:]) - len(header[rel_off:].lstrip())
    return name, signature, rel_off


def _line_index(text: str) -> List[int]:
    """Para cada offset de caracter, su numero de linea 1-based."""
    lines = []
    current = 1
    for ch in text:
        lines.append(current)
        if ch == "\n":
            current += 1
    lines.append(current)
    return lines


@dataclass
class ExtractedMethod:
    start_line: int   # linea de la firma (1-based)
    end_line: int     # ultima linea de contenido incluida (sin la llave final)
    lines: List[str]  # lineas sin indentacion


def _content_end_line(text_lines: List[str], func: FunctionBlock) -> int:
    """Ultima linea con contenido antes de la llave de cierre. Si la llave de
    cierre comparte linea con codigo, esa misma linea es contenido."""
    close = func.close_brace_line
    close_text = text_lines[close - 1].strip()
    if close_text and close_text != "}" and not re.fullmatch(r"}\s*;?", close_text):
        return close
    return max(func.header_line, close - 1)


def extract_enclosing_method(text: str, line_no: int) -> Optional[ExtractedMethod]:
    """Extrae el metodo/funcion que contiene `line_no` (1-based)."""
    candidates = [
        f for f in find_functions(text)
        if f.header_line <= line_no <= f.close_brace_line
    ]
    if not candidates:
        return None
    # el bloque mas interno (el de menor tamano) que contiene la linea
    func = min(candidates, key=lambda f: f.close_brace_line - f.header_line)
    return _extract(text, func)


def extract_named_method(text: str, name: str) -> Optional[ExtractedMethod]:
    """Extrae la primera funcion cuyo nombre es exactamente `name`."""
    for func in find_functions(text):
        if func.name == name:
            return _extract(text, func)
    return None


def _extract(text: str, func: FunctionBlock) -> ExtractedMethod:
    text_lines = text.splitlines()
    end = _content_end_line(text_lines, func)
    lines = [l.strip() for l in text_lines[func.header_line - 1:end]]
    return ExtractedMethod(start_line=func.header_line, end_line=end, lines=lines)


# ──────────────────────────── Tokenizador PMT ────────────────────────────

_PMT_TOKEN_RE = re.compile(
    r"""
      '(?:\\.|[^'\\])*'            # literal de caracter
    | "(?:\\.|[^"\\])*"            # literal de cadena
    | \d(?:[\w.]|[eE][+-])*        # numero (int/float/hex/sufijos)
    | [A-Za-z_]\w*                 # identificador / palabra clave
    | <<=|>>=|->\*|\.\.\.|::|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||\+=|-=|\*=|/=|%=|&=|\|=|\^=
    | \S                           # cualquier otro simbolo suelto
    """,
    re.VERBOSE,
)


def pmt_tokens(expr: str) -> str:
    """Tokeniza una expresion al formato de las columnas BeforePMT/AfterPMT."""
    tokens = _PMT_TOKEN_RE.findall(expr)
    if not tokens:
        return ""
    return ", ".join(tokens) + ","
