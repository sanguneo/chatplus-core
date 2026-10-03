# Copyright (c) 2026 chatplus-core contributors
"""Property-based proof (PBT-1): decode_frames replays arbitrary op sequences.

Confirms R1 (last-writer-wins) and tombstone semantics from the follow-up plan:
for any sequence of set/delete operations encoded as real Hive frames, the
decoded map equals a pure-Python left-to-right reference fold.
"""

from hypothesis import given
from hypothesis import strategies as st

from chatplus_core.hive import decode_frames
from tests.test_hive import KEY, frame

_keys = st.one_of(st.integers(min_value=0, max_value=1000), st.text(alphabet="ab가🎯_", max_size=8))
_values = st.one_of(st.none(), st.binary(max_size=24))
_ops = st.lists(st.tuples(_keys, _values), max_size=40)


@given(_ops)
def test_decode_frames_matches_reference_fold(
    operations: list[tuple[int | str, bytes | None]],
) -> None:
    reference: dict[int | str, bytes] = {}
    blob = b""
    for key, value in operations:
        blob += frame(key, value)
        if value is None:
            _ = reference.pop(key, None)
        else:
            reference[key] = value
    assert decode_frames(blob, KEY) == reference
