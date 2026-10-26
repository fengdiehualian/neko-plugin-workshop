"""Run real media requests and upload orchestration with an isolated Node clock."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def transport_node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is required for media transport behavior tests")
    return executable


def run_transport(node: str, operation: str) -> None:
    scaffold = r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { setImmediate as realImmediate } from "node:timers";
const root = process.argv[1];
const moduleUrl = source => "data:text/javascript;base64," +
    Buffer.from(stripTypeScriptTypes(source)).toString("base64");
const transportUrl = moduleUrl(readFileSync(root + "/ui/media_transport.ts", "utf8"));
const transport = await import(transportUrl);
const utilsUrl = moduleUrl(readFileSync(root + "/ui/utils.ts", "utf8"));
let mediaSource = readFileSync(root + "/ui/media.tsx", "utf8");
// Keep the actual request/file functions, omitting only the React JSX renderer.
mediaSource = mediaSource.slice(0, mediaSource.indexOf("// Stable source effect")) +
    mediaSource.slice(mediaSource.indexOf("function releaseVideo("));
mediaSource = mediaSource.replace(/^import .* from "@neko\/plugin-ui"\r?\n/m, "")
    .replaceAll('from "./utils"', "from " + JSON.stringify(utilsUrl))
    .replaceAll('from "./media_transport"', "from " + JSON.stringify(transportUrl));
const media = await import(moduleUrl(mediaSource));

let now = 0, sequence = 0;
const timers = new Map(), scheduled = [], signals = [], requests = [], blobs = [];
globalThis.setTimeout = (fn, delay = 0) => {
    const id = ++sequence;
    timers.set(id, {fn, at: now + Number(delay), delay: Number(delay)});
    scheduled.push(Number(delay));
    return id;
};
globalThis.clearTimeout = id => timers.delete(id);
globalThis.setInterval = (fn, delay) => {
    const id = ++sequence;
    timers.set(id, {fn, at: now + Number(delay), delay: Number(delay), interval: true});
    return id;
};
globalThis.clearInterval = id => timers.delete(id);
Date.now = () => now;
async function flush() {
    for (let i = 0; i < 24; i++) await Promise.resolve();
    await new Promise(resolve => realImmediate(resolve));
    for (let i = 0; i < 24; i++) await Promise.resolve();
}
async function finish(promise) {
    let settled = false, result, failure;
    promise.then(value => {settled = true; result = value;},
        error => {settled = true; failure = error;});
    for (let step = 0; step < 100 && !settled; step++) {
        await flush();
        if (settled) break;
        assert.ok(timers.size, "request stalled without a deadline or retry timer");
        const [id, timer] = [...timers].sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        now = timer.at;
        if (timer.interval) timer.at += timer.delay;
        else timers.delete(id);
        timer.fn();
    }
    assert.ok(settled, "media request exceeded the finite test step budget");
    if (failure) throw failure;
    return result;
}
const never = () => new Promise(() => {});
const envelope = result => ({plugin_id: "forever_companion", action_id: "gallery_add", result});
function observe(id, args, options) {
    assert.ok(options.signal instanceof AbortSignal);
    assert.equal(options.signal.aborted, false);
    signals.push(options.signal);
    requests.push({id, args, timeoutMs: options.timeoutMs, signal: options.signal});
}
function noPendingTimers() {
    assert.equal(timers.size, 0, "deadline or cancellation timer leaked");
}
class TestVideo extends EventTarget {
    videoWidth = 640; videoHeight = 360; readyState = 2;
    load() { queueMicrotask(() => this.dispatchEvent(new Event("loadeddata"))); }
    pause() {}
    removeAttribute() {}
}
globalThis.document = {
    createElement(kind) {
        if (kind === "video") return new TestVideo();
        assert.equal(kind, "canvas");
        return {width: 0, height: 0, getContext: () => ({drawImage() {}}),
            toDataURL: () => "data:image/jpeg;base64," + Buffer.from("poster").toString("base64")};
    }
};
globalThis.FileReader = class {
    readAsDataURL(blob) {
        blob.arrayBuffer().then(buffer => {
            this.result = "data:application/octet-stream;base64," + Buffer.from(buffer).toString("base64");
            this.onload();
        }, () => this.onerror());
    }
};
URL.createObjectURL = blob => {blobs.push(blob); return "blob:test/" + blobs.length;};
URL.revokeObjectURL = () => {};
function videoFile(size = 2 * media.VIDEO_CHUNK_BYTES + 3) {
    return new File([new Uint8Array(size)], "wallpaper.mp4", {type: "video/mp4"});
}
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", scaffold + operation, str(ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


@pytest.mark.parametrize("executed_before_loss", [False, True])
def test_chunk_request_or_success_ack_loss_recovers_without_duplicate_write(
    transport_node, executed_before_loss,
):
    run_transport(transport_node, "const executedBeforeLoss = " + json.dumps(executed_before_loss) + r""";
