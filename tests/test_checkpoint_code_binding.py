"""Fictional-module regressions for actual runtime/source checkpoint binding."""
from contextlib import contextmanager
import functools
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import FunctionType
import unittest
from unittest import mock

from intel_v2 import checkpoint_code_binding as cb


SOURCE = '''"""Fictional module only."""
def project(value, count=2, *, strict=True):
    """Fictional row projection."""
    def nested(item):
        return item + 1
    return [nested(item) for item in value[:count]] if strict else list(value)
'''


@contextmanager
def fictional_module(source=SOURCE, *, name="fictional_checkpoint_module"):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fictional.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        # Compile directly so this fixture never relies on stale timestamp pyc.
        previous = sys.modules.get(name)
        sys.modules[name] = module
        try:
            exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
            yield module, path
        finally:
            if previous is None:
                del sys.modules[name]
            else:
                sys.modules[name] = previous


class CodeBindingTests(unittest.TestCase):
    def fingerprint(self, module, path):
        return cb.callable_fingerprint(module.project, path, expected_name="project")

    def test_plain_module_function_is_stable_and_explicit_registry_works(self):
        with fictional_module() as (module, path):
            first = self.fingerprint(module, path)
            self.assertRegex(first, r"^[0-9a-f]{64}$")
            self.assertEqual(first, self.fingerprint(module, path))
            self.assertEqual(cb.callable_fingerprints({"row_parser": (module.project, path, "project")}),
                             {"row_parser": first})

    def test_functools_wraps_cannot_hide_runtime_truncation(self):
        with fictional_module() as (module, path):
            original = module.project
            wrapped = functools.wraps(original)(lambda *args, **kwargs: original(*args, **kwargs)[:1])
            module.project = wrapped
            # Function-valued closure cells also fail closed rather than being
            # represented by an address-dependent repr or unwrap operation.
            with self.assertRaises(ValueError):
                cb.runtime_code_fingerprint(wrapped)
            self.assertEqual(original([1, 2]), [2, 3])
            self.assertEqual(wrapped([1, 2]), [2])
            with self.assertRaisesRegex(ValueError, "binding changed.*restart"):
                self.fingerprint(module, path)
            module.project = original
            self.assertEqual(self.fingerprint(module, path), cb.runtime_code_fingerprint(original))

    def test_loaded_old_code_rejected_after_same_length_disk_edit(self):
        with fictional_module() as (module, path):
            before = self.fingerprint(module, path)
            changed = SOURCE.replace("item + 1", "item + 2")
            self.assertEqual(len(changed), len(SOURCE))
            path.write_text(changed, encoding="utf-8")
            self.assertEqual(module.project([1]), [2])
            with self.assertRaisesRegex(ValueError, "binding changed.*restart"):
                self.fingerprint(module, path)
            exec(compile(changed, str(path), "exec", dont_inherit=True), module.__dict__)
            self.assertEqual(module.project([1]), [3])
            self.assertNotEqual(before, self.fingerprint(module, path))

    def test_nested_runtime_code_change_is_fingerprinted_and_rejected(self):
        with fictional_module() as (module, path):
            original = module.project.__code__
            nested = next(value for value in original.co_consts if isinstance(value, type(original)) and value.co_name == "nested")
            altered = nested.replace(co_consts=tuple(2 if type(value) is int and value == 1 else value for value in nested.co_consts))
            before = cb.runtime_code_fingerprint(module.project)
            module.project.__code__ = original.replace(co_consts=tuple(altered if value is nested else value for value in original.co_consts))
            self.assertEqual(module.project([1]), [3])
            self.assertNotEqual(before, cb.runtime_code_fingerprint(module.project))
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_positional_default_mutation_cannot_start_new_binding(self):
        with fictional_module() as (module, path):
            before = cb.runtime_code_fingerprint(module.project)
            module.project.__defaults__ = (1,)
            self.assertNotEqual(before, cb.runtime_code_fingerprint(module.project))
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_keyword_default_mutation_cannot_start_new_binding(self):
        with fictional_module() as (module, path):
            before = cb.runtime_code_fingerprint(module.project)
            module.project.__kwdefaults__["strict"] = False
            self.assertNotEqual(before, cb.runtime_code_fingerprint(module.project))
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_loaded_old_defaults_rejected_when_only_source_default_changes(self):
        with fictional_module() as (module, path):
            path.write_text(SOURCE.replace("count=2", "count=1"), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_mutable_literal_default_change_is_detected(self):
        with fictional_module("def project(value=[]):\n    return value\n") as (module, path):
            self.fingerprint(module, path)
            module.project.__defaults__[0].append("fictional")
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_computed_source_defaults_fail_closed_without_execution(self):
        with fictional_module("def project(value=None):\n    return value\n") as (module, path):
            path.write_text("def project(value=forbidden_side_effect()):\n    return value\n", encoding="utf-8")
            module.forbidden_side_effect = mock.Mock(side_effect=AssertionError("must not execute"))
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)
            module.forbidden_side_effect.assert_not_called()

    def test_current_source_is_compiled_without_top_level_execution(self):
        with fictional_module() as (module, path):
            expected = self.fingerprint(module, path)
            module.forbidden_side_effect = mock.Mock(side_effect=AssertionError("must not execute module"))
            path.write_text('forbidden_side_effect()\n' + SOURCE, encoding="utf-8")
            self.assertEqual(expected, self.fingerprint(module, path))
            module.forbidden_side_effect.assert_not_called()

    def test_paths_and_source_line_positions_are_not_semantic_hash_inputs(self):
        with fictional_module(name="fictional_root_a") as (first, first_path):
            with fictional_module("\n\n" + SOURCE, name="fictional_root_b") as (second, second_path):
                self.assertNotEqual(first_path, second_path)
                self.assertNotEqual(first.project.__code__.co_firstlineno, second.project.__code__.co_firstlineno)
                self.assertEqual(self.fingerprint(first, first_path), self.fingerprint(second, second_path))

    def test_docstring_and_nested_constants_are_included(self):
        with fictional_module(name="fictional_root_a") as (first, first_path):
            with fictional_module(SOURCE.replace("Fictional row projection", "Changed row projection"), name="fictional_root_b") as (second, second_path):
                self.assertNotEqual(self.fingerprint(first, first_path), self.fingerprint(second, second_path))

    def test_closure_cells_change_raw_runtime_fingerprint(self):
        def factory(offset):
            def projected(value):
                return value + offset
            return projected
        first, second = factory(1), factory(2)
        self.assertNotEqual(cb.runtime_code_fingerprint(first), cb.runtime_code_fingerprint(second))
        self.assertEqual(cb.runtime_code_fingerprint(first), cb.runtime_code_fingerprint(factory(1)))
        with self.assertRaises(ValueError):
            cb.callable_fingerprint(first, __file__, expected_name="projected")

    def test_wrong_expected_module_path_and_function_name_are_rejected(self):
        with fictional_module() as (module, path):
            with self.assertRaises(ValueError):
                cb.callable_fingerprint(module.project, Path(__file__), expected_name="project")
            with self.assertRaises(ValueError):
                cb.callable_fingerprint(module.project, path, expected_name="other")

    def test_forged_module_attribute_does_not_hide_foreign_globals(self):
        with fictional_module() as (module, path):
            foreign_globals = {"__name__": "fictional_foreign", "__file__": str(path)}
            replacement = FunctionType(module.project.__code__, foreign_globals, "project", module.project.__defaults__)
            replacement.__module__ = module.__name__
            replacement.__kwdefaults__ = module.project.__kwdefaults__.copy()
            foreign_globals["project"] = replacement
            module.project = replacement
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_reassigned_module_global_invalidates_even_original_reference(self):
        with fictional_module() as (module, path):
            original = module.project
            module.project = lambda value: value
            with self.assertRaises(ValueError):
                cb.callable_fingerprint(original, path, expected_name="project")

    def test_matching_forged_module_name_and_path_still_reject_foreign_globals(self):
        with fictional_module() as (module, path):
            foreign_globals = module.__dict__.copy()
            replacement = FunctionType(module.project.__code__, foreign_globals, "project", module.project.__defaults__)
            replacement.__kwdefaults__ = module.project.__kwdefaults__.copy()
            foreign_globals["project"] = replacement
            module.project = replacement
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_uninspectable_callable_and_unsupported_default_objects_fail_closed(self):
        with self.assertRaises(ValueError):
            cb.callable_fingerprint(len, __file__, expected_name="len")
        with fictional_module() as (module, path):
            module.project.__defaults__ = (object(),)
            with self.assertRaises(ValueError):
                cb.runtime_code_fingerprint(module.project)
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_cyclic_default_fails_closed_without_recursion_error(self):
        with fictional_module() as (module, path):
            cycle = []
            cycle.append(cycle)
            module.project.__defaults__ = (cycle,)
            with self.assertRaises(ValueError):
                cb.runtime_code_fingerprint(module.project)

    def test_typed_constants_and_unordered_defaults_normalize_stably(self):
        with fictional_module() as (module, path):
            module.project.__kwdefaults__ = {"strict": {"b": {2, 1}, "a": True}}
            first = cb.runtime_code_fingerprint(module.project)
            module.project.__kwdefaults__ = {"strict": {"a": True, "b": {1, 2}}}
            self.assertEqual(first, cb.runtime_code_fingerprint(module.project))
            module.project.__kwdefaults__["strict"]["a"] = 1
            self.assertNotEqual(first, cb.runtime_code_fingerprint(module.project))

    def test_registry_compiles_each_file_once_and_rereads_on_every_call(self):
        source = SOURCE + "\ndef helper(value):\n    return value\n"
        with fictional_module(source) as (module, path):
            registry = {"row": (module.project, path, "project"), "helper": (module.helper, path, "helper")}
            with mock.patch.object(cb, "_compiled_source", wraps=cb._compiled_source) as compiler:
                cb.callable_fingerprints(registry)
                self.assertEqual(compiler.call_count, 1)
                cb.callable_fingerprints(registry)
                self.assertEqual(compiler.call_count, 2)
            path.write_text(source.replace("return value\n", "return None\n"), encoding="utf-8")
            with self.assertRaises(ValueError):
                cb.callable_fingerprints(registry)

    def test_syntax_error_missing_source_and_oversized_source_fail_closed(self):
        with fictional_module() as (module, path):
            for content in ("not valid python syntax !!!", "#" + "x" * cb.MAX_SOURCE_BYTES):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.fingerprint(module, path)
            path.unlink()
            with self.assertRaises(ValueError):
                self.fingerprint(module, path)

    def test_binding_helpers_themselves_match_their_compiled_current_source(self):
        path = Path(cb.__file__)
        functions = {name: (value, path, name) for name, value in vars(cb).items()
                     if type(value) is FunctionType and value.__module__ == cb.__name__}
        fingerprints = cb.callable_fingerprints(functions)
        self.assertEqual(set(fingerprints), set(functions))
        self.assertIn("callable_fingerprints", fingerprints)


if __name__ == "__main__":
    unittest.main()
