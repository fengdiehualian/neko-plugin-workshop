# 0.5.53 canonical recovery dependency alignment

This internal candidate pins canonical autonomy **0.1.77** at
`ed94de1277ff0bf61182733374ec8ac8db7e8413`, the reviewed final recovery fix2.
The Git dependency uses that exact commit, not a version-only selection of the
superseded original or fix1 .77 artifacts. The reviewed final wheel SHA256 is
`2dfe41ebaaceac09fcee35d30785e1a75b7f204f9e087adc1889bb46db75d48a`;
sdist SHA256 is `b85dfa2af51bc7e48c5cdef1858be0daf52de194429a57b7711ae14994dd8e4b`.
Git's committed LF text, the normal Windows Git dependency checkout's CRLF text
and the reviewed wheel's mixed working line endings have different raw hashes.
All candidate runtime raw hashes are recorded against both committed source and
reviewed wheel; non-core differences are proved to be CRLF-only, without
rewriting candidate files. The 12 core distribution members remain byte-exact
across source, dependency and reviewed wheel. This does not relax the strict core
source/build/archive hashes.

The checked engine stays **0.5.7-dev.20261016.39**, source
`3ee62ca25b60693132f7b7b8ae6bac3df7ad452f`, archive SHA256
`44c161edb81aad330aef57933716b31d329eb11506d6ba8e0b6c322f57285b56`.
Packaged default viewer c799ae1a and the operator's configured 333d viewer remain
separate. Existing checked core and viewer receipts are reused; this alignment
does not rebuild, install or switch either component.

Canonical .77 adds bounded private first-error diagnostics, retaining the first
exception when cleanup also fails. Public status carries only a diagnostic ID
and availability. This improves evidence; it does not identify or cure the
original PermissionError's unknown denied operation.

Its explicit offline operator assessment retains accepted-only old native work
as **unknown**. Acknowledgement requires identity/session/run bindings, state
and status digest CAS, the genuine owner lock, external prior-process exit
attestation and durable prepared/committed audit. Missing or incomplete audit
remains a pending-start fence. A successful acknowledgement keeps stopped=true,
does not replay the old request and does not start a body or verify an old goal.
Recovery is a canonical operator CLI action, never an LLM/plugin tool, adapter
startup action, timer, UI refresh or normal inspect operation.

The adapter's public API, external-runtime/passive observer behavior,
recent-context producer filtering, profile lifetimes, speech admission, native
queue ownership, configured online voice and disabled automatic startup defaults
are unchanged. Their existing contracts remain documented in
[the .52 integration note](integration-0.5.52-20261016.md).

Deployment is separate: first official vendor sync, then a normal registered .53
adapter reload while the failed body remains stopped/fenced. Every possible
starter must recognize .77's pending receipt gate before the separately reviewed
operator acknowledgement; only afterward may a separate normal .77 body run be
started. This source/package task performs none of those live actions. Offline
SDK, dependency and package checks do not establish old task9's outcome, actual
acknowledgement, live recovery, voice playback or sustained survival.
