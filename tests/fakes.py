class FakeLLM:
    """Scripted LLM: returns the queued outputs in order, then None."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.outputs.pop(0) if self.outputs else None
