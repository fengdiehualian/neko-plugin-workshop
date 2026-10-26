import copy

from test_providers import load

layout = load("host_layout")
MODEL = {"success": True, "model_info": {"path": "/static/yui/yui.model3.json"}}
ROW = {"model_path": "/static/yui/yui.model3.json", "position": {"x": 2211, "y": 1107},
       "scale": {"x": 0.043, "y": 0.043}, "viewport": {"width": 2559, "height": 1439}}


def test_select_current_model_and_never_return_unrelated_preferences():
    other = dict(ROW, model_path="/static/another/another.model3.json")
    row = dict(ROW, api_key="must not escape", character_notes="must not escape")
    result = layout.current_layout(MODEL, [other, {"model_path": "__global_conversation__"}, row])
    assert result == {"available": True, **{key: ROW[key] for key in ("position", "viewport", "scale")}}
    assert layout.current_layout(MODEL, [other]) == {"available": False}


def test_reject_corrupt_layout_instead_of_pointing_at_another_application():
    for key, values in {"position": [("x", float("nan")), ("y", True), ("x", 100001)],
                        "viewport": [("width", 0), ("height", "1439")],
                        "scale": [("x", -1), ("y", float("inf"))]}.items():
        for field, value in values:
            row = copy.deepcopy(ROW)
            row[key][field] = value
            assert layout.current_layout(MODEL, [row]) == {"available": False}
    for value in (None, {}, [None], [dict(ROW, position=None)]):
        assert layout.current_layout(MODEL, value) == {"available": False}


def test_layout_failure_does_not_reuse_previous_model():
    calls = []
    def request(path):
        calls.append(path)
        return MODEL if path.endswith("current_live2d_model") else [ROW]
    reader = layout.HostLayout(request)
    assert reader.snapshot()["available"]
    assert reader.snapshot()["available"]
    assert len(calls) == 2
    reader.read_at = 0
    reader.request = lambda path: (_ for _ in ()).throw(OSError("offline"))
    assert reader.snapshot() == {"available": False}
