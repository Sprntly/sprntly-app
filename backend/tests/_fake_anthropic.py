"""Shared fake streaming client primitives for Design Agent tests."""


class _FakeStream:
    """Context manager wrapping a pre-recorded Anthropic message.

    Satisfies the `with client.messages.stream(**kw) as s: ... s.get_final_message()`
    protocol in runner.py. Event iteration is a no-op — progress steps are advisory.
    """

    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self._msg