const writes = new Map();
let attempts = 0;
const args = {op: "video_chunk", upload_id: "a".repeat(32), chunk_index: 0, data_b64: "eA=="};
const task = {cancelled: false};
const result = await finish(transport.callMediaWithRetry(async (id, value, options) => {
    observe(id, value, options);
    attempts++;
    assert.deepEqual(value, args);
    if (attempts > 1) assert.equal(signals[attempts - 2].aborted, true);
    if (attempts === 1 && !executedBeforeLoss) return never();
    if (!writes.has(value.chunk_index)) writes.set(value.chunk_index, value.data_b64);
    if (attempts === 1) return never();
    return {chunk_index: 0, accepted: true};
}, "gallery_add", args, task, {operation: "video_chunk", chunkIndex: 0, timeoutMs: 20000, attempts: 3}));
assert.deepEqual(result, {chunk_index: 0, accepted: true});
assert.equal(attempts, 2);
assert.equal(writes.size, 1);
assert.deepEqual(requests.map(value => value.timeoutMs), [20000, 20000]);
assert.equal(signals[0].aborted, true);
assert.equal(signals[1].aborted, false);
assert.equal(task.controller, undefined);
assert.ok(scheduled.includes(250));
noPendingTimers();
""")


def test_permanent_timeout_has_three_attempts_and_aborts_each_old_request(transport_node):
    run_transport(transport_node, r"""
const task = {cancelled: false};
let attempts = 0;
await assert.rejects(() => finish(transport.callMediaWithRetry((id, args, options) => {
    observe(id, args, options);
    if (attempts) assert.equal(signals[attempts - 1].aborted, true);
    attempts++;
    return never();
}, "get_gallery_image", {item_id: "g1", chunk_index: 0}, task,
    {operation: "video_read", chunkIndex: 0, timeoutMs: 20000, attempts: 3})), /media_request_timeout/);
assert.equal(attempts, 3);
assert.ok(signals.every(signal => signal.aborted));
assert.deepEqual(scheduled, [20000, 250, 20000, 750, 20000]);
assert.equal(task.controller, undefined);
noPendingTimers();
""")


def test_cancellation_interrupts_request_and_retry_backoff(transport_node):
    run_transport(transport_node, r"""
for (const cancelDuringBackoff of [false, true]) {
    const task = {cancelled: false};
    let attempts = 0;
    const pending = transport.callMediaWithRetry((id, args, options) => {
        observe(id, args, options);
        attempts++;
        if (cancelDuringBackoff) return Promise.reject(new Error("hosted surface request timed out"));
        return never();
    }, "gallery_add", {op: "video_chunk"}, task,
        {operation: "video_chunk", timeoutMs: 20000, attempts: 3});
    pending.catch(() => {});
    await flush();
    if (cancelDuringBackoff) {
        assert.equal(requests.at(-1).signal.aborted, true);
        assert.ok([...timers.values()].some(timer => timer.delay === 250));
    }
    transport.cancelMediaTask(task);
    await assert.rejects(() => finish(pending), /wallpaper_cancelled/);
    assert.equal(attempts, 1);
    assert.equal(task.cancelled, true);
    assert.equal(task.controller, undefined);
    noPendingTimers();
}
""")


def test_business_rejections_are_never_retried_even_with_transient_http_status(transport_node):
    run_transport(transport_node, r"""
for (const value of [
    {message: "video_disk_full", code: "ETIMEDOUT", status: 503},
    {message: "hosted surface request timed out", code: "video_chunk_invalid", status: 504},
    {message: "gallery_full", status: 408},
    {message: "wallpaper_cancelled", status: 502},
    {message: "permission_denied", status: 503},
    {message: "hosted surface request timed out", name: "AbortError"},
]) {
    let attempts = 0;
    const failure = Object.assign(new Error(value.message), value);
    await assert.rejects(() => finish(transport.callMediaWithRetry(async () => {
        attempts++;
        throw failure;
    }, "gallery_add", {op: "video_chunk"}, {cancelled: false},
        {operation: "video_chunk", timeoutMs: 20000, attempts: 3})), error => error === failure);
    assert.equal(attempts, 1);
    noPendingTimers();
}
for (const value of [
    new Error("media_request_timeout"), new Error("hosted surface request timed out"),
    Object.assign(new Error("request failed"), {status: 503}),
    Object.assign(new Error("request failed"), {code: "ECONNRESET"}),
]) assert.equal(transport.isTransientMediaError(value), true);
""")


def test_structured_host_timeout_is_retryable_but_other_host_failures_are_not(transport_node):
    run_transport(transport_node, r"""
for (const details of [
    {error_type: "TimeoutError"}, {cause: {error_type: "ConnectionResetError"}},
]) {
    let attempts = 0;
    const start = requests.length;
    const result = await finish(transport.callMediaWithRetry(async (id, args, options) => {
        observe(id, args, options);
        attempts++;
        if (attempts > 1) assert.equal(requests[start].signal.aborted, true);
        if (attempts === 1) throw Object.assign(new Error("Plugin action failed"), {
            code: "PLUGIN_UI_ACTION_FAILED", details,
        });
        return {accepted: true};
    }, "gallery_add", {op: "video_chunk"}, {cancelled: false},
        {operation: "video_chunk", timeoutMs: 20000, attempts: 3}));
    assert.equal(result.accepted, true);
    assert.equal(attempts, 2);
    assert.equal(requests[start].signal.aborted, true);
    assert.equal(requests[start + 1].signal.aborted, false);
    noPendingTimers();
}
for (const details of [{error_type: "RuntimeError"}, {}, undefined]) {
    let attempts = 0;
    const failure = Object.assign(new Error("Plugin action failed"), {
        code: "PLUGIN_UI_ACTION_FAILED", details,
    });
    await assert.rejects(() => finish(transport.callMediaWithRetry(async () => {
        attempts++;
        throw failure;
    }, "gallery_add", {op: "video_chunk"}, {cancelled: false},
        {operation: "video_chunk", timeoutMs: 20000, attempts: 3})), error => error === failure);
    assert.equal(attempts, 1);
    noPendingTimers();
}
assert.equal(transport.isTransientMediaError({
    message: "video_chunk_invalid", code: "PLUGIN_UI_ACTION_FAILED",
    details: {error_type: "TimeoutError"},
}), false);
""")


def test_upload_retries_lost_chunk_ack_and_commit_reply_with_ack_only_progress(transport_node):
    run_transport(transport_node, r"""
