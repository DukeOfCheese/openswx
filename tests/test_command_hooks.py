import pytest

from openswx import session as S
from openswx.core.document import DocumentError


class Recorder:
    def __init__(self, refuse=None, redirect_to=None):
        self.calls, self.refuse, self.redirect_to = [], refuse, redirect_to

    def before(self, name, arguments):
        if name == self.refuse:
            raise DocumentError(f"{name} refused by the hook")
        doc = S.APP.documents.get(S.APP.active)
        self.calls.append(("before", name))
        return name, len(doc.features) if doc is not None else 0

    def after(self, token, ok):
        name, n = token
        doc = S.APP.documents.get(S.APP.active)
        self.calls.append(("after", name, ok, len(doc.features) - n if doc is not None else 0))

    def redirect(self, name, doc):
        return self.redirect_to if name in ("undo", "redo") else None


@pytest.fixture
def hook():
    h = Recorder()
    S.add_command_hook(h)
    yield h
    S.remove_command_hook(h)


def _block():
    S.call("new_part", {"name": "Hooked"})
    sk = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_rectangle", {"sketch": sk, "corner1": [0, 0], "corner2": [40, 20]})
    return sk, S.call("extrude", {"sketch": sk, "depth": 10})["feature"]


def test_hooks_see_each_outermost_mutating_command_once(hook):
    sk, ext = _block()
    assert hook.calls[-2:] == [("before", "extrude"), ("after", "extrude", True, 1)]
    hook.calls.clear()
    S.call("get_model")  # (a query: not a change)
    assert hook.calls == []
    S.call("batch", {"commands": [{"name": "edit_feature", "args": {"name": ext, "params": {"depth": 12}}},
                                  {"name": "edit_feature", "args": {"name": ext, "params": {"depth": 14}}}]})
    assert hook.calls == [("before", "batch"), ("after", "batch", True, 0)]  # (the inner commands are not seen)
    hook.calls.clear()
    S.preview("edit_feature", {"name": ext, "params": {"depth": 30}})  # (a throw-away preview)
    assert hook.calls == []


def test_a_preview_of_redefining_a_feature_changes_nothing(hook):
    """The Edit Feature panel previews redefine_feature, which itself runs the creation command as a preview: the
    outer preview must stay one (no log entry, no new version, no hook, the document unchanged)."""
    sk, ext = _block()
    hook.calls.clear()
    version, last, depth = S.APP.version, S.APP.log[-1], S.APP.doc.find(ext)["params"]["depth"]
    mesh = S.preview("redefine_feature", {"name": ext, "command_name": "extrude", "args": {"sketch": sk, "depth": 30}})
    assert mesh["ok"] and hook.calls == []
    assert S.APP.version == version and S.APP.log[-1] is last
    assert S.APP.doc.find(ext)["params"]["depth"] == depth


def test_hooks_hear_when_a_command_fails(hook):
    _block()
    hook.calls.clear()
    with pytest.raises(S.CommandError):
        S.call("edit_feature", {"name": "NoSuchFeature", "params": {"depth": 1}})
    assert hook.calls == [("before", "edit_feature"), ("after", "edit_feature", False, 0)]


def test_a_hook_can_refuse_a_command(hook):
    _block()
    hook.refuse = "extrude"
    n = len(S.APP.doc.features)
    with pytest.raises(S.CommandError, match="refused by the hook"):
        S.call("extrude", {"sketch": "Sketch1", "depth": 5})
    assert len(S.APP.doc.features) == n


def test_undo_asks_the_hooks_first(hook):
    _block()
    hook.redirect_to = {"undone": "elsewhere"}
    assert S.call("undo") == {"undone": "elsewhere"}
    assert S.call("redo") == {"undone": "elsewhere"}
    hook.redirect_to = None
    n = len(S.APP.doc.features)
    S.call("undo")  # (no hook takes it: the document's own undo)
    assert len(S.APP.doc.features) == n - 1


def test_without_hooks_nothing_is_watched():
    assert S.COMMAND_HOOKS == []
    _, ext = _block()
    assert S.APP.doc.find(ext)["id"].startswith("f")


def test_an_allocator_hands_out_feature_ids_and_names():
    class Pool:
        def __init__(self):
            self.ids, self.names = iter(["f101", "f102"]), {"Sketch": iter(["Sketch7"]), "Boss-Extrude": iter(["Boss-Extrude9"])}

        def take_id(self, doc):
            return next(self.ids)

        def take_name(self, doc, prefix):
            return next(self.names[prefix])

    S.call("new_part", {"name": "Pooled"})
    S.APP.doc.allocator = Pool()
    sk = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_rectangle", {"sketch": sk, "corner1": [0, 0], "corner2": [40, 20]})
    ext = S.call("extrude", {"sketch": sk, "depth": 10})["feature"]
    assert (sk, ext) == ("Sketch7", "Boss-Extrude9")
    assert [f["id"] for f in S.APP.doc.features] == ["f101", "f102"]
    assert S.APP.doc.next_id == 1  # (the document's own counter is not used)
