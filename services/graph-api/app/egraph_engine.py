"""Standard 4: Congruence-Closure E-Graph & Equality Saturation Engine (Willsey et al., POPL 2021).

Implements a pure-Python bounded E-Graph with:
- Union-Find with path compression + canonical hashconsing + upward congruence-closure
  ``rebuild()``.
- E-Class Lattice Domain & Algebra Analysis (``EClassData``) tracking commutativity,
  nonzero, positivity, nonnegativity, realness, and exact ``Fraction`` constant values.
- Sound guarded rewrite rules:
  - Ring addition & multiplication (strictly keeping matrix/vector/unknown products
    NON-COMMUTATIVE; ``mul_comm_scalar`` fires ONLY when both sides are proved scalar).
  - Non-commutative matrix transpose identities (``(A^T)^T = A``, ``(A*B)^T = B^T*A^T``,
    ``(A+B)^T = A^T + B^T``).
  - Guarded division rules (``a/a = 1`` and ``(a/b)*b = a`` ONLY when ``is_nonzero(b)``).
  - Guarded logarithm, exponential, and square-root rules (``log(a/b) = log(a)-log(b)``
    ONLY when ``is_positive(a)`` & ``is_positive(b)``, ``sqrt(x^2) = x`` ONLY when
    ``is_nonnegative(x)``).
  - Non-zero scalar factor equation rescaling (``Eq(L1, R1) <=> Eq(L2, R2)`` via nonzero
    denominator factor ``c * (L2 - R2) == L1 - R1``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction

from app.formula_ast import AstNode

MAX_EGRAPH_NODES = 2_000
MAX_EGRAPH_ITERATIONS = 12
_MAX_CONST_BITS = 128
_BUILTIN_CALLS = frozenset(
    {
        "log",
        "ln",
        "exp",
        "sqrt",
        "abs",
        "sin",
        "cos",
        "tan",
        "sinh",
        "cosh",
        "tanh",
        "floor",
        "ceil",
        "round",
    }
)


class EGraphUnsupported(Exception):
    """Raised when an AST contains constructs delegated directly to SymPy (sums/integrals/etc.)."""


@dataclass(frozen=True)
class ENode:
    """Canonical expression node whose children are canonical e-class IDs."""

    op: str
    children: tuple[int, ...] = ()
    value: str | None = None


@dataclass(frozen=True)
class EClassData:
    """Sound domain & algebraic property lattice attached to each e-class."""

    is_commutative: bool | None = None
    is_nonzero: bool = False
    is_positive: bool = False
    is_nonnegative: bool = False
    is_real: bool = False
    constant: Fraction | None = None


@dataclass
class EClass:
    id: int
    nodes: set[ENode]
    parents: set[tuple[ENode, int]]
    data: EClassData


def _join_commutativity(left: bool | None, right: bool | None) -> bool | None:
    if left is False or right is False:
        return False
    if left is True or right is True:
        return True
    return None


def _merge_data(left: EClassData, right: EClassData) -> EClassData:
    is_pos = left.is_positive or right.is_positive
    is_nonneg = left.is_nonnegative or right.is_nonnegative or is_pos
    is_nz = left.is_nonzero or right.is_nonzero or is_pos
    is_real = left.is_real or right.is_real or is_pos or is_nonneg
    const = left.constant if left.constant is not None else right.constant
    if const is not None:
        is_nz = is_nz or (const != 0)
        is_pos = is_pos or (const > 0)
        is_nonneg = is_nonneg or (const >= 0)
        is_real = True
    return EClassData(
        is_commutative=_join_commutativity(left.is_commutative, right.is_commutative),
        is_nonzero=is_nz,
        is_positive=is_pos,
        is_nonnegative=is_nonneg,
        is_real=is_real,
        constant=const,
    )


def _fraction_str(val: Fraction) -> str:
    if val.denominator == 1:
        return str(val.numerator)
    return f"{val.numerator}/{val.denominator}"


class EGraph:
    """Bounded congruence-closure E-Graph with upward analysis propagation."""

    def __init__(self, *, max_nodes: int = MAX_EGRAPH_NODES) -> None:
        self.max_nodes = max_nodes
        self._next_id = 0
        self.parent: dict[int, int] = {}
        self.classes: dict[int, EClass] = {}
        self.hashcons: dict[ENode, int] = {}
        self.worklist: list[int] = []
        self.total_nodes = 0

    def find(self, eid: int) -> int:
        root = eid
        while self.parent[root] != root:
            root = self.parent[root]
        while eid != root:
            nxt = self.parent[eid]
            self.parent[eid] = root
            eid = nxt
        return root

    def canonicalize(self, node: ENode) -> ENode:
        if not node.children:
            return node
        return ENode(node.op, tuple(self.find(c) for c in node.children), node.value)

    def data(self, eid: int) -> EClassData:
        return self.classes[self.find(eid)].data

    def add(self, node: ENode, seed_data: EClassData | None = None) -> int:
        canon = self.canonicalize(node)
        existing = self.hashcons.get(canon)
        if existing is not None:
            root = self.find(existing)
            if seed_data is not None:
                cls = self.classes[root]
                merged = _merge_data(cls.data, seed_data)
                if merged != cls.data:
                    cls.data = merged
                    self.worklist.append(root)
            return root

        if self.total_nodes >= self.max_nodes:
            raise _CapacityReached

        eid = self._next_id
        self._next_id += 1
        self.parent[eid] = eid
        computed = self._eval_node_data(canon)
        initial_data = _merge_data(computed, seed_data) if seed_data is not None else computed
        cls = EClass(id=eid, nodes={canon}, parents=set(), data=initial_data)
        self.classes[eid] = cls
        self.hashcons[canon] = eid
        self.total_nodes += 1
        for child_id in canon.children:
            c_root = self.find(child_id)
            self.classes[c_root].parents.add((canon, eid))
        return eid

    def set_data(self, eid: int, extra: EClassData) -> None:
        root = self.find(eid)
        cls = self.classes[root]
        merged = _merge_data(cls.data, extra)
        if merged != cls.data:
            cls.data = merged
            self.worklist.append(root)

    def union(self, id1: int, id2: int) -> int:
        r1 = self.find(id1)
        r2 = self.find(id2)
        if r1 == r2:
            return r1
        c1 = self.classes[r1]
        c2 = self.classes[r2]
        if len(c1.nodes) < len(c2.nodes):
            r1, r2 = r2, r1
            c1, c2 = c2, c1
        self.parent[r2] = r1
        c1.nodes.update(c2.nodes)
        c1.parents.update(c2.parents)
        c1.data = _merge_data(c1.data, c2.data)
        del self.classes[r2]
        self.worklist.append(r1)
        return r1

    def rebuild(self) -> None:
        while self.worklist:
            todo = {self.find(eid) for eid in self.worklist if eid in self.parent}
            self.worklist.clear()
            for cid in sorted(todo):
                root = self.find(cid)
                if root in self.classes:
                    self._repair(root)

    def _repair(self, cid: int) -> None:
        cls = self.classes.get(cid)
        if cls is None:
            return
        old_parents = list(cls.parents)
        cls.parents.clear()
        new_parents: dict[ENode, int] = {}

        for p_node, p_eid in old_parents:
            self.hashcons.pop(p_node, None)
            canon_p = self.canonicalize(p_node)
            p_root = self.find(p_eid)
            if p_node != canon_p and p_root in self.classes:
                self.classes[p_root].nodes.discard(p_node)
                self.classes[p_root].nodes.add(canon_p)
            existing = new_parents.get(canon_p)
            if existing is not None:
                existing_root = self.find(existing)
                if existing_root != p_root:
                    p_root = self.union(existing_root, p_root)
            p_root = self.find(p_root)
            self.hashcons[canon_p] = p_root
            new_parents[canon_p] = p_root
            if p_root in self.classes:
                p_cls = self.classes[p_root]
                upgraded = _merge_data(p_cls.data, self._eval_node_data(canon_p))
                if upgraded != p_cls.data:
                    p_cls.data = upgraded
                    self.worklist.append(p_root)

        cur_root = self.find(cid)
        if cur_root in self.classes:
            self.classes[cur_root].parents.update(
                {(node, self.find(eid)) for node, eid in new_parents.items()}
            )

    def _eval_node_data(self, node: ENode) -> EClassData:
        op = node.op
        if op == "num" and node.value is not None:
            try:
                val = Fraction(node.value)
            except (ValueError, ZeroDivisionError):
                return EClassData(is_commutative=True, is_real=True)
            return EClassData(
                is_commutative=True,
                is_nonzero=val != 0,
                is_positive=val > 0,
                is_nonnegative=val >= 0,
                is_real=True,
                constant=val,
            )
        if op == "const" and node.value == "pi":
            return EClassData(
                is_commutative=True,
                is_nonzero=True,
                is_positive=True,
                is_nonnegative=True,
                is_real=True,
            )
        if op == "zero_elem":
            return EClassData(is_commutative=None, is_nonnegative=True, is_real=True)
        if op == "neg" and len(node.children) == 1:
            c = self.data(node.children[0])
            return EClassData(
                is_commutative=c.is_commutative,
                is_nonzero=c.is_nonzero,
                is_real=c.is_real,
                constant=(-c.constant) if c.constant is not None else None,
            )
        if op == "add" and len(node.children) == 2:
            l_d, r_d = self.data(node.children[0]), self.data(node.children[1])
            is_pos = (l_d.is_positive and r_d.is_nonnegative) or (
                l_d.is_nonnegative and r_d.is_positive
            )
            is_nonneg = l_d.is_nonnegative and r_d.is_nonnegative
            is_nz = (
                is_pos
                or (l_d.constant == 0 and r_d.is_nonzero)
                or (r_d.constant == 0 and l_d.is_nonzero)
            )
            return EClassData(
                is_commutative=_join_commutativity(l_d.is_commutative, r_d.is_commutative),
                is_nonzero=is_nz,
                is_positive=is_pos,
                is_nonnegative=is_nonneg,
                is_real=l_d.is_real and r_d.is_real,
            )
        if op == "mul" and len(node.children) == 2:
            l_d, r_d = self.data(node.children[0]), self.data(node.children[1])
            is_pos = l_d.is_positive and r_d.is_positive
            is_nonneg = is_pos or (l_d.is_nonnegative and r_d.is_nonnegative)
            is_nz = l_d.is_nonzero and r_d.is_nonzero
            if l_d.is_commutative is False or r_d.is_commutative is False:
                comm: bool | None = False
            elif l_d.is_commutative is True and r_d.is_commutative is True:
                comm = True
            else:
                comm = None
            return EClassData(
                is_commutative=comm,
                is_nonzero=is_nz,
                is_positive=is_pos,
                is_nonnegative=is_nonneg,
                is_real=l_d.is_real and r_d.is_real,
            )
        if op == "div" and len(node.children) == 2:
            l_d, r_d = self.data(node.children[0]), self.data(node.children[1])
            is_pos = l_d.is_positive and r_d.is_positive
            is_nonneg = l_d.is_nonnegative and r_d.is_positive
            is_nz = l_d.is_nonzero and r_d.is_nonzero
            return EClassData(
                is_commutative=l_d.is_commutative if r_d.is_commutative is True else None,
                is_nonzero=is_nz,
                is_positive=is_pos,
                is_nonnegative=is_nonneg,
                is_real=l_d.is_real and r_d.is_real and r_d.is_nonzero,
            )
        if op == "pow" and len(node.children) == 2:
            b_d, e_d = self.data(node.children[0]), self.data(node.children[1])
            is_int_exp = e_d.constant is not None and e_d.constant.denominator == 1
            is_even_int = (
                is_int_exp and e_d.constant is not None and (e_d.constant.numerator % 2 == 0)
            )
            bad_zero_pow = (
                b_d.constant == 0 and e_d.constant is not None and e_d.constant <= 0
            )
            is_pos = b_d.is_positive and e_d.is_real
            is_nonneg = (b_d.is_nonnegative and e_d.is_real and not bad_zero_pow) or (
                b_d.is_real and is_even_int
            )
            return EClassData(
                is_commutative=b_d.is_commutative,
                is_nonzero=is_pos or b_d.is_nonzero,
                is_positive=is_pos,
                is_nonnegative=is_nonneg,
                is_real=b_d.is_real and (b_d.is_positive or is_int_exp),
            )
        if op == "transpose" and len(node.children) == 1:
            return EClassData(is_commutative=False, is_real=self.data(node.children[0]).is_real)
        if op == "exp" and len(node.children) == 1:
            arg_d = self.data(node.children[0])
            return EClassData(
                is_commutative=arg_d.is_commutative,
                is_nonzero=True,
                is_positive=arg_d.is_real,
                is_nonnegative=arg_d.is_real,
                is_real=arg_d.is_real,
            )
        if op == "sqrt" and len(node.children) == 1:
            arg_d = self.data(node.children[0])
            return EClassData(
                is_commutative=arg_d.is_commutative,
                is_nonzero=arg_d.is_positive,
                is_positive=arg_d.is_positive,
                is_nonnegative=arg_d.is_nonnegative,
                is_real=arg_d.is_nonnegative,
            )
        if op == "log" and node.children:
            arg_d = self.data(node.children[0])
            return EClassData(
                is_commutative=arg_d.is_commutative,
                is_real=arg_d.is_positive,
            )
        return EClassData()


class _CapacityReached(Exception):
    pass


def _assumption_data(
    name: str,
    symbol_assumptions: Mapping[str, Mapping[str, object]] | None,
) -> EClassData:
    if not symbol_assumptions or name not in symbol_assumptions:
        return EClassData()
    spec = symbol_assumptions[name]
    cat = spec.get("category")
    domain = spec.get("domain")
    nonzero = bool(spec.get("nonzero", False))
    positive = bool(spec.get("positive", False))
    nonnegative = bool(spec.get("nonnegative", False))
    if cat in {"vector", "matrix", "tensor"}:
        return EClassData(is_commutative=False, is_real=(domain != "complex"))
    if cat not in {"scalar", "index"}:
        return EClassData()
    is_pos = domain == "positive" or positive
    is_nonneg = domain == "non_negative" or is_pos or nonnegative
    is_nz = nonzero or bool(spec.get("negative", False)) or is_pos or (is_nonneg and nonzero)
    is_real = domain != "complex"
    return EClassData(
        is_commutative=True,
        is_nonzero=is_nz,
        is_positive=is_pos,
        is_nonnegative=is_nonneg,
        is_real=is_real,
    )


def _base_matrix_name(node: AstNode) -> str | None:
    if node.kind == "symbol":
        return node.value
    if node.kind == "style" and len(node.children) == 1:
        return _base_matrix_name(node.children[0])
    if node.kind in {"subscript", "power"} and node.children:
        return _base_matrix_name(node.children[0])
    return None


def _has_matrix_like_symbol(
    node: AstNode,
    symbol_assumptions: Mapping[str, Mapping[str, object]] | None,
) -> bool:
    if not symbol_assumptions:
        return False
    if node.kind == "symbol" and node.value:
        return symbol_assumptions.get(node.value, {}).get("category") in {
            "matrix",
            "vector",
            "tensor",
        }
    return any(_has_matrix_like_symbol(child, symbol_assumptions) for child in node.children)


def _matrix_tag(
    base: AstNode,
    exp: AstNode,
    symbol_assumptions: Mapping[str, Mapping[str, object]] | None,
) -> str | None:
    if not symbol_assumptions:
        return None
    bname = _base_matrix_name(base)
    if not bname or bname not in symbol_assumptions:
        return None
    if symbol_assumptions[bname].get("category") not in {"matrix", "vector", "tensor"}:
        return None
    if exp.kind != "symbol" or not exp.value or exp.value in {"T", "top"}:
        return None
    exp_spec = symbol_assumptions.get(exp.value)
    if exp_spec is not None and exp_spec.get("category") in {"scalar", "index"}:
        return None
    is_matrix_exp = exp_spec is not None and exp_spec.get("category") in {
        "matrix",
        "vector",
        "tensor",
    }
    if is_matrix_exp or (len(exp.value) == 1 and exp.value.isupper()):
        return exp.value
    return None


def _index_names(node: AstNode, out: list[str]) -> None:
    if node.kind in {"symbol", "number"} and node.value:
        out.append(node.value)
    else:
        for child in node.children:
            _index_names(child, out)


def add_ast_to_egraph(
    eg: EGraph,
    node: AstNode,
    *,
    symbol_assumptions: Mapping[str, Mapping[str, object]] | None = None,
) -> int:
    """Lower a Formula AST into canonical E-Graph e-classes."""

    def lower(n: AstNode) -> int:
        if n.kind == "number" and n.value is not None:
            try:
                val = Fraction(n.value)
            except (ValueError, ZeroDivisionError) as exc:
                raise EGraphUnsupported from exc
            return eg.add(ENode("num", value=_fraction_str(val)))

        if n.kind == "symbol" and n.value:
            if n.value == "pi":
                return eg.add(ENode("const", value="pi"))
            if n.value in {"infty", "infinity", "ellipsis"}:
                raise EGraphUnsupported
            return eg.add(
                ENode("sym", value=n.value),
                seed_data=_assumption_data(n.value, symbol_assumptions),
            )

        if n.kind == "style" and len(n.children) == 1:
            return lower(n.children[0])

        if n.kind == "negate" and len(n.children) == 1:
            return eg.add(ENode("neg", (lower(n.children[0]),)))

        if n.kind == "add" and n.children:
            acc = lower(n.children[0])
            for ch in n.children[1:]:
                acc = eg.add(ENode("add", (acc, lower(ch))))
            return acc

        if n.kind == "multiply" and n.children:
            if n.value == "times":
                raise EGraphUnsupported
            acc = lower(n.children[0])
            for ch in n.children[1:]:
                acc = eg.add(ENode("mul", (acc, lower(ch))))
            return acc

        if n.kind == "divide" and len(n.children) == 2:
            return eg.add(ENode("div", (lower(n.children[0]), lower(n.children[1]))))

        if n.kind == "power" and len(n.children) == 2:
            base_node, exp_node = n.children
            if n.attribute("operation") == "transpose":
                if _has_matrix_like_symbol(base_node, symbol_assumptions):
                    return eg.add(ENode("transpose", (lower(base_node),)))
                raise EGraphUnsupported
            tag = _matrix_tag(base_node, exp_node, symbol_assumptions)
            if tag is not None:
                inner = base_node
                while inner.kind == "style" and len(inner.children) == 1:
                    inner = inner.children[0]
                if inner.kind == "subscript" and len(inner.children) == 2:
                    bname = _base_matrix_name(inner.children[0]) or "M"
                    idx: list[str] = []
                    _index_names(inner.children[1], idx)
                    sym_id = f"{bname}__sup_{tag}" + (f"__sub_{'_'.join(idx)}" if idx else "")
                else:
                    bname = _base_matrix_name(inner) or "M"
                    sym_id = f"{bname}__sup_{tag}"
                return eg.add(
                    ENode("sym", value=sym_id),
                    seed_data=EClassData(is_commutative=False),
                )
            return eg.add(ENode("pow", (lower(base_node), lower(exp_node))))

        if n.kind == "subscript" and len(n.children) >= 1:
            base = n.children[0]
            while base.kind == "style" and len(base.children) == 1:
                base = base.children[0]
            idx_parts: list[str] = []
            if len(n.children) >= 2:
                _index_names(n.children[1], idx_parts)
            if base.kind == "power" and len(base.children) == 2:
                tag = _matrix_tag(base.children[0], base.children[1], symbol_assumptions)
                if tag is None:
                    raise EGraphUnsupported
                bname = _base_matrix_name(base.children[0]) or "M"
                suffix = f"__sub_{'_'.join(idx_parts)}" if idx_parts else ""
                sym_id = f"{bname}__sup_{tag}{suffix}"
                return eg.add(
                    ENode("sym", value=sym_id),
                    seed_data=EClassData(is_commutative=False),
                )
            if base.kind != "symbol" or not base.value:
                raise EGraphUnsupported
            bname = base.value
            joined = f"{bname}_{'_'.join(idx_parts)}" if idx_parts else bname
            seed = _assumption_data(joined, symbol_assumptions)
            if (
                symbol_assumptions
                and joined not in symbol_assumptions
                and bname in symbol_assumptions
            ):
                seed = _assumption_data(bname, symbol_assumptions)
            return eg.add(ENode("sym", value=joined), seed_data=seed)

        if n.kind == "call" and n.children:
            fn = n.children[0]
            fname = fn.value if fn.kind == "symbol" and fn.value else None
            if not fname:
                if fn.kind == "style" and len(fn.children) == 1 and fn.children[0].kind == "symbol":
                    fname = fn.children[0].value
                else:
                    raise EGraphUnsupported
            if not fname:
                raise EGraphUnsupported
            # Treat explicit non-function symbols like A(B + C) as juxtaposition multiplication
            # when contracts establish fn is a matrix, vector, tensor, or scalar value.
            fn_spec = symbol_assumptions.get(fname) if symbol_assumptions else None
            if (
                fname not in _BUILTIN_CALLS
                and fn_spec is not None
                and fn_spec.get("category") in {"scalar", "vector", "matrix", "tensor"}
                and len(n.children) == 2
            ):
                return eg.add(ENode("mul", (lower(fn), lower(n.children[1]))))
            op_name = "log" if fname == "ln" else fname
            if op_name in _BUILTIN_CALLS:
                if op_name == "log" and len(n.children) == 3:
                    num = eg.add(ENode("log", (lower(n.children[1]),)))
                    den = eg.add(ENode("log", (lower(n.children[2]),)))
                    return eg.add(ENode("div", (num, den)))
                args = tuple(lower(c) for c in n.children[1:])
                if op_name == "log" and len(args) != 1:
                    raise EGraphUnsupported
                return eg.add(ENode(op_name, args))
            args = tuple(lower(c) for c in n.children[1:])
            arg_comm = all(eg.data(a).is_commutative is True for a in args) if args else True
            return eg.add(
                ENode(f"call:{op_name}", args),
                seed_data=EClassData(is_commutative=bool(arg_comm)),
            )

        raise EGraphUnsupported

    return lower(node)


def _fold_constants(eg: EGraph) -> list[tuple[int, int, str]]:
    merges: list[tuple[int, int, str]] = []
    for cid, cls in list(eg.classes.items()):
        for node in list(cls.nodes):
            val: Fraction | None = None
            if node.op == "neg" and len(node.children) == 1:
                c0 = eg.data(node.children[0]).constant
                if c0 is not None:
                    val = -c0
            elif node.op in {"add", "mul", "div", "pow"} and len(node.children) == 2:
                c0 = eg.data(node.children[0]).constant
                c1 = eg.data(node.children[1]).constant
                if c0 is not None and c1 is not None:
                    if node.op == "add":
                        val = c0 + c1
                    elif node.op == "mul":
                        val = c0 * c1
                    elif node.op == "div" and c1 != 0:
                        val = c0 / c1
                    elif (
                        node.op == "pow"
                        and c1.denominator == 1
                        and abs(c1.numerator) <= 12
                        and not (c0 == 0 and c1.numerator <= 0)
                    ):
                        val = c0 ** int(c1.numerator)
            if (
                val is not None
                and val.numerator.bit_length() <= _MAX_CONST_BITS
                and val.denominator.bit_length() <= _MAX_CONST_BITS
            ):
                num_id = eg.add(ENode("num", value=_fraction_str(val)))
                if eg.find(num_id) != eg.find(cid):
                    merges.append((cid, num_id, "const_fold"))
    return merges


def _apply_rewrites_once(eg: EGraph, tracker: set[str]) -> int:
    merges: list[tuple[int, int, str]] = []
    snapshot_classes = list(eg.classes.items())
    zero_num = eg.add(ENode("num", value="0"))
    zero_elem = eg.add(ENode("zero_elem"))
    one_num = eg.add(ENode("num", value="1"))
    two_num = eg.add(ENode("num", value="2"))

    merges.extend(_fold_constants(eg))

    def queue(lhs: int, rhs: int, rule: str) -> None:
        if eg.find(lhs) != eg.find(rhs):
            merges.append((lhs, rhs, rule))

    for cid, cls in snapshot_classes:
        nodes = list(cls.nodes)
        for node in nodes:
            if eg.total_nodes + len(merges) >= eg.max_nodes:
                break
            op = node.op
            ch = node.children

            if op == "neg" and len(ch) == 1:
                x = ch[0]
                if eg.find(x) in {eg.find(zero_num), eg.find(zero_elem)}:
                    queue(cid, x, "neg_zero")
                for sub in list(eg.classes[eg.find(x)].nodes):
                    if sub.op == "neg" and len(sub.children) == 1:
                        queue(cid, sub.children[0], "neg_neg")
                    elif sub.op == "add" and len(sub.children) == 2:
                        na = eg.add(ENode("neg", (sub.children[0],)))
                        nb = eg.add(ENode("neg", (sub.children[1],)))
                        queue(cid, eg.add(ENode("add", (na, nb))), "neg_add")
                    elif sub.op == "mul" and len(sub.children) == 2:
                        na = eg.add(ENode("neg", (sub.children[0],)))
                        queue(cid, eg.add(ENode("mul", (na, sub.children[1]))), "neg_mul_l")
                    elif sub.op == "div" and len(sub.children) == 2:
                        na = eg.add(ENode("neg", (sub.children[0],)))
                        queue(cid, eg.add(ENode("div", (na, sub.children[1]))), "neg_div")

            elif op == "add" and len(ch) == 2:
                a, b = ch
                queue(cid, eg.add(ENode("add", (b, a))), "add_comm")
                if eg.find(b) in {eg.find(zero_num), eg.find(zero_elem)}:
                    queue(cid, a, "add_zero")
                if eg.find(a) in {eg.find(zero_num), eg.find(zero_elem)}:
                    queue(cid, b, "add_zero")
                if eg.find(a) == eg.find(b):
                    queue(cid, eg.add(ENode("mul", (two_num, a))), "add_double")
                for sub_b in list(eg.classes[eg.find(b)].nodes):
                    if (
                        sub_b.op == "neg"
                        and len(sub_b.children) == 1
                        and eg.find(sub_b.children[0]) == eg.find(a)
                    ):
                        t_zero = zero_num if eg.data(a).is_commutative is True else zero_elem
                        queue(cid, t_zero, "add_neg_self")
                    elif sub_b.op == "add" and len(sub_b.children) == 2:
                        ab = eg.add(ENode("add", (a, sub_b.children[0])))
                        queue(cid, eg.add(ENode("add", (ab, sub_b.children[1]))), "add_assoc_rl")
                for sub_a in list(eg.classes[eg.find(a)].nodes):
                    if sub_a.op == "add" and len(sub_a.children) == 2:
                        bc = eg.add(ENode("add", (sub_a.children[1], b)))
                        queue(cid, eg.add(ENode("add", (sub_a.children[0], bc))), "add_assoc_lr")
                    if (
                        sub_a.op == "log"
                        and len(sub_a.children) == 1
                        and eg.data(sub_a.children[0]).is_positive
                    ):
                        for sub_b in list(eg.classes[eg.find(b)].nodes):
                            if (
                                sub_b.op == "log"
                                and len(sub_b.children) == 1
                                and eg.data(sub_b.children[0]).is_positive
                            ):
                                prod = eg.add(ENode("mul", (sub_a.children[0], sub_b.children[0])))
                                queue(cid, eg.add(ENode("log", (prod,))), "log_mul_combine")
                            elif sub_b.op == "neg" and len(sub_b.children) == 1:
                                for neg_in in list(eg.classes[eg.find(sub_b.children[0])].nodes):
                                    if (
                                        neg_in.op == "log"
                                        and len(neg_in.children) == 1
                                        and eg.data(neg_in.children[0]).is_positive
                                    ):
                                        q = eg.add(
                                            ENode("div", (sub_a.children[0], neg_in.children[0]))
                                        )
                                        queue(cid, eg.add(ENode("log", (q,))), "log_div_combine")

            elif op == "mul" and len(ch) == 2:
                a, b = ch
                da, db = eg.data(a), eg.data(b)
                if eg.find(a) == eg.find(one_num):
                    queue(cid, b, "mul_one_l")
                if eg.find(b) == eg.find(one_num):
                    queue(cid, a, "mul_one_r")
                if eg.find(a) == eg.find(zero_num) or eg.find(b) == eg.find(zero_num):
                    both_comm = da.is_commutative is True and db.is_commutative is True
                    queue(cid, zero_num if both_comm else zero_elem, "mul_zero")
                if eg.find(a) == eg.find(zero_elem) or eg.find(b) == eg.find(zero_elem):
                    queue(cid, zero_elem, "mul_zero")

                # STRICT COMMUTATIVITY GUARD: only scalar * scalar or scalar past non-scalar.
                if da.is_commutative is True and db.is_commutative is True:
                    queue(cid, eg.add(ENode("mul", (b, a))), "mul_comm_scalar")
                elif (da.is_commutative is True and db.is_commutative is False) or (
                    da.is_commutative is False and db.is_commutative is True
                ):
                    queue(cid, eg.add(ENode("mul", (b, a))), "mul_scalar_comm")

                for sub_a in list(eg.classes[eg.find(a)].nodes):
                    if sub_a.op == "neg" and len(sub_a.children) == 1:
                        prod = eg.add(ENode("mul", (sub_a.children[0], b)))
                        queue(cid, eg.add(ENode("neg", (prod,))), "mul_neg_l")
                    elif sub_a.op == "add" and len(sub_a.children) == 2:
                        xb = eg.add(ENode("mul", (sub_a.children[0], b)))
                        yb = eg.add(ENode("mul", (sub_a.children[1], b)))
                        queue(cid, eg.add(ENode("add", (xb, yb))), "distrib_r")
                    elif sub_a.op == "mul" and len(sub_a.children) == 2:
                        bc = eg.add(ENode("mul", (sub_a.children[1], b)))
                        queue(cid, eg.add(ENode("mul", (sub_a.children[0], bc))), "mul_assoc_lr")
                    elif sub_a.op == "div" and len(sub_a.children) == 2:
                        num_a, den_a = sub_a.children
                        d_den = eg.data(den_a)
                        if d_den.is_nonzero and d_den.is_commutative is True:
                            if eg.find(den_a) == eg.find(b):
                                queue(cid, num_a, "div_mul_cancel_l")
                            else:
                                prod = eg.add(ENode("mul", (num_a, b)))
                                queue(cid, eg.add(ENode("div", (prod, den_a))), "div_mul_pull_l")

                for sub_b in list(eg.classes[eg.find(b)].nodes):
                    if sub_b.op == "neg" and len(sub_b.children) == 1:
                        prod = eg.add(ENode("mul", (a, sub_b.children[0])))
                        queue(cid, eg.add(ENode("neg", (prod,))), "mul_neg_r")
                    elif sub_b.op == "add" and len(sub_b.children) == 2:
                        ax = eg.add(ENode("mul", (a, sub_b.children[0])))
                        ay = eg.add(ENode("mul", (a, sub_b.children[1])))
                        queue(cid, eg.add(ENode("add", (ax, ay))), "distrib_l")
                    elif sub_b.op == "mul" and len(sub_b.children) == 2:
                        ab = eg.add(ENode("mul", (a, sub_b.children[0])))
                        queue(cid, eg.add(ENode("mul", (ab, sub_b.children[1]))), "mul_assoc_rl")
                    elif sub_b.op == "div" and len(sub_b.children) == 2:
                        num_b, den_b = sub_b.children
                        d_den = eg.data(den_b)
                        if d_den.is_nonzero and d_den.is_commutative is True:
                            if eg.find(den_b) == eg.find(a) and da.is_commutative is True:
                                queue(cid, num_b, "div_mul_cancel_r")
                            elif (
                                da.is_commutative is True
                                or eg.data(num_b).is_commutative is False
                            ):
                                prod = eg.add(ENode("mul", (a, num_b)))
                                queue(cid, eg.add(ENode("div", (prod, den_b))), "div_mul_pull_r")

            elif op == "div" and len(ch) == 2:
                num, den = ch
                d_den = eg.data(den)
                if eg.find(den) == eg.find(one_num):
                    queue(cid, num, "div_one")
                if d_den.is_nonzero:
                    if eg.find(num) == eg.find(den):
                        queue(cid, one_num, "div_self")
                    if d_den.is_commutative is True:
                        neg_one = eg.add(ENode("num", value="-1"))
                        inv_b = eg.add(ENode("pow", (den, neg_one)))
                        queue(cid, eg.add(ENode("mul", (num, inv_b))), "div_to_pow")
                        for sub_num in list(eg.classes[eg.find(num)].nodes):
                            if sub_num.op == "add" and len(sub_num.children) == 2:
                                d1 = eg.add(ENode("div", (sub_num.children[0], den)))
                                d2 = eg.add(ENode("div", (sub_num.children[1], den)))
                                queue(cid, eg.add(ENode("add", (d1, d2))), "div_add_distrib")
                            elif sub_num.op == "neg" and len(sub_num.children) == 1:
                                pos_div = eg.add(ENode("div", (sub_num.children[0], den)))
                                queue(cid, eg.add(ENode("neg", (pos_div,))), "div_neg_num")

            elif op == "pow" and len(ch) == 2:
                base, exp_id = ch
                d_base, d_exp = eg.data(base), eg.data(exp_id)
                if eg.find(exp_id) == eg.find(one_num):
                    queue(cid, base, "pow_one")
                if eg.find(exp_id) == eg.find(two_num):
                    queue(cid, eg.add(ENode("mul", (base, base))), "pow_two")
                if (
                    d_exp.constant == Fraction(-1)
                    and d_base.is_nonzero
                    and d_base.is_commutative is True
                ):
                    queue(cid, eg.add(ENode("div", (one_num, base))), "pow_neg_one_div")

            elif op == "transpose" and len(ch) == 1:
                inner = ch[0]
                if eg.data(inner).is_commutative is True:
                    queue(cid, inner, "transpose_scalar")
                for sub in list(eg.classes[eg.find(inner)].nodes):
                    if sub.op == "transpose" and len(sub.children) == 1:
                        queue(cid, sub.children[0], "transpose_involutive")
                    elif sub.op == "mul" and len(sub.children) == 2:
                        tb = eg.add(ENode("transpose", (sub.children[1],)))
                        ta = eg.add(ENode("transpose", (sub.children[0],)))
                        queue(cid, eg.add(ENode("mul", (tb, ta))), "transpose_mul")
                    elif sub.op == "add" and len(sub.children) == 2:
                        ta = eg.add(ENode("transpose", (sub.children[0],)))
                        tb = eg.add(ENode("transpose", (sub.children[1],)))
                        queue(cid, eg.add(ENode("add", (ta, tb))), "transpose_add")
                    elif sub.op == "neg" and len(sub.children) == 1:
                        ta = eg.add(ENode("transpose", (sub.children[0],)))
                        queue(cid, eg.add(ENode("neg", (ta,))), "transpose_neg")

            elif op == "log" and len(ch) == 1:
                arg = ch[0]
                for sub in list(eg.classes[eg.find(arg)].nodes):
                    if (
                        sub.op == "exp"
                        and len(sub.children) == 1
                        and eg.data(sub.children[0]).is_real
                    ):
                        queue(cid, sub.children[0], "log_exp")
                    elif sub.op == "div" and len(sub.children) == 2:
                        num, den = sub.children
                        if eg.data(num).is_positive and eg.data(den).is_positive:
                            lnum = eg.add(ENode("log", (num,)))
                            lden = eg.add(ENode("log", (den,)))
                            neg_lden = eg.add(ENode("neg", (lden,)))
                            queue(cid, eg.add(ENode("add", (lnum, neg_lden))), "log_div_expand")
                    elif sub.op == "mul" and len(sub.children) == 2:
                        left_a, right_b = sub.children
                        if eg.data(left_a).is_positive and eg.data(right_b).is_positive:
                            la = eg.add(ENode("log", (left_a,)))
                            lb = eg.add(ENode("log", (right_b,)))
                            queue(cid, eg.add(ENode("add", (la, lb))), "log_mul_expand")

            elif op == "exp" and len(ch) == 1:
                arg = ch[0]
                for sub in list(eg.classes[eg.find(arg)].nodes):
                    if (
                        sub.op == "log"
                        and len(sub.children) == 1
                        and eg.data(sub.children[0]).is_positive
                    ):
                        queue(cid, sub.children[0], "exp_log")

            elif op == "sqrt" and len(ch) == 1:
                arg = ch[0]
                for sub in list(eg.classes[eg.find(arg)].nodes):
                    if (
                        sub.op == "pow"
                        and len(sub.children) == 2
                        and eg.find(sub.children[1]) == eg.find(two_num)
                    ):
                        base = sub.children[0]
                        if eg.data(base).is_nonnegative:
                            queue(cid, base, "sqrt_sq")
                    elif (
                        sub.op == "mul"
                        and len(sub.children) == 2
                        and eg.find(sub.children[0]) == eg.find(sub.children[1])
                    ):
                        base = sub.children[0]
                        if eg.data(base).is_nonnegative:
                            queue(cid, base, "sqrt_mul_self")

    applied_count = 0
    for lhs, rhs, rule_name in merges:
        if eg.find(lhs) != eg.find(rhs):
            eg.union(lhs, rhs)
            applied_count += 1
            tracker.add(rule_name)
    eg.rebuild()
    return applied_count


def _saturate(
    eg: EGraph,
    target_pairs: Iterable[tuple[int, int]],
    *,
    max_iterations: int = MAX_EGRAPH_ITERATIONS,
) -> tuple[int, list[str]]:
    tracker: set[str] = set()
    pairs = list(target_pairs)
    iterations = 0
    for iteration in range(1, max_iterations + 1):
        if any(eg.find(a) == eg.find(b) for a, b in pairs):
            break
        iterations = iteration
        try:
            changed = _apply_rewrites_once(eg, tracker)
        except _CapacityReached:
            eg.rebuild()
            break
        if changed == 0:
            break
    rules = sorted(
        rule for rule in tracker if re.fullmatch(r"[a-z][a-z0-9_]{0,47}", rule)
    )[:64]
    return iterations, rules


def _collect_nonzero_denominators(eg: EGraph, root_ids: Iterable[int]) -> list[int]:
    """Collect e-class IDs of proved-nonzero commutative divisors for equation rescaling."""
    visited: set[int] = set()
    denominators: dict[int, int] = {}

    def walk(eid: int) -> None:
        r = eg.find(eid)
        if r in visited:
            return
        visited.add(r)
        for node in eg.classes[r].nodes:
            if node.op == "div" and len(node.children) == 2:
                den = eg.find(node.children[1])
                d_data = eg.data(den)
                if d_data.is_nonzero and d_data.is_commutative is True:
                    denominators.setdefault(den, den)
            for child in node.children:
                walk(child)

    for root in root_ids:
        walk(root)
    return list(denominators.values())[:6]


def _witness(eg: EGraph, iterations: int, rules: list[str]) -> dict[str, object]:
    return {
        "outcome": "supported",
        "witness": {
            "method": "egraph_equality_saturation",
            "difference": "0",
            "iterations": iterations,
            "eclasses": len(eg.classes),
            "enodes": eg.total_nodes,
            "rules_applied": rules,
        },
    }


def egraph_check_equivalence(
    ast_a: AstNode,
    ast_b: AstNode,
    *,
    symbol_assumptions: Mapping[str, Mapping[str, object]] | None = None,
    max_iterations: int = MAX_EGRAPH_ITERATIONS,
    max_nodes: int = MAX_EGRAPH_NODES,
) -> dict[str, object] | None:
    """Attempt sound equivalence proof via congruence-closure equality saturation.

    Returns ``{"outcome": "supported", "witness": ...}`` ONLY when ``ast_a`` and
    ``ast_b`` converge into the same e-class (or, for equations ``L1=R1`` and ``L2=R2``,
    their residuals converge after guarded nonzero factor rescaling). Returns ``None``
    otherwise so SymPy handles remaining cases or fail-closed indeterminacy.
    """
    eg = EGraph(max_nodes=max_nodes)
    try:
        if ast_a.kind == "equals" and ast_b.kind == "equals":
            if len(ast_a.children) != 2 or len(ast_b.children) != 2:
                return None
            l1 = add_ast_to_egraph(eg, ast_a.children[0], symbol_assumptions=symbol_assumptions)
            r1 = add_ast_to_egraph(eg, ast_a.children[1], symbol_assumptions=symbol_assumptions)
            l2 = add_ast_to_egraph(eg, ast_b.children[0], symbol_assumptions=symbol_assumptions)
            r2 = add_ast_to_egraph(eg, ast_b.children[1], symbol_assumptions=symbol_assumptions)

            res1 = eg.add(ENode("add", (l1, eg.add(ENode("neg", (r1,))))))
            neg_res1 = eg.add(ENode("neg", (res1,)))
            res2 = eg.add(ENode("add", (l2, eg.add(ENode("neg", (r2,))))))
            neg_res2 = eg.add(ENode("neg", (res2,)))

            targets = [(res1, res2), (res1, neg_res2)]
            transpose_pairs: list[tuple[int, int]] = []
            if (
                eg.data(res1).is_commutative is not True
                or eg.data(res2).is_commutative is not True
            ):
                # Transpose is a linear involution: (L - R)^T = 0 iff L - R = 0.
                t_res1 = eg.add(ENode("transpose", (res1,)))
                transpose_pairs = [(t_res1, res2), (t_res1, neg_res2)]
            denoms = _collect_nonzero_denominators(eg, (l1, r1, l2, r2))
            scaled_pairs: list[tuple[int, int]] = []
            for den in denoms:
                s2 = eg.add(ENode("mul", (den, res2)))
                s1 = eg.add(ENode("mul", (den, res1)))
                scaled_pairs.extend(
                    [(s2, res1), (s2, neg_res1), (s1, res2), (s1, neg_res2)]
                )

            iterations, rules = _saturate(
                eg,
                [*targets, *transpose_pairs, *scaled_pairs],
                max_iterations=max_iterations,
            )
            if any(eg.find(a) == eg.find(b) for a, b in targets):
                return _witness(eg, iterations, rules)
            if any(eg.find(a) == eg.find(b) for a, b in transpose_pairs):
                merged_rules = sorted({*rules, "eq_transpose_both_sides"})[:64]
                return _witness(eg, iterations, merged_rules)
            if any(eg.find(a) == eg.find(b) for a, b in scaled_pairs):
                merged_rules = sorted({*rules, "nonzero_factor_rescale"})[:64]
                return _witness(eg, iterations, merged_rules)
            return None

        if ast_a.kind == "equals" or ast_b.kind == "equals":
            return None

        id_a = add_ast_to_egraph(eg, ast_a, symbol_assumptions=symbol_assumptions)
        id_b = add_ast_to_egraph(eg, ast_b, symbol_assumptions=symbol_assumptions)
        iterations, rules = _saturate(eg, [(id_a, id_b)], max_iterations=max_iterations)
        if eg.find(id_a) == eg.find(id_b):
            return _witness(eg, iterations, rules)
        return None
    except (EGraphUnsupported, _CapacityReached):
        return None
