"""Frozen-source comparison: synthetic evidence, existing read-only catalog.

No capture, OCR, model call, message submission, install or game operation.
Reports local CPU/formatter cost, NOT real recognition or speech latency.
"""
import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    plugin = root / "plugin/plugins/hsr_companion"
    sys.path.insert(0, str(root))
    if args.frozen:
        importlib.import_module("plugin.plugins")
        frozen = plugin / "dist/replyplan-check-final-20261022/hsr_companion"
        spec = importlib.util.spec_from_file_location("plugin.plugins.hsr_companion", frozen / "__init__.py",
            submodule_search_locations=[str(frozen), str(plugin)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.runtime import consultation
    from plugin.plugins.hsr_companion.runtime.answer_message import render_followup
    from plugin.plugins.hsr_companion.runtime.build_info import BUILD_REVISION
    from plugin.plugins.hsr_companion.runtime.guidance import GuidanceCatalog, build_guidance, plan_guidance
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    from plugin.plugins.hsr_companion.tests.test_d1_closeout import panel
    from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

    book = GuidanceCatalog(json.loads((plugin / "resources/guidance.cn-4.6.json").read_text(encoding="utf-8")))
    catalog = ExternalKnowledgeCatalog(registry=json.loads((plugin / "resources/character_registry.json").read_text(encoding="utf-8")),
        root=Path("C:/Users/USER/AppData/Local/N.E.K.O/data-packs/hsr_companion"))
    assert catalog.get("light_cone.23055"), "Requires existing catalog; do not download."

    def select(q, v=None, intent="equipment_guide"):
        base = build_query_plan(q, intent)
        planned = plan_guidance(q, base, book=book, catalog=catalog)
        return planned, build_guidance(planned, q, book=book, catalog=catalog, profile={}, visual=v)

    topics = []
    for query in ("真珠遗器怎么配？", "真珠光锥怎么配？", "真珠遗器和光锥怎么配？",
                  "真珠不用讲光锥，只看遗器怎么配？", "真珠不用讲遗器，只看光锥怎么配？"):
        for intent in ("conversation", "equipment_guide"):
            _, g = select(query, intent=intent)
            topics.append({"query": query, "intent": intent, "selected": [
                {k: bool(row.get(k)) for k in ("main_stats", "equipment_candidates", "light_cone_candidates")}
                for row in g["reply_plan"].get("discussion_options") or []]})
    q = "这件遗器适合绯英吗？"
    _, a = select(q, relic(subs=[("暴击率", "8.7%"), ("防御力", "15.1%"), ("生命值", "80")]), "assess_relic")
    _, b = select(q, relic(subs=[("暴击率", "8.7%"), ("防御力", "15.1%"), ("生命值", "81")]), "assess_relic")
    cases = {"topics": topics, "unspoken_optional_notice": consultation.for_followup(
        b["reply_plan"], previous=a["reply_plan"])["answer_increment"]}
    v = panel()
    v["characters"] = [{"entity_id": "character.pearl", "name": "真珠", "identity_scope": "detail_subject"}]
    timings = []
    for name, query, visual, intent in (
        ("panel", "真珠这面板怎么样，接下来先养哪儿？", v, "assess_panel"),
        ("relic", q, relic(), "assess_relic"),
        ("full", "完整报出这件遗器主副词条，并说明适合绯英吗、下一步怎么改？", relic(), "assess_relic"),
        ("unread", q, {}, "assess_relic"),
    ):
        planned, g = select(query, visual, intent)
        observed = planned.observation_plan(build_query_plan(query, intent))
        data = {"guidance_answer": g, "equipment_answer": g["assessment"].get("equipment_answer") or {},
                "screen_facts": visual.get("screen_facts") or {}, "answer_progress": {"complete": True}}
        start = time.perf_counter()
        text, receipt = render_followup(data, plan=observed, ready=True)
        cold_ms = (time.perf_counter() - start) * 1000
        cases[name] = {"conclusion": g["reply_plan"]["conclusion"], "next_step": g["reply_plan"]["next_step"],
            "text": text, "receipt": receipt, "next_step_in_body": bool(g["reply_plan"]["next_step"])
                and g["reply_plan"]["next_step"] in text}
        samples, render_samples, plan_samples, projection_samples = [], [], [], []
        for _ in range(300):
            start = time.perf_counter()
            consultation.build_reply_plan(planned, g["assessment"], next((r for r in book.characters
                if r["identity_id"] in planned.character_ids), None), query=query, guides=g.get("guides") or [])
            samples.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            plan_guidance(query, build_query_plan(query, intent), book=book, catalog=catalog)
            plan_samples.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            if hasattr(consultation, "model_guidance"):
                consultation.model_guidance(g)
            projection_samples.append((time.perf_counter() - start) * 1000)
        for _ in range(100):
            start = time.perf_counter()
            render_followup(data, plan=observed, ready=True)
            render_samples.append((time.perf_counter() - start) * 1000)
        projected = consultation.model_guidance(g) if hasattr(consultation, "model_guidance") else g
        timings.append({"case": name, "selection_median_ms": statistics.median(samples),
            "planning_median_ms": statistics.median(plan_samples),
            "model_projection_median_ms": statistics.median(projection_samples),
            "selection_max_ms": max(samples), "formatter_cold_ms": cold_ms,
            "formatter_warm_median_ms": statistics.median(render_samples),
            "guidance_chars_internal": len(json.dumps(g, ensure_ascii=False)),
            "guidance_chars_model": len(json.dumps(projected, ensure_ascii=False)), "body_chars": len(text)})
        assert data["guidance_answer"] == g
    paths = [Path(sys.modules[build_guidance.__module__].__file__).parent / f for f in (
        "guidance.py", "consultation.py", "consultation_contract.py", "answer_message.py", "cone_message.py")]
    report = {"candidate": BUILD_REVISION, "frozen": args.frozen,
        "scope": "Synthetic facts and real local formatter; no OCR/model/host speech latency claim",
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "cases": cases, "timings": timings}
    output = plugin / ("dist/reply-delivery-probe-before-20261023.json" if args.frozen else
                       "dist/reply-delivery-probe-after-20261023.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "unspoken_optional_notice": cases["unspoken_optional_notice"],
        "full_next_in_body": cases["full"]["next_step_in_body"], "full_receipt": cases["full"]["receipt"],
        "timings": timings}, ensure_ascii=False))


if __name__ == "__main__":
    main()
