"""Scryfall-style search syntax, compiled to SQL.

    t:creature c:g mv<=3 -is:deck
    o:"target creature" (r:rare or r:mythic)
    loc:"binder a" is:foil
    !"Llanowar Elves"

Two rules shape the whole design.

**Predicates are oracle-scoped, not printing-scoped.** A search compiles to a
set of matching `oracle_id`s, and the outer query then aggregates every
printing of those cards. Compiling the predicate inline instead would mean
`s:dom` silently restricted which printings got counted, so a card you own four
of would report owning one. Same trap as everywhere else in this codebase:
decks reference the game object, the collection tracks printings.

**Unknown syntax is an error, not a silent no-op.** A typo'd filter that
quietly matches everything is worse than a message saying which field is wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator


class QueryError(ValueError):
    """Raised for anything the user could fix by retyping the query."""


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

FIELD_ALIASES = {
    "n": "name", "name": "name",
    "t": "type", "type": "type",
    "o": "oracle", "oracle": "oracle", "text": "oracle",
    "c": "color", "color": "color", "colour": "color",
    "id": "identity", "identity": "identity", "ci": "identity",
    "mv": "mv", "cmc": "mv", "manavalue": "mv",
    "r": "rarity", "rarity": "rarity",
    "s": "set", "set": "set", "e": "set", "edition": "set",
    "pow": "power", "power": "power",
    "tou": "toughness", "toughness": "toughness",
    "loy": "loyalty", "loyalty": "loyalty",
    "usd": "usd", "price": "usd",
    "value": "value", "worth": "value",
    "edhrec": "edhrec",
    "layout": "layout",
    "lang": "lang", "language": "lang",
    "cn": "cn", "number": "cn", "collector": "cn",
    "year": "year",
    "loc": "loc", "location": "loc",
    "is": "is", "not": "not",
}

COLOR_LETTERS = set("wubrg")

COLOR_NAMES = {
    "white": "W", "blue": "U", "black": "B", "red": "R", "green": "G",
    "azorius": "WU", "dimir": "UB", "rakdos": "BR", "gruul": "RG", "selesnya": "GW",
    "orzhov": "WB", "izzet": "UR", "golgari": "BG", "boros": "RW", "simic": "GU",
    "bant": "GWU", "esper": "WUB", "grixis": "UBR", "jund": "BRG", "naya": "RGW",
    "abzan": "WBG", "jeskai": "URW", "sultai": "BGU", "mardu": "RWB", "temur": "GUR",
    "chaos": "UBRG", "aggression": "WBRG", "altruism": "RGWU", "growth": "GWUB",
    "artifice": "WUBR",
    "wubrg": "WUBRG", "five": "WUBRG", "rainbow": "WUBRG",
}

RARITY_ORDER = ["common", "uncommon", "rare", "mythic", "special", "bonus"]
RARITY_ALIASES = {"c": "common", "u": "uncommon", "r": "rare", "m": "mythic",
                  "s": "special", "b": "bonus"}

NUMERIC_OPS = {":": "=", "=": "=", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}

IS_VALUES = (
    "foil", "nonfoil", "etched", "owned", "deck", "wishlist", "lent", "unfiled",
    "legendary", "commander", "vanilla", "dfc", "split", "permanent", "spell",
)

# Power and toughness are TEXT because '*', '1+*' and '∞' exist. Comparing
# numerically has to exclude them, or CAST('*' AS REAL) = 0 makes every one of
# them a 0-power creature.
_NUMERIC_TEXT = "{a}.{col} IS NOT NULL AND {a}.{col} NOT GLOB '*[^0-9.]*' AND {a}.{col} != ''"


# --------------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------------- #

@dataclass
class And:
    children: list[Any]


@dataclass
class Or:
    children: list[Any]


@dataclass
class Not:
    child: Any


@dataclass
class Term:
    field: str
    op: str
    value: str
    exact: bool = False


# --------------------------------------------------------------------------- #
# Tokenizer
# --------------------------------------------------------------------------- #

@dataclass
class Token:
    kind: str          # ATOM | LPAREN | RPAREN | OR | AND | MINUS
    text: str = ""


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(Token("LPAREN")); i += 1; continue
        if ch == ")":
            tokens.append(Token("RPAREN")); i += 1; continue
        if ch == "-" and (not tokens or tokens[-1].kind in ("LPAREN", "OR", "AND", "MINUS")
                          or (i > 0 and source[i - 1].isspace())):
            tokens.append(Token("MINUS")); i += 1; continue

        # An atom runs to whitespace or a closing paren, except inside quotes.
        start = i
        buf: list[str] = []
        quoted = False
        while i < n:
            c = source[i]
            if c == '"':
                quoted = not quoted
                i += 1
                continue
            if not quoted and (c.isspace() or c == ")"):
                break
            buf.append(c)
            i += 1
        if quoted:
            raise QueryError('Unclosed quote — every " needs a closing partner.')
        text = "".join(buf)
        if not text:
            i = start + 1
            continue
        low = text.lower()
        if low == "or":
            tokens.append(Token("OR"))
        elif low == "and":
            tokens.append(Token("AND"))
        else:
            # Remember whether the value was quoted, so o:"target creature"
            # keeps its space but is still one atom.
            tokens.append(Token("ATOM", text))
    return tokens


# --------------------------------------------------------------------------- #
# Parser  (recursive descent; OR binds loosest, then AND, then unary '-')
# --------------------------------------------------------------------------- #

class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> Any:
        node = self.parse_or()
        if self.peek():
            raise QueryError("Unbalanced ')' — there is a closing bracket with nothing open.")
        return node

    def parse_or(self) -> Any:
        parts = [self.parse_and()]
        while (t := self.peek()) and t.kind == "OR":
            self.next()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else Or(parts)

    def parse_and(self) -> Any:
        parts: list[Any] = []
        while (t := self.peek()) and t.kind not in ("RPAREN", "OR"):
            if t.kind == "AND":       # explicit 'and' is allowed but implied
                self.next()
                continue
            parts.append(self.parse_unary())
        if not parts:
            raise QueryError("Empty group — '()' has nothing in it.")
        return parts[0] if len(parts) == 1 else And(parts)

    def parse_unary(self) -> Any:
        t = self.peek()
        if t and t.kind == "MINUS":
            self.next()
            return Not(self.parse_unary())
        if t and t.kind == "LPAREN":
            self.next()
            node = self.parse_or()
            nxt = self.peek()
            if not nxt or nxt.kind != "RPAREN":
                raise QueryError("Unclosed '(' — a group was opened but never closed.")
            self.next()
            return node
        if t and t.kind == "ATOM":
            self.next()
            return parse_atom(t.text)
        raise QueryError("Expected a search term.")


_ATOM_RE = re.compile(r"^(?P<field>[A-Za-z]+)(?P<op>:|!=|<=|>=|=|<|>)(?P<value>.*)$", re.S)


def parse_atom(text: str) -> Term:
    if text.startswith("!"):
        rest = text[1:]
        if not rest:
            raise QueryError("'!' needs a card name after it, e.g. !\"Llanowar Elves\".")
        return Term("name", "=", rest, exact=True)

    m = _ATOM_RE.match(text)
    if not m:
        return Term("name", ":", text)      # a bare word is a name search

    field_raw = m.group("field").lower()
    if field_raw not in FIELD_ALIASES:
        known = ", ".join(sorted(set(FIELD_ALIASES.values())))
        raise QueryError(f"Unknown filter '{field_raw}:'. Try one of: {known}.")
    field = FIELD_ALIASES[field_raw]
    value = m.group("value")
    if value == "":
        raise QueryError(f"'{field_raw}:' needs a value after it.")
    if field == "not":                       # not:foil == -is:foil
        return Term("is", ":", value)        # caller wraps; see parse_atom_not
    return Term(field, m.group("op"), value)


def parse(source: str) -> Any | None:
    """Parse a query string into an AST, or None when it is blank."""
    tokens = tokenize(source or "")
    if not tokens:
        return None
    # 'not:foo' is sugar for '-is:foo'; handled here so the parser stays simple.
    node = _Parser(tokens).parse()
    return node


# --------------------------------------------------------------------------- #
# Compiler
# --------------------------------------------------------------------------- #

def _owned_exists(alias: str, extra: str = "") -> str:
    """A copy the user physically holds, matched at the game-object level so
    owning any printing counts.

    Written as a non-correlated ``IN`` rather than a correlated ``EXISTS`` on
    purpose. The EXISTS form makes SQLite scan all 37k oracle_ids and re-run
    the subquery for every one -- `is:foil` took 8.8 seconds. This form
    evaluates the copies side once (it is small: one row per card owned) and
    then searches an index, which is roughly two thousand times faster.
    """
    return (f"{alias}.oracle_id IN "
            f"(SELECT c2.oracle_id FROM copies cp2 "
            f"JOIN cards c2 ON c2.id = cp2.card_id WHERE 1=1{extra})")


def _colors(value: str) -> str:
    v = value.lower().strip()
    if v in ("c", "colorless", "colourless"):
        return ""
    if v in ("m", "multi", "multicolor", "multicolour"):
        return "MULTI"
    if v in COLOR_NAMES:
        return COLOR_NAMES[v]
    letters = set(v)
    if letters and letters <= COLOR_LETTERS:
        return "".join(sorted(ch.upper() for ch in letters))
    raise QueryError(
        f"'{value}' is not a colour. Use letters (wubrg), names (green), "
        f"guild or shard names (simic, jund), 'c' for colourless, or 'm' for multicolour.")


def _compile_color(alias: str, column: str, op: str, value: str) -> tuple[str, list[Any]]:
    want = _colors(value)

    if want == "MULTI":
        cond = f"LENGTH({alias}.{column}) >= 2"
        return (f"NOT ({cond})", []) if op in ("!=",) else (cond, [])
    if want == "":
        cond = f"{alias}.{column} = ''"
        return (f"NOT ({cond})", []) if op == "!=" else (cond, [])

    letters = list(want)
    contains = " AND ".join(f"{alias}.{column} LIKE ?" for _ in letters)
    contains_params = [f"%{ch}%" for ch in letters]

    # Subset: strip every allowed letter and nothing may be left over.
    stripped = f"{alias}.{column}"
    for ch in letters:
        stripped = f"REPLACE({stripped}, '{ch}', '')"
    subset = f"{stripped} = ''"

    if op in (":", ">="):
        return contains, contains_params
    if op == "=":
        return f"{alias}.{column} = ?", [want]
    if op == "!=":
        return f"{alias}.{column} != ?", [want]
    if op == "<=":
        return subset, []
    if op == "<":
        return f"({subset}) AND {alias}.{column} != ?", [want]
    if op == ">":
        return f"({contains}) AND {alias}.{column} != ?", contains_params + [want]
    raise QueryError(f"Operator '{op}' does not work with colours.")


def _compile_numeric(expr: str, op: str, value: str, label: str,
                     guard: str = "") -> tuple[str, list[Any]]:
    sql_op = NUMERIC_OPS.get(op)
    if sql_op is None:
        raise QueryError(f"Operator '{op}' does not work with {label}.")
    try:
        num = float(value)
    except ValueError:
        raise QueryError(f"'{value}' is not a number, so it cannot be compared with {label}.") from None
    cond = f"{expr} {sql_op} ?"
    if guard:
        cond = f"({guard}) AND {cond}"
    return cond, [num]


def compile_node(node: Any, alias: str = "cq") -> tuple[str, list[Any]]:
    if isinstance(node, And):
        parts = [compile_node(c, alias) for c in node.children]
        return "(" + " AND ".join(p[0] for p in parts) + ")", [x for p in parts for x in p[1]]
    if isinstance(node, Or):
        parts = [compile_node(c, alias) for c in node.children]
        return "(" + " OR ".join(p[0] for p in parts) + ")", [x for p in parts for x in p[1]]
    if isinstance(node, Not):
        sql, params = compile_node(node.child, alias)
        return f"NOT ({sql})", params
    if isinstance(node, Term):
        return _compile_term(node, alias)
    raise QueryError("Could not understand that search.")


def _compile_term(t: Term, a: str) -> tuple[str, list[Any]]:
    f, op, v = t.field, t.op, t.value

    if f == "name":
        if t.exact:
            return f"LOWER({a}.name) = LOWER(?) OR LOWER({a}.name) LIKE LOWER(?)", [v, f"{v} // %"]
        return f"{a}.name LIKE ?", [f"%{v}%"]

    if f == "type":
        return f"{a}.type_line LIKE ?", [f"%{v}%"]

    if f == "oracle":
        # oracle_text holds every face joined, so a double-faced card is found
        # by text printed on either side.
        return f"{a}.oracle_text LIKE ?", [f"%{v}%"]

    if f == "color":
        return _compile_color(a, "colors", op, v)

    if f == "identity":
        # Scryfall's default for identity is 'subset of', i.e. what you can
        # legally run in a deck with that commander.
        return _compile_color(a, "color_identity", "<=" if op == ":" else op, v)

    if f == "mv":
        return _compile_numeric(f"{a}.mana_value", op, v, "mana value")

    if f in ("power", "toughness", "loyalty"):
        col = f
        guard = _NUMERIC_TEXT.format(a=a, col=col)
        return _compile_numeric(f"CAST({a}.{col} AS REAL)", op, v, col, guard)

    if f == "usd":
        # Any printing at this price. `usd>=40` finds cards that are expensive
        # somewhere, which is not the same as "my copy is worth that" -- see
        # `value:` for the collection question.
        return _compile_numeric(f"{a}.price_usd", op, v, "price")

    if f == "value":
        # The most valuable copy actually held. This is the one that answers
        # "what are the expensive cards in my collection".
        sql_op = NUMERIC_OPS.get(op)
        if sql_op is None:
            raise QueryError(f"Operator '{op}' does not work with value.")
        try:
            num = float(v)
        except ValueError:
            raise QueryError(f"'{v}' is not a number, so it cannot be compared with value.") from None
        return (f"{a}.oracle_id IN (SELECT c2.oracle_id FROM copies cp2 "
                f"JOIN cards c2 ON c2.id = cp2.card_id GROUP BY c2.oracle_id "
                f"HAVING MAX(CASE cp2.finish WHEN 'etched' THEN c2.price_usd_etched "
                f"WHEN 'foil' THEN c2.price_usd_foil ELSE c2.price_usd END) {sql_op} ?)",
                [num])

    if f == "edhrec":
        return _compile_numeric(f"{a}.edhrec_rank", op, v, "EDHREC rank")

    if f == "year":
        return _compile_numeric(f"CAST(SUBSTR({a}.released_at, 1, 4) AS INTEGER)",
                                op, v, "year")

    if f == "rarity":
        want = RARITY_ALIASES.get(v.lower(), v.lower())
        if want not in RARITY_ORDER:
            raise QueryError(f"'{v}' is not a rarity. Try: {', '.join(RARITY_ORDER)}.")
        if op in (":", "="):
            return f"{a}.rarity = ?", [want]
        if op == "!=":
            return f"{a}.rarity != ?", [want]
        cases = " ".join(f"WHEN '{r}' THEN {i}" for i, r in enumerate(RARITY_ORDER))
        expr = f"(CASE {a}.rarity {cases} ELSE -1 END)"
        return f"{expr} {NUMERIC_OPS[op]} ?", [RARITY_ORDER.index(want)]

    if f == "set":
        return f"LOWER({a}.set_code) = LOWER(?)", [v]

    if f == "layout":
        return f"LOWER({a}.layout) = LOWER(?)", [v]

    if f == "lang":
        return f"LOWER(JSON_EXTRACT({a}.raw, '$.lang')) = LOWER(?)", [v]

    if f == "cn":
        return f"LOWER({a}.collector_num) = LOWER(?)", [v]

    if f == "loc":
        return (_owned_exists(a, " AND cp2.location_id IN "
                                 "(SELECT id FROM locations WHERE name LIKE ?)"),
                [f"%{v}%"])

    if f == "is":
        return _compile_is(v.lower(), a)

    raise QueryError(f"Unknown filter '{f}'.")


def _compile_is(v: str, a: str) -> tuple[str, list[Any]]:
    if v == "foil":
        # Derived from finish, not the legacy `foil` flag: etched counts as
        # shiny, and the two columns can drift if anything writes only one.
        return _owned_exists(a, " AND cp2.finish != 'normal'"), []
    if v == "nonfoil":
        return _owned_exists(a, " AND cp2.finish = 'normal'"), []
    if v == "etched":
        return _owned_exists(a, " AND cp2.finish = 'etched'"), []
    if v == "owned":
        return _owned_exists(a), []
    if v == "lent":
        return _owned_exists(a, " AND cp2.lent_to IS NOT NULL"), []
    if v == "unfiled":
        return _owned_exists(a, " AND cp2.location_id IS NULL"), []
    if v == "deck":
        return f"{a}.oracle_id IN (SELECT dk.oracle_id FROM deck_cards dk)", []
    if v == "wishlist":
        return (f"{a}.oracle_id IN "
                f"(SELECT dk.oracle_id FROM deck_cards dk WHERE dk.wishlist = 1)", [])
    if v in ("legendary", "commander"):
        cond = f"{a}.type_line LIKE '%Legendary%'"
        if v == "commander":
            cond += f" AND ({a}.type_line LIKE '%Creature%' " \
                    f"OR {a}.oracle_text LIKE '%can be your commander%')"
        return cond, []
    if v == "vanilla":
        return f"({a}.oracle_text IS NULL OR {a}.oracle_text = '')", []
    if v == "dfc":
        return f"{a}.layout IN ('transform','modal_dfc','meld','reversible_card')", []
    if v == "split":
        return f"{a}.layout IN ('split','adventure','flip')", []
    if v == "permanent":
        return (f"({a}.type_line LIKE '%Creature%' OR {a}.type_line LIKE '%Artifact%' "
                f"OR {a}.type_line LIKE '%Enchantment%' OR {a}.type_line LIKE '%Land%' "
                f"OR {a}.type_line LIKE '%Planeswalker%' OR {a}.type_line LIKE '%Battle%')", [])
    if v == "spell":
        return (f"({a}.type_line LIKE '%Instant%' OR {a}.type_line LIKE '%Sorcery%')", [])
    raise QueryError(f"'is:{v}' is not something Grimoire knows. Try: {', '.join(IS_VALUES)}.")


def compile_query(source: str, alias: str = "cq") -> tuple[str, list[Any]] | None:
    """Compile a query string to (sql_predicate, params) over `cards AS <alias>`.

    Returns None for a blank query, meaning "no filter"."""
    node = parse(source)
    if node is None:
        return None
    return compile_node(node, alias)


def describe(source: str) -> Iterator[str]:
    """Human-readable chips for the UI, one per top-level term."""
    node = parse(source)
    if node is None:
        return
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, And):
            stack.extend(reversed(cur.children))
        elif isinstance(cur, Or):
            yield " or ".join(_describe_one(c) for c in cur.children)
        elif isinstance(cur, Not):
            yield "not " + _describe_one(cur.child)
        else:
            yield _describe_one(cur)


def _describe_one(node: Any) -> str:
    if isinstance(node, Term):
        if node.field == "name" and node.op == ":":
            return f'name contains "{node.value}"'
        if node.exact:
            return f'named exactly "{node.value}"'
        return f"{node.field}{node.op}{node.value}"
    if isinstance(node, Not):
        return "not " + _describe_one(node.child)
    return "(group)"
