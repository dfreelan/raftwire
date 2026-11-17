"""Typed exception hierarchy for raftwire."""

from __future__ import annotations


class RaftwireError(Exception):
    """Base class for all raftwire errors."""

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)


class CodecError(RaftwireError):
    """Binary or JSONL encode/decode failure."""


class TruncatedError(CodecError):
    """Input ended before a complete record was available."""

    def __init__(self, expected: int, got: int, what: str = "record") -> None:
        self.expected = expected
        self.got = got
        self.what = what
        super().__init__(
            f"truncated {what}: need {expected} bytes, got {got}"
        )


class MagicError(CodecError):
    """Unexpected magic bytes in a binary record."""

    def __init__(self, expected: int, got: int) -> None:
        self.expected = expected
        self.got = got
        super().__init__(
            f"bad magic: expected 0x{expected:04X}, got 0x{got:04X}"
        )


class VersionError(CodecError):
    """Unsupported record version."""

    def __init__(self, version: int, supported: tuple[int, ...] = (1,)) -> None:
        self.version = version
        self.supported = supported
        super().__init__(
            f"unsupported version {version}; supported={supported}"
        )


class ChecksumError(RaftwireError):
    """CRC mismatch — data is corrupt or truncated."""

    def __init__(
        self,
        expected: int,
        actual: int,
        *,
        context: str = "",
    ) -> None:
        self.expected = expected
        self.actual = actual
        self.context = context
        loc = f" ({context})" if context else ""
        super().__init__(
            f"checksum mismatch{loc}: expected 0x{expected:08X}, "
            f"got 0x{actual:08X}"
        )


class FrameError(RaftwireError):
    """Framing protocol error (length, flags, or stream state)."""


class FrameTooLargeError(FrameError):
    """Declared frame payload exceeds the configured maximum."""

    def __init__(self, length: int, maximum: int) -> None:
        self.length = length
        self.maximum = maximum
        super().__init__(
            f"frame length {length} exceeds maximum {maximum}"
        )


class IncompleteFrameError(FrameError):
    """Not enough bytes buffered to complete a frame."""

    def __init__(self, need: int, have: int) -> None:
        self.need = need
        self.have = have
        super().__init__(f"incomplete frame: need {need} bytes, have {have}")


class LogStoreError(RaftwireError):
    """Log store operation failed."""


class IndexError_(LogStoreError):
    """Requested log index is out of range or invalid.

    Named with a trailing underscore to avoid shadowing the builtin.
    """

    def __init__(self, index: int, first: int, last: int) -> None:
        self.index = index
        self.first = first
        self.last = last
        super().__init__(
            f"index {index} out of range [{first}, {last}]"
        )


class InvariantError(LogStoreError):
    """A log store invariant was violated."""

    def __init__(self, invariant: str, detail: str = "") -> None:
        self.invariant = invariant
        self.detail = detail
        msg = f"invariant violated: {invariant}"
        if detail:
            msg = f"{msg} — {detail}"
        super().__init__(msg)


class CompactError(RaftwireError):
    """Compaction or truncation could not proceed."""


class SnapshotError(CompactError):
    """Snapshot install or metadata conflict."""


class ReplayError(RaftwireError):
    """Deterministic replay failed (gap, checksum, or apply error)."""

    def __init__(self, message: str, *, index: int | None = None) -> None:
        self.index = index
        if index is not None:
            message = f"at index {index}: {message}"
        super().__init__(message)


class RpcError(RaftwireError):
    """RPC message encode/decode or validation error."""


class CliError(RaftwireError):
    """CLI usage or I/O error (mapped to non-zero exit)."""


# Public re-export alias so callers can write ``raftwire.errors.IndexError``
# without colliding with the builtin in ``from raftwire.errors import *``.
IndexError = IndexError_

__all__ = [
    "RaftwireError",
    "CodecError",
    "TruncatedError",
    "MagicError",
    "VersionError",
    "ChecksumError",
    "FrameError",
    "FrameTooLargeError",
    "IncompleteFrameError",
    "LogStoreError",
    "IndexError_",
    "IndexError",
    "InvariantError",
    "CompactError",
    "SnapshotError",
    "ReplayError",
    "RpcError",
    "CliError",
]