const task = {cancelled: false}, progress = [], writes = new Map(), saved = [];
let firstChunkAttempts = 0, commitAttempts = 0;
const file = videoFile();
const result = await finish(media.uploadVideoFile(async (id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_begin") {
        assert.equal(options.timeoutMs, 150000);
        return envelope({upload_id: "a".repeat(32), chunk_bytes: media.VIDEO_CHUNK_BYTES});
    }
    if (args.op === "video_chunk") {
        assert.equal(options.timeoutMs, 20000);
        if (!writes.has(args.chunk_index)) writes.set(args.chunk_index, args.data_b64);
        if (args.chunk_index === 0) {
            firstChunkAttempts++;
            assert.deepEqual(progress, [[0, "preparing"]]);
            if (firstChunkAttempts === 1) return never();
        } else {
            assert.equal(progress.filter(value => value[1] === "uploading").length, args.chunk_index);
        }
        return envelope({chunk_index: args.chunk_index, accepted: true});
    }
    assert.equal(args.op, "video_commit");
    assert.equal(options.timeoutMs, 150000);
    commitAttempts++;
    assert.equal(progress.at(-1)[1], "saving");
    if (!saved.length) saved.push({id: "g1"});
    if (commitAttempts === 1) return never();
    return envelope({id: "g1", items: saved});
}, file, "wallpaper", task, (value, stage) => progress.push([value, stage])));
assert.equal(result.id, "g1");
assert.equal(firstChunkAttempts, 2);
assert.equal(commitAttempts, 2);
assert.equal(writes.size, 3);
assert.equal(saved.length, 1);
assert.equal(requests.filter(value => value.args.op === "video_begin").length, 1);
assert.equal(requests.filter(value => value.args.op === "video_abort").length, 0);
assert.deepEqual(progress, [
    [0, "preparing"], [50, "uploading"], [99, "uploading"], [99, "uploading"],
    [99, "saving"], [100, "done"],
]);
assert.equal(task.controller, undefined);
assert.equal(signals.at(-1).aborted, false);
noPendingTimers();
""")


def test_upload_begin_is_not_retried_when_its_response_is_lost(transport_node):
    run_transport(transport_node, r"""
const progress = [];
await assert.rejects(() => finish(media.uploadVideoFile((id, args, options) => {
    observe(id, args, options);
    assert.equal(args.op, "video_begin");
    return never();
}, videoFile(), "wallpaper", {cancelled: false}, (value, stage) => progress.push([value, stage]))),
    /media_request_timeout/);
assert.equal(requests.length, 1);
assert.equal(requests[0].timeoutMs, 150000);
assert.equal(requests[0].signal.aborted, true);
assert.deepEqual(progress, [[0, "preparing"]]);
noPendingTimers();
""")


@pytest.mark.parametrize("failed_operation", ["video_chunk", "video_commit"])
def test_upload_permanent_timeout_and_abort_cleanup_both_have_finite_limits(
    transport_node, failed_operation,
):
    run_transport(transport_node, "const failedOperation = " + json.dumps(failed_operation) + r""";
const progress = [];
await assert.rejects(() => finish(media.uploadVideoFile(async (id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_begin") return {upload_id: "d".repeat(32), chunk_bytes: media.VIDEO_CHUNK_BYTES};
    if (args.op === failedOperation || args.op === "video_abort") return never();
    assert.equal(args.op, "video_chunk");
    return {chunk_index: args.chunk_index, accepted: true};
}, videoFile(17), "wallpaper", {cancelled: false}, (value, stage) => progress.push([value, stage]))),
    /media_request_timeout/);
assert.equal(requests.filter(value => value.args.op === "video_begin").length, 1);
assert.equal(requests.filter(value => value.args.op === failedOperation).length,
    failedOperation === "video_chunk" ? 3 : 2);
assert.equal(requests.filter(value => value.args.op === "video_abort").length, 2);
assert.ok(requests.filter(value => value.args.op === failedOperation || value.args.op === "video_abort")
    .every(value => value.signal.aborted));
assert.equal(progress.some(value => value[1] === "done"), false);
if (failedOperation === "video_chunk") assert.deepEqual(progress, [[0, "preparing"]]);
else assert.deepEqual(progress, [[0, "preparing"], [99, "uploading"], [99, "saving"]]);
noPendingTimers();
""")


def test_upload_cancel_after_ack_never_sends_next_chunk_and_cleanup_retries_independently(transport_node):
    run_transport(transport_node, r"""
