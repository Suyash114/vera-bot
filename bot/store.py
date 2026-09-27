"""In-memory state for one judge run: versioned contexts, sends, conversations.

Everything lives in process memory, so the server must run with a single
worker (see Dockerfile).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

SCOPES = ("category", "merchant", "customer", "trigger")
REPEAT_MIN_CHARS = 40  # short replies ("yes", "ok") repeat naturally; only long texts signal a canned reply


@dataclass
class Conversation:
    cid: str
    merchant_id: str | None
    customer_id: str | None
    trigger_id: str | None = None
    bodies: list[str] = field(default_factory=list)  # what the bot sent (anti-repetition)
    ended: bool = False


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._contexts: dict[tuple[str, str], tuple[int, dict]] = {}
            self.sent_keys: set[str] = set()
            self._conversations: dict[str, Conversation] = {}
            self._suppressed: set[str] = set()
            # merchant_id -> long inbound texts seen, across conversations (auto-reply repeats)
            self._long_inbound: dict[str, set[str]] = {}
            # merchant_id -> auto-replies seen, across conversations
            self._auto_replies: dict[str, int] = {}

    # ---- contexts -------------------------------------------------------
    def put(self, scope: str, cid: str, version: int, payload: dict) -> tuple[int, dict]:
        if scope not in SCOPES:
            return 400, {"accepted": False, "reason": "invalid_scope",
                         "details": f"scope must be one of {list(SCOPES)}"}
        with self._lock:
            current = self._contexts.get((scope, cid))
            if current and version < current[0]:
                return 409, {"accepted": False, "reason": "stale_version", "current_version": current[0]}
            if not current or version > current[0]:
                self._contexts[(scope, cid)] = (version, payload)
        return 200, {"accepted": True, "ack_id": f"ack_{scope}_{cid}_v{version}",
                     "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}

    def get(self, scope: str, cid: str | None) -> dict | None:
        if not cid:
            return None
        entry = self._contexts.get((scope, cid))
        return entry[1] if entry else None

    def items(self, scope: str) -> list[dict]:
        return [payload for (s, _), (_, payload) in list(self._contexts.items()) if s == scope]

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(SCOPES, 0)
        for scope, _ in list(self._contexts):
            counts[scope] += 1
        return counts

    # ---- conversations --------------------------------------------------
    def has_conversation(self, cid: str) -> bool:
        return cid in self._conversations

    def open_conversation(self, cid: str, merchant_id: str | None, customer_id: str | None,
                          trigger_id: str | None, body: str) -> Conversation:
        with self._lock:
            conv = Conversation(cid, merchant_id, customer_id, trigger_id)
            conv.bodies.append(body)
            self._conversations[cid] = conv
            return conv

    def forget_conversation(self, cid: str) -> None:
        with self._lock:
            self._conversations.pop(cid, None)

    def conversation(self, cid: str, merchant_id: str | None, customer_id: str | None) -> Conversation:
        with self._lock:
            conv = self._conversations.get(cid)
            if conv is None:
                conv = self._conversations[cid] = Conversation(cid, merchant_id, customer_id)
            return conv

    # ---- atomic check-and-act (ticks and replies run on a thread pool) ----
    def claim(self, suppression_key: str) -> bool:
        """Reserve a send; False if another tick already has it."""
        with self._lock:
            if suppression_key in self.sent_keys:
                return False
            self.sent_keys.add(suppression_key)
            return True

    def release(self, suppression_key: str) -> None:
        with self._lock:
            self.sent_keys.discard(suppression_key)

    def record_inbound(self, merchant_id: str, message: str) -> bool:
        """Remember a long inbound message; True if this merchant already sent it verbatim."""
        if len(message.strip()) < REPEAT_MIN_CHARS:
            return False
        with self._lock:
            seen = self._long_inbound.setdefault(merchant_id, set())
            repeat = message.strip() in seen
            seen.add(message.strip())
            return repeat

    def bump_auto_reply(self, merchant_id: str) -> int:
        with self._lock:
            n = self._auto_replies[merchant_id] = self._auto_replies.get(merchant_id, 0) + 1
            return n

    def reset_auto_replies(self, merchant_id: str) -> None:
        with self._lock:
            self._auto_replies.pop(merchant_id, None)

    # ---- opt-out, scoped to the recipient who asked -----------------------
    # A merchant opt-out blocks everything for that merchant; a customer opt-out
    # blocks only sends to that customer.
    def suppress(self, merchant_id: str | None, customer_id: str | None = None) -> None:
        self._suppressed.add((merchant_id, customer_id))

    def unsuppress(self, merchant_id: str | None, customer_id: str | None = None) -> None:
        self._suppressed.discard((merchant_id, customer_id))

    def is_suppressed(self, merchant_id: str | None, customer_id: str | None = None) -> bool:
        return (merchant_id, None) in self._suppressed or \
            (customer_id is not None and (merchant_id, customer_id) in self._suppressed)

    def suppress_merchant(self, merchant_id: str) -> None:
        self.suppress(merchant_id)
