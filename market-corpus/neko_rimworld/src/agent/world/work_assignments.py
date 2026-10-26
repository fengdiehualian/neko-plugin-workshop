"""Observed work tables, assignment summaries and same-sample pagination."""

import copy

from ...serialization import canonical


def observed_work(fact):
    values = fact.get("facts", {})
    if isinstance(values.get("work"), dict):
        return values["work"]
    state = values.get("state", {})
    if not isinstance(state, dict):
        return {}
    return state.get("work", state.get("facets", {}).get("work", {}))


def summarize_work_assignments(facts, coverage):
    """Count existing assignment rows, never infer runnable jobs or missing zeros."""
    actors=set()
    unknown=set()
    by_type={}
    complete=True
    activities={}
    precedence=[]
    for fact in facts:
        ident=fact.get("object",{}).get("id")
        if not isinstance(ident,str):
            complete=False
            continue
        if ident in actors:
            complete=False
        actors.add(ident)
        work=observed_work(fact)
        table=work.get("work_types",[]) if isinstance(work,dict) else []
        if not isinstance(table,list):
            table=[]
        job=work.get("current_job") if isinstance(work,dict) else None
        activities[ident]=job.get("definition") if isinstance(job,dict) else None
        assigned=[row for row in table if isinstance(row,dict) and type(row.get("priority")) is int and row["priority"]>0]
        order_known=all(type(row.get("natural_priority")) in (int,float) for row in assigned)
        precedence.append({"actor_id":ident,"current_job":activities[ident],
            "normal_assigned_type_order":[{"definition":row.get("definition"),"priority":row["priority"],"natural_priority":row["natural_priority"]}
                for row in sorted(assigned,key=lambda row:(row["priority"],-row["natural_priority"],row.get("definition","")))] if order_known else None,
            "order_known":order_known})
        if not isinstance(work,dict) or work.get("status")!="observed" or work.get("work_types_complete") is not True:
            complete=False
            unknown.add(ident)
        for row in table:
            if not isinstance(row,dict):
                complete=False
                unknown.add(ident)
                continue
            definition=row.get("definition")
            if not isinstance(definition,str):
                complete=False
                unknown.add(ident)
                continue
            entry=by_type.setdefault(definition,{"assigned":set(),"disabled":set(),"unassigned":set()})
            priority=row.get("priority")
            if type(priority) is int and priority>0:
                entry["assigned"].add(ident)
            elif priority==0 and type(priority) is int:
                entry["unassigned"].add(ident)
            elif type(priority) is not int:
                complete=False
                unknown.add(ident)
            if row.get("disabled") is True:
                entry["disabled"].add(ident)
    exact=bool(actors) and complete and coverage.get("listing")=="complete" and coverage.get("matched")==len(actors)
    return {
        "scope":"Objects actually read for this look selection, before display-size cropping; not automatically the whole colony.",
        "actor_ids":sorted(actors),"unknown_or_incomplete_work_actor_ids":sorted(unknown),
        "complete_for_selection":exact,
        "actor_readings":precedence,
        "types":[{"definition":name,"assigned_count":len(value["assigned"]) if exact else None,
            "assigned_lower_bound":len(value["assigned"]),"assigned_actor_ids":sorted(value["assigned"]),
            "unassigned_count":len(value["unassigned"]) if exact else None,"unassigned_actor_ids":sorted(value["unassigned"]),
            "wandering_assigned_actor_ids":sorted(a for a in value["assigned"] if activities.get(a)=="GotoWander"),
            "disabled_actor_ids":sorted(value["disabled"])} for name,value in sorted(by_type.items())],
        "meaning":"Derived from original native work tables retained in facts. Assigned means observed priority > 0; wandering is the observed GotoWander job, not proof of action eligibility. Normal assigned-type order uses lower assigned priority first, then higher natural_priority, not array position; actual available Jobs and emergency work still matter. Null counts mean incomplete coverage. Assignment does not explain why a pawn is idle."
    }

class WorkReadings:
    """An observation-backed work table across pages of one unchanged sample."""

    def __init__(self):
        self.key = None
        self.facts = {}
        self.positions = set()
        self.sources = []

    def summarize(self, reading):
        sample = reading.get("sample", {})
        tick = sample.get("game_tick")
        state_samples = reading.get("state_observations", [])
        stable = bool(state_samples) and type(tick) is int and all(
            source.get("sample", {}).get("load_epoch") == sample.get("load_epoch")
            and source.get("sample", {}).get("started_tick") == tick
            and source.get("sample", {}).get("finished_tick") == tick
            and not source.get("changed_during_read")
            for source in state_samples
        )
        key = canonical({"scope": reading.get("scope", {}), "load_epoch": sample.get("load_epoch"), "tick": tick}) if stable and sample.get("load_epoch") else None
        if key is None:
            self.__init__()
            return summarize_work_assignments(reading["facts"], reading["coverage"])
        if key != self.key:
            self.__init__()
            self.key = key
        for fact in reading["facts"]:
            ident = fact.get("object", {}).get("id")
            if isinstance(ident, str):
                # Keep work only; earlier native observations retain its provenance.
                self.facts[ident] = {"object": {"id": ident}, "facts": {"work": copy.deepcopy(observed_work(fact))}}
        page = reading.get("page", {})
        offset = page.get("offset", 0)
        if type(offset) is int and offset >= 0:
            self.positions.update(range(offset, offset + len(reading["facts"])))
        source = {"selection_sample": copy.deepcopy(sample), "page": copy.deepcopy(page),
                  "state_observation_ids": [s.get("observation_id") for s in state_samples]}
        if source not in self.sources:
            self.sources.append(source)
        coverage = copy.deepcopy(reading["coverage"])
        matched = coverage.get("matched")
        covered = type(matched) is int and len(self.facts) == matched and self.positions == set(range(matched))
        coverage["listing"] = "complete" if covered else "partial"
        result = summarize_work_assignments(list(self.facts.values()), coverage)
        result["scope"] = "Union of work tables read in this consultation for the same selection, load epoch and unchanged game tick; not an atomic snapshot."
        result["source_pages"] = copy.deepcopy(self.sources)
        result["sample"] = {"load_epoch": sample["load_epoch"], "game_tick": tick, "atomic": False}
        return result