const task = {cancelled: false}, progress = [];
let abortAttempts = 0;
await assert.rejects(() => finish(media.uploadVideoFile(async (id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_begin") return {upload_id: "b".repeat(32), chunk_bytes: media.VIDEO_CHUNK_BYTES};
    if (args.op === "video_chunk") {
        assert.equal(args.chunk_index, 0);
        return {chunk_index: 0, accepted: true};
    }
    assert.equal(args.op, "video_abort");
    assert.equal(task.cancelled, true);
    assert.equal(options.timeoutMs, 30000);
    abortAttempts++;
    if (abortAttempts === 1) return never();
    return {aborted: true};
}, videoFile(), "wallpaper", task, (value, stage) => {
    progress.push([value, stage]);
    if (stage === "uploading") media.cancelMediaTask(task);
})), /wallpaper_cancelled/);
assert.deepEqual(requests.map(value => value.args.op), [
    "video_begin", "video_chunk", "video_abort", "video_abort",
]);
assert.equal(abortAttempts, 2);
assert.deepEqual(progress, [[0, "preparing"], [50, "uploading"]]);
assert.equal(requests[1].signal.aborted, false);
assert.equal(requests[2].signal.aborted, true);
assert.equal(requests[3].signal.aborted, false);
noPendingTimers();
""")


def test_upload_business_refusal_and_invalid_ack_do_not_advance_progress(transport_node):
    run_transport(transport_node, r"""
for (const invalidAck of [false, true]) {
    const progress = [], start = requests.length;
    await assert.rejects(() => finish(media.uploadVideoFile(async (id, args, options) => {
        observe(id, args, options);
        if (args.op === "video_begin") return {upload_id: "c".repeat(32), chunk_bytes: media.VIDEO_CHUNK_BYTES};
        if (args.op === "video_abort") return {aborted: true};
        assert.equal(args.op, "video_chunk");
        if (invalidAck) return {chunk_index: 1, accepted: true};
        throw Object.assign(new Error("video_disk_full"), {status: 503});
    }, videoFile(), "wallpaper", {cancelled: false}, (value, stage) => progress.push([value, stage]))),
        invalidAck ? /video_chunk_invalid/ : /video_disk_full/);
    assert.deepEqual(requests.slice(start).map(value => value.args.op), [
        "video_begin", "video_chunk", "video_abort",
    ]);
    assert.deepEqual(progress, [[0, "preparing"]]);
    noPendingTimers();
}
""")


def test_download_retries_only_current_chunk_and_reconstructs_original_bytes(transport_node):
    run_transport(transport_node, r"""
const payload = Buffer.alloc(media.VIDEO_CHUNK_BYTES + 7, 123);
let firstAttempts = 0;
const result = await finish(media.downloadGalleryVideo(async (id, args, options) => {
    observe(id, args, options);
    assert.equal(id, "get_gallery_image");
    assert.ok(options.timeoutMs === 20000 || options.timeoutMs === 3000);
    if (args.chunk_index === 0 && ++firstAttempts === 1) return never();
    const offset = args.chunk_index * media.VIDEO_CHUNK_BYTES;
    return envelope({chunk_index: args.chunk_index,
        data_b64: payload.subarray(offset, offset + media.VIDEO_CHUNK_BYTES).toString("base64")});
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 2,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false}));
assert.equal(result.kind, "video");
assert.equal(result.poster, "poster");
    assert.deepEqual(requests.map(value => value.args.chunk_index), [0, 0, 1]);
assert.equal(signals[0].aborted, true);
assert.equal(signals[1].aborted, false);
assert.deepEqual(Buffer.from(await blobs.at(-1).arrayBuffer()), payload);
noPendingTimers();
""")


def test_persisted_receipt_recovers_lost_ack_and_ignores_late_primary_ack(transport_node):
    run_transport(transport_node, r"""
const task = {cancelled: false}, progress = [], writes = new Map();
let lateAck, primarySignal, probes = 0;
const result = await finish(media.uploadVideoFile((id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_begin") return Promise.resolve(envelope({
        upload_id: "e".repeat(32), chunk_bytes: media.VIDEO_CHUNK_BYTES, chunk_receipts: true,
    }));
    if (args.op === "video_chunk") {
        writes.set(args.chunk_index, args.data_b64);
        if (args.chunk_index === 0) {
            primarySignal = options.signal;
            return new Promise(resolve => {lateAck = resolve;});
        }
        return Promise.resolve(envelope({chunk_index: args.chunk_index, accepted: true}));
    }
    if (args.op === "video_status") {
        probes++;
        assert.equal(now, 1000);
        assert.equal(options.timeoutMs, 3000);
        assert.deepEqual(progress, [[0, "preparing"]]);
        assert.equal(primarySignal.aborted, false);
        assert.equal(args.upload_id, "e".repeat(32));
        assert.equal(args.chunk_index, 0);
        return Promise.resolve(envelope({chunk_index: 0, accepted: true}));
    }
    assert.equal(args.op, "video_commit");
    return Promise.resolve(envelope({id: "g1", items: [{id: "g1"}]}));
}, videoFile(), "wallpaper", task, (value, stage) => progress.push([value, stage])));
assert.equal(result.id, "g1");
assert.equal(probes, 1);
assert.equal(primarySignal.aborted, true);
assert.equal(writes.size, 3);
assert.equal(requests.filter(value => value.args.op === "video_chunk").length, 3);
assert.equal(progress.filter(value => value[1] === "uploading").length, 3);
const beforeLateAck = [...progress];
lateAck(envelope({chunk_index: 0, accepted: true}));
await flush();
assert.deepEqual(progress, beforeLateAck);
assert.equal(task.controller, undefined);
noPendingTimers();
""")


def test_receipt_success_wins_over_primary_abort_rejection(transport_node):
    run_transport(transport_node, r"""
