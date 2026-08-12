"""Names that are used and never defined.

This exists because of a NameError that reached the live system. A clip
was being cut at the render profile's height, and the line read
`render_height(profile_id, ...)` inside a helper that has no `profile_id`
- it is a local of the caller. Python compiles that happily. It fails
the moment the branch runs, which needed a trip that actually has video,
which no test here has.

Every other check in this repository reads sources as TEXT: does this
string appear, do these two constants agree. That catches a great deal
and cannot catch this. `compile()` does not either - an undefined global
is a runtime error by design, because it might be defined by then.

So this walks the syntax tree of every module and asks the one question
those cannot: is every name that gets LOADED reachable from somewhere -
a local, a parameter, a global, an import, a builtin, a comprehension
variable, a class attribute in scope?

Deliberately conservative. Anything it cannot resolve confidently is
left alone: the value of this check is that a failure means a real bug,
not that it finds every possible one.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "roadplanner_mcp"

BUILTINS = set(dir(builtins)) | {
    "__file__",
    "__name__",
    "__doc__",
    "__package__",
    "__path__",
    "__spec__",
    "__loader__",
    "__builtins__",
    "__class__",
}


def _direct(node: ast.AST):
    """Every node of this scope, WITHOUT descending into nested ones.

    The first version of this walked nested functions too, and then
    checked their names a second time against the wrong scope - so it
    reported a closure's use of its enclosing parameter as undefined.
    A checker that cries wolf is worse than none; this one only looks at
    what a scope itself contains.
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _binds(node: ast.AST) -> set[str]:
    """Names this scope binds, nested scopes excluded."""
    names: set[str] = set()
    for child in _direct(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            names.add(child.id)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            names.update(child.names)
        elif isinstance(child, ast.arg):
            names.add(child.arg)
    return names


def _arguments(node: ast.AST) -> set[str]:
    args = getattr(node, "args", None)
    if args is None:
        return set()
    found = {
        argument.arg
        for group in (args.posonlyargs, args.args, args.kwonlyargs)
        for argument in group
    }
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            found.add(extra.arg)
    return found


def _scope(node: ast.AST, outer: set[str], problems: list[str], where: str) -> None:
    """One function body, with exactly the names visible inside it."""
    visible = set(outer) | _arguments(node) | _binds(node)
    for child in _direct(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            if child.id not in visible and child.id not in BUILTINS:
                problems.append(
                    f"{where}:{child.lineno}: {child.id!r} wird benutzt, ist aber "
                    "in keinem sichtbaren Gültigkeitsbereich definiert"
                )
        # A nested scope sees everything this one does, plus its own.
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            _scope(child, visible, problems, where)
        elif isinstance(child, ast.ClassDef):
            for member in child.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # A method does not see its class body's names as
                    # bare names - that is what `self.` is for.
                    _scope(member, visible, problems, where)


def verify_every_name_used_is_reachable() -> None:
    """The check that would have caught `profile_id` before the release."""
    problems: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Module scope is visible everywhere below it and a module's own
        # names are bound wherever they appear in it - but only at the TOP
        # level. The first version collected them with a full walk, which
        # swept every local variable in the file into module scope, and
        # then nothing could ever be undefined. It passed on the exact
        # NameError it was written for.
        module_level = _binds(tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _scope(node, module_level, problems, path.name)
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        _scope(member, module_level, problems, path.name)
    assert not problems, "\n".join(sorted(set(problems)))


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Undefined name tests passed.")


if __name__ == "__main__":
    main()
