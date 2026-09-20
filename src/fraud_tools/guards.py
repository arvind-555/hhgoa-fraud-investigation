"""Errors and the as-of leak check applied to every tool result."""
import re


class ToolError(Exception):
    """A tool call failed (bad id, unknown entity, query error). Never carries secrets."""


class FutureTransactionError(ToolError):
    """The referenced transaction is not visible at the session's as_of (it does not exist yet)."""


class LeakError(Exception):
    """A result contained information later than as_of. The result is discarded; this is a build/query bug, not a user error."""


_EPOCH_KEY = re.compile(r"(^|_)epoch($|_)|^(own_first|own_last|first_seen|last_seen|max_epoch_seen)$")


def assert_visible(payload, as_of, path="result"):
    """Recursively verify no epoch-valued field exceeds as_of (keys 'as_of'/'as_of_epoch' are exempt).

    Visibility of a closed case is governed by close_epoch; an open_epoch above as_of would be impossible for a visible case, so it is checked too."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            k = str(k)                      # histogram dicts carry int keys
            if k in ("as_of", "as_of_epoch"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool) and _EPOCH_KEY.search(k) and v > as_of:
                raise LeakError(f"{path}.{k} = {v} > as_of {as_of}")
            assert_visible(v, as_of, f"{path}.{k}")
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            assert_visible(v, as_of, f"{path}[{i}]")