const task = {cancelled: false};
const result = await finish(transport.callMediaWithRetry((id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_status") return Promise.resolve({chunk_index: 0, accepted: true});
    return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => {
        reject(Object.assign(new Error("Hosted request aborted"), {name: "AbortError"}));
    }, {once: true}));
}, "gallery_add", {op: "video_chunk"}, task, {
    operation: "video_chunk", chunkIndex: 0, timeoutMs: 20000, attempts: 3,
    receipt: {uploadId: "f".repeat(32), isAccepted: value => value?.accepted === true && value.chunk_index === 0},
}));
assert.deepEqual(result, {chunk_index: 0, accepted: true});
assert.equal(now, 1000);
assert.equal(requests.length, 2);
assert.equal(signals[0].aborted, true);
assert.equal(task.cancelled, false);
noPendingTimers();
""")


@pytest.mark.parametrize("probe_outcome", ["not_accepted", "wrong_index", "failure", "timeout"])
def test_receipt_failure_or_missing_confirmation_does_not_fail_primary(transport_node, probe_outcome):
    run_transport(transport_node, "const probeOutcome = " + json.dumps(probe_outcome) + r""";
const task = {cancelled: false};
const result = await finish(transport.callMediaWithRetry((id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_status") {
        if (probeOutcome === "not_accepted") return Promise.resolve({chunk_index: 0, accepted: false});
        if (probeOutcome === "wrong_index") return Promise.resolve({chunk_index: 1, accepted: true});
        if (probeOutcome === "failure") return Promise.reject(new Error("video_upload_not_found"));
        return never();
    }
    return new Promise(resolve => setTimeout(() => resolve({chunk_index: 0, accepted: true}),
        probeOutcome === "timeout" ? 5500 : 1500));
}, "gallery_add", {op: "video_chunk"}, task, {
    operation: "video_chunk", chunkIndex: 0, timeoutMs: 20000, attempts: 3,
    receipt: {uploadId: "f".repeat(32), isAccepted: value => value?.accepted === true && value.chunk_index === 0},
}));
assert.equal(result.accepted, true);
assert.equal(requests.length, 2);
assert.equal(signals[0].aborted, false);
assert.equal(signals[1].aborted, true);
assert.equal(task.controller, undefined);
noPendingTimers();
""")


@pytest.mark.parametrize("cancel_at", [500, 1100])
def test_receipt_cancellation_aborts_both_requests_and_never_starts_after_cancel(transport_node, cancel_at):
    run_transport(transport_node, "const cancelAt = " + str(cancel_at) + r""";
const task = {cancelled: false};
const pending = transport.callMediaWithRetry((id, args, options) => {
    observe(id, args, options);
    return never();
}, "gallery_add", {op: "video_chunk"}, task, {
    operation: "video_chunk", chunkIndex: 0, timeoutMs: 20000, attempts: 3,
    receipt: {uploadId: "f".repeat(32), isAccepted: value => value?.accepted === true},
});
setTimeout(() => transport.cancelMediaTask(task), cancelAt);
await assert.rejects(() => finish(pending), /wallpaper_cancelled/);
assert.equal(requests.length, cancelAt < 1000 ? 1 : 2);
assert.ok(signals.every(signal => signal.aborted));
assert.equal(task.controller, undefined);
noPendingTimers();
""")


@pytest.mark.parametrize("reject_at", [500, 1100])
def test_primary_business_refusal_is_not_overridden_by_receipt(transport_node, reject_at):
    run_transport(transport_node, "const rejectAt = " + str(reject_at) + r""";
let lateReceipt;
const task = {cancelled: false};
const failure = new Error("video_chunk_invalid");
await assert.rejects(() => finish(transport.callMediaWithRetry((id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_status") return new Promise(resolve => {lateReceipt = resolve;});
    return new Promise((_resolve, reject) => setTimeout(() => reject(failure), rejectAt));
}, "gallery_add", {op: "video_chunk"}, task, {
    operation: "video_chunk", chunkIndex: 0, timeoutMs: 20000, attempts: 3,
    receipt: {uploadId: "f".repeat(32), isAccepted: value => value?.accepted === true},
})), error => error === failure);
assert.equal(requests.length, rejectAt < 1000 ? 1 : 2);
assert.ok(signals.every(signal => signal.aborted));
if (lateReceipt) lateReceipt({chunk_index: 0, accepted: true});
await flush();
assert.equal(task.controller, undefined);
noPendingTimers();
""")


def test_missing_receipt_capability_preserves_original_upload_protocol(transport_node):
    run_transport(transport_node, r"""
