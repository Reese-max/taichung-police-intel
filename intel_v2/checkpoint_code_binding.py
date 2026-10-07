"""Bind registered Python callables to their loaded semantics and current source.

This is a local consistency check, not an integrity boundary against arbitrary
changes to the interpreter, third-party libraries, or unregistered globals.
Source is compiled and parsed only; it is never executed by this module.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
from types import CodeType, FunctionType


ERROR = "parser code binding changed; restart from the official entrypoint"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_VALUE_DEPTH = 64


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _normalize(value, *, seen=None, depth=0):
    """Encode supported values without repr(), custom hooks, or path metadata."""
    if depth > MAX_VALUE_DEPTH:
        raise ValueError(ERROR)
    if seen is None:
        seen = set()
    kind = type(value)
    if value is None:
        return ["none"]
    if value is Ellipsis:
        return ["ellipsis"]
    if kind is bool:
        return ["bool", value]
    if kind is int:
        return ["int", str(value)]
    if kind is float:
        return ["float", struct.pack(">d", value).hex()]
    if kind is complex:
        return ["complex", struct.pack(">d", value.real).hex(), struct.pack(">d", value.imag).hex()]
    if kind is str:
        return ["str", value]
    if kind is bytes:
        return ["bytes", value.hex()]
    if kind not in (tuple, list, dict, set, frozenset, slice, CodeType):
        raise ValueError(ERROR)
    identity = id(value)
    if identity in seen:
        raise ValueError(ERROR)
    seen.add(identity)
    try:
        if kind is CodeType:
            return ["code", _code_payload(value, seen=seen, depth=depth + 1)]
        if kind is slice:
            return ["slice", _normalize(value.start, seen=seen, depth=depth + 1),
                    _normalize(value.stop, seen=seen, depth=depth + 1),
                    _normalize(value.step, seen=seen, depth=depth + 1)]
        if kind is dict:
            pairs = [[_normalize(key, seen=seen, depth=depth + 1),
                      _normalize(item, seen=seen, depth=depth + 1)] for key, item in value.items()]
            return ["dict", sorted(pairs, key=lambda pair: _encoded(pair[0]))]
        items = [_normalize(item, seen=seen, depth=depth + 1) for item in value]
        if kind in (set, frozenset):
            items.sort(key=_encoded)
        return [kind.__name__, items]
    finally:
        seen.remove(identity)


def _code_payload(code, *, seen=None, depth=0):
    """Include execution structure; omit source paths and line positions."""
    if type(code) is not CodeType:
        raise ValueError(ERROR)
    return {
        "code": code.co_code.hex(),
        "constants": _normalize(code.co_consts, seen=seen, depth=depth + 1),
        "names": list(code.co_names), "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars), "cellvars": list(code.co_cellvars),
        "name": code.co_name, "qualname": getattr(code, "co_qualname", code.co_name),
        "argcount": code.co_argcount, "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount, "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize, "flags": code.co_flags,
        "exceptiontable": getattr(code, "co_exceptiontable", b"").hex(),
    }


def _function_payload(function):
    if type(function) is not FunctionType:
        raise ValueError(ERROR)
    try:
        closure = tuple(cell.cell_contents for cell in function.__closure__) if function.__closure__ else None
    except ValueError as error:
        raise ValueError(ERROR) from error
    return {
        "runtime": {"implementation": sys.implementation.name,
                    "cache_tag": sys.implementation.cache_tag,
                    "version": list(sys.version_info[:3]), "optimize": sys.flags.optimize},
        "code": _code_payload(function.__code__),
        "defaults": _normalize(function.__defaults__),
        "kwdefaults": _normalize(function.__kwdefaults__),
        "closure": _normalize(closure),
    }


def runtime_code_fingerprint(function):
    """Fingerprint actual code/defaults/closure values, without source unwrapping."""
    return hashlib.sha256(_encoded(_function_payload(function))).hexdigest()


def read_source_bounded(path):
    """Reject nonregular/oversized sources before reading; bound file growth."""
    descriptor = None
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 <= metadata.st_size <= MAX_SOURCE_BYTES:
            raise ValueError(ERROR)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None  # The stream owns and closes it from here.
            source = stream.read(MAX_SOURCE_BYTES + 1)
        if len(source) > MAX_SOURCE_BYTES:
            raise ValueError(ERROR)
        return source
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(ERROR) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _compiled_source(path):
    try:
        source = read_source_bounded(path)
        tree = ast.parse(source, filename=str(path))
        compiled = compile(source, str(path), "exec", dont_inherit=True, optimize=sys.flags.optimize)
    except (OSError, SyntaxError, TypeError, ValueError) as error:
        raise ValueError(ERROR) from error
    return tree, compiled


def _assert_source_coherent(function, path, name, tree, compiled):
    if type(function) is not FunctionType or hasattr(function, "__wrapped__"):
        raise ValueError(ERROR)
    if type(name) is not str or not name.isidentifier():
        raise ValueError(ERROR)
    if (function.__name__ != name or function.__qualname__ != name or function.__closure__ is not None
            or function.__module__ != function.__globals__.get("__name__")
            or function.__globals__.get(name) is not function):
        raise ValueError(ERROR)
    module = sys.modules.get(function.__module__)
    if module is None or vars(module) is not function.__globals__:
        raise ValueError(ERROR)
    try:
        if (Path(function.__globals__["__file__"]).resolve() != path
                or Path(function.__code__.co_filename).resolve() != path):
            raise ValueError(ERROR)
    except (KeyError, TypeError, OSError, ValueError) as error:
        raise ValueError(ERROR) from error
    definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    codes = [value for value in compiled.co_consts if type(value) is CodeType and value.co_name == name]
    if len(definitions) != 1 or len(codes) != 1 or definitions[0].decorator_list:
        raise ValueError(ERROR)
    if _code_payload(function.__code__) != _code_payload(codes[0]):
        raise ValueError(ERROR)
    # All registered continuation helpers have literal defaults. Evaluating a
    # computed source default could execute code, so that contract fails closed.
    arguments = definitions[0].args
    try:
        defaults = tuple(ast.literal_eval(value) for value in arguments.defaults) or None
        kwdefaults = {arg.arg: ast.literal_eval(value) for arg, value in zip(arguments.kwonlyargs, arguments.kw_defaults)
                      if value is not None} or None
    except (ValueError, TypeError, SyntaxError) as error:
        raise ValueError(ERROR) from error
    if (_normalize(function.__defaults__) != _normalize(defaults)
            or _normalize(function.__kwdefaults__) != _normalize(kwdefaults)):
        raise ValueError(ERROR)


def callable_fingerprints(registry):
    """Return alias hashes for (function, expected_path, expected_name) entries.

    Each current file is read/compiled once per call. Only plain module-level
    functions whose loaded code and literal defaults match that file qualify.
    The caller must explicitly register any additional runtime helpers/globals
    that belong to its contract; third-party dependencies are not implied.
    """
    if type(registry) is not dict:
        raise ValueError(ERROR)
    compiled_files = {}
    result = {}
    for alias, entry in registry.items():
        if type(alias) is not str or type(entry) not in (tuple, list) or len(entry) != 3:
            raise ValueError(ERROR)
        function, expected_path, name = entry
        try:
            path = Path(expected_path).resolve()
        except (TypeError, OSError, ValueError) as error:
            raise ValueError(ERROR) from error
        if path not in compiled_files:
            compiled_files[path] = _compiled_source(path)
        _assert_source_coherent(function, path, name, *compiled_files[path])
        result[alias] = runtime_code_fingerprint(function)
    return result


def callable_fingerprint(function, expected_path, *, expected_name=None):
    """Single-callable form of callable_fingerprints()."""
    name = function.__name__ if type(function) is FunctionType and expected_name is None else expected_name
    return callable_fingerprints({"function": (function, expected_path, name)})["function"]
