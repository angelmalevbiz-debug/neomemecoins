# Bounded observation recording, without erasing evidence

`PAPER_OBSERVATION_PARTS_V1` repairs a recorder failure, not trading expectancy.
The local Windows runtime's 40,404,611,065-byte `observations.jsonl` rejected
all further writes. Both Python text/binary appends and an explicit positioned
Win32 `WriteFile` failed without writing bytes; Win32 reported error **665**,
"file system limitation". Opening, reading, seeking and syncing an empty
write succeeded. Disk free space was not exhausted. We have not established
the precise underlying NTFS fragmentation cause and do not repair the volume.

Future observations rotate at a 128 MiB batch boundary into numbered
`observations.part-000001.jsonl` files. The existing `observations.jsonl` remains
verbatim and readable; it is never copied, deleted, truncated, or repriced by
this repair. `observations.jsonl.parts.json` atomically commits their order and
sealed byte lengths before the next file receives data. No part is pruned.
One writer owns the journal. Multiple writers are not supported.

The learner reads the parts as one logical byte stream. Its existing
`recording_start_offset` remains valid: no replay reset, new balance, erased
gap, or inferred observation is needed. Archived gaps continue to block
candidate promotion. A successful new write does not retroactively fill them.
The producer reports part count, logical bytes and part bound under
`paper_training.recorder.journal`; `processed` must actually increase.

Missing/changed sealed files, unsafe names, incompatible manifests, nonempty
uncommitted next parts, and incomplete tails fail closed. Partial active final
records keep their prior consumer cursor and are retried. Crash-before-manifest
may leave an empty next file, which is safe to reuse. Unreadable manifests are
never replaced with an empty recording. Keep every part and the manifest when
copying a recording, with its writer stopped. Existing explicit archive/reset/
restore commands include them together; this deployment performs **no reset**.

The ticker-seed builder and PAPER import/replay tools understand the logical
stream when given the original `observations.jsonl` path. Do not pass only one
continuation part for complete-history analysis. Dataset hashes cover the
concatenated raw bytes, not only the legacy prefix. The existing import tool
still loads/sorts rows in memory; do not use it unbounded on a 40 GB recording.

Regression tests cover legacy cursor continuity, cross-part reads/seeks,
Unicode/CRLF, checkpoint resume, exactly-once repeated polling, incomplete
tails, manifest publication failure, unsafe/missing/modified files, bounded
rotation, and complete archive/restore. These are storage tests, not proof of
new trades, 50 entries/hour, a higher win rate, or profit after modeled fees.