const progress = [];
await finish(media.uploadVideoFile((id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_begin") return Promise.resolve({
        upload_id: "a".repeat(32), chunk_bytes: media.VIDEO_CHUNK_BYTES,
    });
    if (args.op === "video_chunk") return new Promise(resolve => setTimeout(
        () => resolve({chunk_index: args.chunk_index, accepted: true}), 1500));
    assert.equal(args.op, "video_commit");
    return Promise.resolve({id: "g1", items: [{id: "g1"}]});
}, videoFile(17), "wallpaper", {cancelled: false}, (value, stage) => progress.push([value, stage])));
assert.deepEqual(requests.map(value => value.args.op), ["video_begin", "video_chunk", "video_commit"]);
assert.equal(now, 1500);
assert.deepEqual(progress, [[0, "preparing"], [99, "uploading"], [99, "saving"], [100, "done"]]);
noPendingTimers();
""")


def test_primary_ack_cancels_inflight_receipt_and_ignores_its_late_confirmation(transport_node):
    run_transport(transport_node, r"""
let lateReceipt;
const result = await finish(transport.callMediaWithRetry((id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_status") return new Promise(resolve => {lateReceipt = resolve;});
    return new Promise(resolve => setTimeout(() => resolve({chunk_index: 0, accepted: true}), 1500));
}, "gallery_add", {op: "video_chunk"}, {cancelled: false}, {
    operation: "video_chunk", chunkIndex: 0, timeoutMs: 20000, attempts: 3,
    receipt: {uploadId: "f".repeat(32), isAccepted: value => value?.accepted === true},
}));
assert.equal(result.accepted, true);
assert.equal(signals[0].aborted, false);
assert.equal(signals[1].aborted, true);
lateReceipt({chunk_index: 0, accepted: true});
await flush();
assert.equal(requests.length, 2);
noPendingTimers();
""")


def test_unconfirmed_receipt_keeps_original_finite_retry_budget(transport_node):
    run_transport(transport_node, r"""
const task = {cancelled: false};
await assert.rejects(() => finish(transport.callMediaWithRetry((id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_status") return Promise.resolve({chunk_index: 0, accepted: false});
    return never();
}, "gallery_add", {op: "video_chunk"}, task, {
    operation: "video_chunk", chunkIndex: 0, timeoutMs: 20000, attempts: 3,
    receipt: {uploadId: "f".repeat(32), isAccepted: value => value?.accepted === true},
})), /media_request_timeout/);
assert.equal(requests.filter(value => value.args.op === "video_chunk").length, 3);
assert.equal(requests.filter(value => value.args.op === "video_status").length, 3);
assert.ok(signals.every(signal => signal.aborted));
assert.equal(task.controller, undefined);
noPendingTimers();
""")


def test_upload_returns_local_frames_only_after_successful_commit(transport_node):
    run_transport(transport_node, r"""
const file = videoFile(17);
let preparedFrames;
const result = await finish(media.uploadVideoFile((id, args, options) => {
    observe(id, args, options);
    if (args.op === "video_begin") {
        preparedFrames = {mime: args.mime, poster: args.poster, thumb: args.thumb};
        return Promise.resolve({upload_id: "a".repeat(32), chunk_bytes: media.VIDEO_CHUNK_BYTES});
    }
    if (args.op === "video_chunk") return Promise.resolve({chunk_index: args.chunk_index, accepted: true});
    assert.equal(args.op, "video_commit");
    return Promise.resolve(envelope({id: "g7", items: [{id: "g7"}]}));
}, file, "wallpaper", {cancelled: false}, () => {}));
assert.equal(result.id, "g7");
assert.deepEqual(result.localFrames, preparedFrames);
assert.equal(result.localFrames.mime, "video/mp4");
assert.ok(result.localFrames.poster.startsWith("data:image/jpeg;base64,"));
assert.equal(blobs.length, 1, "upload creates only the temporary decoding URL; panel owns playback URLs");
assert.deepEqual(requests.map(value => value.args.op), ["video_begin", "video_chunk", "video_commit"]);
noPendingTimers();
""")


def test_download_progress_advances_only_after_validated_chunks(transport_node):
    run_transport(transport_node, r"""
const payload = Buffer.alloc(media.VIDEO_CHUNK_BYTES + 7, 123), progress = [];
let firstAttempts = 0;
const result = await finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    if (args.chunk_index === 0 && ++firstAttempts === 1) {
        assert.deepEqual(progress, [0]);
        return never();
    }
    const offset = args.chunk_index * media.VIDEO_CHUNK_BYTES;
    return Promise.resolve(envelope({chunk_index: args.chunk_index,
        data_b64: payload.subarray(offset, offset + media.VIDEO_CHUNK_BYTES).toString("base64")}));
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 2,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false},
    value => progress.push(value)));
assert.equal(result.kind, "video");
assert.deepEqual(progress, [0, 99, 100]);
assert.deepEqual(requests.map(value => value.args.chunk_index), [0, 0, 1]);
noPendingTimers();
""")


def test_download_uses_bounded_chunk_batches_and_preserves_order(transport_node):
    run_transport(transport_node, r"""
