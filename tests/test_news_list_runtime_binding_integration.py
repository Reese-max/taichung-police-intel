"""Runtime replacement must fail before any fictional transport request."""
from functools import wraps
from unittest import TestCase, mock

from intel_v2 import checkpoint_code_binding as cb
from intel_v2 import news_list_checkpoint as cp
from tests.test_news_list_checkpoint import START, END, fixtures
from tests.test_news_list_collector import FakeSession


class RuntimeBindingIntegrationTests(TestCase):
    def test_source_preserving_row_wrapper_cannot_start_a_checkpoint(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        original = cp.oc.parse_news_list

        @wraps(original)
        def truncated(*args, **kwargs):
            return original(*args, **kwargs)[:1]

        with mock.patch.object(cp.oc, "parse_news_list", truncated):
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, max_list_pages=1)
        self.assertEqual(session.fetched, [])

    def test_source_preserving_row_wrapper_cannot_resume_an_existing_checkpoint(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        checkpoint, _ = cp.collect_batch(session, START, END, max_list_pages=1)
        session.fetched.clear()
        original = cp.oc.parse_news_list

        @wraps(original)
        def truncated(*args, **kwargs):
            return original(*args, **kwargs)[:1]

        with mock.patch.object(cp.oc, "parse_news_list", truncated):
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, checkpoint=checkpoint, max_list_pages=1)
        self.assertEqual(session.fetched, [])
        self.assertEqual(cp.validate_checkpoint(checkpoint, START, END)["pages"], 1)

    def test_wrapped_control_reconstruction_cannot_drop_resume_navigation(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        checkpoint, _ = cp.collect_batch(session, START, END, max_list_pages=1)
        session.fetched.clear()
        original = cp.control_html

        @wraps(original)
        def no_controls(*args, **kwargs):
            return b""

        with mock.patch.object(cp, "control_html", no_controls):
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, checkpoint=checkpoint, max_list_pages=1)
        self.assertEqual(session.fetched, [])

    def test_binding_helper_cannot_replay_cached_hashes_to_start_a_checkpoint(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        cached = cp.binding(START, END)["callable_code_sha256"]
        original = cb.callable_fingerprints

        @wraps(original)
        def cached_hashes(registry):
            return cached

        with mock.patch.object(cb, "callable_fingerprints", cached_hashes):
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, max_list_pages=1)
        self.assertEqual(session.fetched, [])

    def test_binding_helper_cannot_replay_cached_hashes_to_resume_a_checkpoint(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        checkpoint, _ = cp.collect_batch(session, START, END, max_list_pages=1)
        session.fetched.clear()
        cached = checkpoint["binding"]["callable_code_sha256"]
        original = cb.callable_fingerprints

        @wraps(original)
        def cached_hashes(registry):
            return cached

        with mock.patch.object(cb, "callable_fingerprints", cached_hashes):
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, checkpoint=checkpoint, max_list_pages=1)
        self.assertEqual(session.fetched, [])
        self.assertEqual(cp.validate_checkpoint(checkpoint, START, END)["pages"], 1)

    def test_in_place_binding_helper_code_change_cannot_start_a_checkpoint(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        original = cb.callable_fingerprints

        def no_hashes(registry):
            return {}

        # Preserve function identity while replacing the code that would run.
        original_code = original.__code__
        try:
            original.__code__ = no_hashes.__code__
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, max_list_pages=1)
        finally:
            original.__code__ = original_code
        self.assertEqual(session.fetched, [])

    def test_in_place_binding_helper_keyword_default_change_blocks_before_transport(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        with mock.patch.dict(cb._normalize.__kwdefaults__, {"depth": 1}):
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, max_list_pages=1)
        self.assertEqual(session.fetched, [])

    def test_bool_for_integer_helper_default_blocks_before_source_read(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        # Ordinary equality considers False equal to the registered int 0;
        # the bootstrap must reject its changed type before invoking helpers.
        with mock.patch.dict(cb._normalize.__kwdefaults__, {"depth": False}), \
                mock.patch.object(cb.os, "open", wraps=cb.os.open) as source_open:
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, max_list_pages=1)
            source_open.assert_not_called()
        self.assertEqual(session.fetched, [])

    def test_unsupported_equality_default_never_runs_hook_or_source_reader(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        equality_hook = mock.Mock()

        class EqualZero:
            def __eq__(self, other):
                equality_hook(other)
                return other == 0

            def __gt__(self, other):
                return False

            def __add__(self, other):
                return other

        # This fictional object can masquerade as 0 to a loose comparison.
        # Reject its unsupported type without dispatching its equality hook.
        with mock.patch.dict(cb._normalize.__kwdefaults__, {"depth": EqualZero()}), \
                mock.patch.object(cb.os, "open", wraps=cb.os.open) as source_open:
            with self.assertRaisesRegex(ValueError, "binding.*restart"):
                cp.collect_batch(session, START, END, max_list_pages=1)
            equality_hook.assert_not_called()
            source_open.assert_not_called()
        self.assertEqual(session.fetched, [])