const payload = Buffer.alloc(media.VIDEO_CHUNK_BYTES + 7, 1), progress = [];
const result = await finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    assert.equal(id, "get_gallery_image");
    assert.equal(args.item_id, "g1");
    if (args.chunk_index === 0) {
        assert.equal(args.chunk_count, 2);
        return Promise.resolve(envelope({chunks: [
            {chunk_index: 0, data_b64: payload.subarray(0, media.VIDEO_CHUNK_BYTES).toString("base64")},
            {chunk_index: 1, data_b64: payload.subarray(media.VIDEO_CHUNK_BYTES).toString("base64")},
        ]}));
    }
    throw new Error("unexpected extra batch");
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 2,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false},
    value => progress.push(value)));
assert.equal(result.kind, "video");
assert.equal(requests.length, 1);
assert.deepEqual(progress, [0, 99, 100]);
noPendingTimers();
""")


def test_batch_read_lost_reply_recovers_before_full_retry_deadline(transport_node):
    run_transport(transport_node, r"""
const payload = Buffer.alloc(media.VIDEO_CHUNK_BYTES * 4 + 7, 37), progress = [];
const result = await finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    if (requests.length === 1) return never();
    const chunks = [];
    for (let index = args.chunk_index; index < args.chunk_index + args.chunk_count; index++) {
        chunks.push({chunk_index: index, data_b64: payload.subarray(
            index * media.VIDEO_CHUNK_BYTES,
            Math.min((index + 1) * media.VIDEO_CHUNK_BYTES, payload.length)).toString("base64")});
    }
    return Promise.resolve(envelope(args.chunk_count === 1 ? chunks[0] : {chunks}));
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 5,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false},
    value => progress.push(value)));
assert.equal(result.kind, "video");
assert.equal(now, 1000, "Batch reply loss still waits for the full 20-second deadline");
assert.deepEqual(requests.map(value => [value.args.chunk_index, value.args.chunk_count]),
    [[0, 4], [0, 4], [4, 1]]);
assert.equal(signals[0].aborted, true);
assert.deepEqual(Buffer.from(await blobs.at(-1).arrayBuffer()), payload);
assert.deepEqual(progress, [0, 25, 50, 75, 99, 100]);
noPendingTimers();
""")


@pytest.mark.parametrize("malformed", ["wrong_index", "short_data", "extra_chunk", "empty_batch"])
def test_batch_read_bad_duplicate_does_not_override_valid_primary(transport_node, malformed):
    run_transport(transport_node, "const malformed = " + json.dumps(malformed) + r""";
const payload = Buffer.alloc(media.VIDEO_CHUNK_BYTES + 3, 9);
const valid = {chunks: [0, 1].map(index => ({
    chunk_index: index, data_b64: payload.subarray(index * media.VIDEO_CHUNK_BYTES,
        Math.min((index + 1) * media.VIDEO_CHUNK_BYTES, payload.length)).toString("base64")
}))};
const broken = structuredClone(valid);
if (malformed === "wrong_index") broken.chunks[1].chunk_index = 0;
if (malformed === "short_data") broken.chunks[1].data_b64 = "eA==";
if (malformed === "extra_chunk") broken.chunks.push(valid.chunks[1]);
if (malformed === "empty_batch") broken.chunks = [];
await finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    if (requests.length === 1) return new Promise(resolve => setTimeout(() => resolve(envelope(valid)), 1500));
    return Promise.resolve(envelope(broken));
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 2,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false}));
assert.equal(requests.length, 2, "Delayed batches did not get a bounded duplicate");
assert.equal(now, 1500);
assert.equal(signals[0].aborted, false);
assert.equal(signals[1].aborted, true);
assert.deepEqual(Buffer.from(await blobs.at(-1).arrayBuffer()), payload);
noPendingTimers();
""")


def test_download_falls_back_to_legacy_single_chunk_reply(transport_node):
    run_transport(transport_node, r"""
const payload = Buffer.alloc(media.VIDEO_CHUNK_BYTES + 5, 9), requested = [];
const result = await finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    requested.push({...args});
    return Promise.resolve({chunk_index: args.chunk_index,
        data_b64: payload.subarray(args.chunk_index === 0 ? 0 : media.VIDEO_CHUNK_BYTES,
            args.chunk_index === 0 ? media.VIDEO_CHUNK_BYTES : payload.length).toString("base64")});
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 2,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false}));
assert.equal(result.kind, "video");
assert.deepEqual(requested.map(value => value.chunk_index), [0, 1]);
assert.equal(requested[0].chunk_count, 2);
assert.equal(requested[1].chunk_count, 1);
noPendingTimers();
""")


def test_invalid_download_chunk_never_reports_completion_or_creates_playback_url(transport_node):
    run_transport(transport_node, r"""
const progress = [];
await assert.rejects(() => finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    return Promise.resolve({chunk_index: 0, data_b64: "eA=="});
}, "g1", {mime: "video/mp4", size: 17, chunk_count: 1,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false},
    value => progress.push(value))), /video_read_failed/);
assert.deepEqual(progress, [0]);
assert.equal(blobs.length, 0);
noPendingTimers();
""")


def test_video_read_hedge_recovers_lost_ack_after_one_second(transport_node):
    run_transport(transport_node, r"""
const payload = Buffer.from([11, 22, 33]), progress = [];
const result = await finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    assert.equal(id, "get_gallery_image");
    assert.deepEqual(args, {
        item_id: "g1",
        chunk_index: 0,
        chunk_count: 1,
    });
    if (requests.length === 1) return never();
    assert.equal(options.timeoutMs, 3000);
    return Promise.resolve(envelope({chunk_index: 0, data_b64: payload.toString("base64")}));
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 1,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false},
    value => progress.push(value)));
assert.equal(result.kind, "video");
assert.equal(now, 1000);
assert.equal(requests.length, 2);
assert.equal(signals[0].aborted, true);
assert.equal(signals[1].aborted, false);
assert.deepEqual(progress, [0, 100]);
noPendingTimers();
""")


def test_video_read_hedge_is_not_started_for_fast_primary_ack(transport_node):
    run_transport(transport_node, r"""
const payload = Buffer.from([1, 2]);
const result = await finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    return Promise.resolve({chunk_index: 0, data_b64: payload.toString("base64")});
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 1,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false}));
assert.equal(result.kind, "video");
assert.equal(requests.length, 1);
assert.equal(now, 0);
noPendingTimers();
""")


def test_video_read_hedge_ignores_malformed_duplicate_and_keeps_primary(transport_node):
    run_transport(transport_node, r"""
const payload = Buffer.from([5, 6, 7]);
const result = await finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    if (requests.length === 1) return new Promise(resolve => setTimeout(
        () => resolve({chunk_index: 0, data_b64: payload.toString("base64")}), 1500));
    return Promise.resolve({chunk_index: 1, data_b64: payload.toString("base64")});
}, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 1,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false}));
assert.equal(result.kind, "video");
assert.equal(requests.length, 2);
assert.equal(signals[1].aborted, true);
assert.equal(now, 1500);
noPendingTimers();
""")


def test_video_read_primary_malformed_reply_fails_closed_before_duplicate(transport_node):
    run_transport(transport_node, r"""
await assert.rejects(() => finish(media.downloadGalleryVideo((id, args, options) => {
    observe(id, args, options);
    return Promise.resolve({chunk_index: 1, data_b64: "eA=="});
}, "g1", {mime: "video/mp4", size: 1, chunk_count: 1,
    chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false})), /video_read_failed/);
assert.equal(requests.length, 1);
noPendingTimers();
""")


def test_video_read_hedge_failure_or_timeout_does_not_interrupt_primary(transport_node):
    run_transport(transport_node, r"""
for (const hedgeMode of ["failure", "timeout"]) {
    const payload = Buffer.from([8, 9, 10]);
    const start = requests.length;
    const result = await finish(media.downloadGalleryVideo((id, args, options) => {
        observe(id, args, options);
        if (requests.length === start + 1) return new Promise(resolve => setTimeout(
            () => resolve({chunk_index: 0, data_b64: payload.toString("base64")}), 1500));
        if (hedgeMode === "failure") return Promise.reject(new Error("hosted surface request timed out"));
        return never();
    }, "g1", {mime: "video/mp4", size: payload.length, chunk_count: 1,
        chunk_bytes: media.VIDEO_CHUNK_BYTES, poster: "poster"}, {cancelled: false}));
    assert.equal(result.kind, "video");
    assert.equal(requests.length - start, 2);
    assert.equal(signals[start + 1].aborted, true);
}
noPendingTimers();
""")


def test_video_read_hedge_is_cancelled_with_task_and_never_starts_after_cancel(transport_node):
    run_transport(transport_node, r"""
for (const cancelAt of [500, 1100]) {
    const task = {cancelled: false};
    const start = requests.length;
    const pending = transport.callMediaWithRetry((id, args, options) => {
        observe(id, args, options);
        return never();
    }, "get_gallery_image", {item_id: "g1", chunk_index: 0}, task, {
        operation: "video_read", chunkIndex: 0, timeoutMs: 20000, attempts: 1,
        hedge: {id: "get_gallery_image", args: {item_id: "g1", chunk_index: 0},
            timeoutMs: 3000, validate: () => true},
    });
    setTimeout(() => transport.cancelMediaTask(task), cancelAt);
    await assert.rejects(() => finish(pending), /wallpaper_cancelled/);
    assert.equal(requests.length - start, cancelAt < 1000 ? 1 : 2);
    assert.ok(signals.slice(start).every(signal => signal.aborted));
    noPendingTimers();
}
""")


def test_video_read_hedge_keeps_finite_three_attempt_budget(transport_node):
    run_transport(transport_node, r"""
const task = {cancelled: false};
await assert.rejects(() => finish(transport.callMediaWithRetry((id, args, options) => {
    observe(id, args, options);
    return never();
}, "get_gallery_image", {item_id: "g1", chunk_index: 0}, task, {
    operation: "video_read", chunkIndex: 0, timeoutMs: 20000, attempts: 3,
    hedge: {id: "get_gallery_image", args: {item_id: "g1", chunk_index: 0},
        timeoutMs: 3000, validate: () => true},
})), /media_request_timeout/);
assert.equal(requests.length, 6);
assert.equal(requests.filter(value => value.args.chunk_index === 0).length, 6);
assert.ok(signals.every(signal => signal.aborted));
assert.equal(task.controller, undefined);
noPendingTimers();
""")
