"""Cloud-first parts and the team, end to end against a real openswx-collab server (the commands behind Home's
Cloud tab): Ada makes an organisation, a project and invitations, Bob joins; a part made straight in the cloud
opens for Bob after Ada's openswx has quit; saving a cloud part writes a local copy; and with the server gone a
cloud part neither opens nor is made, leaving nothing behind.

Same requirements as test_collab_live.py (an openswx-collab checkout with its environment). The tests run in
order and share the server and the two peers.
"""

import pytest

from test_collab_live import SERVER_PY, Peer, Server, httpx  # noqa: F401 (skips like test_collab_live)

pytestmark = pytest.mark.skipif(not SERVER_PY.exists(), reason="no openswx-collab server")
PW = "correct horse"


@pytest.fixture(scope="module")
def world():
    server = Server()
    server.start()
    peers = []
    try:
        http = httpx.Client(base_url=server.url, timeout=30)
        http.post("/api/auth/register", json={"email": "ada@example.com", "password": PW, "name": "Ada"}).raise_for_status()
        peers = [Peer("ada", "1"), Peer("bob", "4242")]
        peers[0].call("collab_sign_in", server=server.url, email="ada@example.com", password=PW)
        yield {"server": server, "a": peers[0], "b": peers[1], "http": http, "state": {}}
    finally:
        for p in peers:
            p.close()
        server.stop()


def test_ada_makes_an_organisation_and_a_project(world):
    a, s = world["a"], world["state"]
    org = a.call("collab_create_org", name="Robotics")
    assert org["role"] == "owner"
    s["org"] = org["id"]
    s["project"] = a.call("collab_create_project", name="Rover", org=org["id"])["id"]
    assert "give the organisation a name" in a.fails("collab_create_org", name="  ")


def test_an_invitation_brings_bob_in_with_a_project_role(world):
    a, b, s, http = world["a"], world["b"], world["state"], world["http"]
    inv = a.call("collab_invite", org="Robotics", email="bob@example.com", project="Rover", project_role="editor")
    assert inv["url"].endswith("/" + inv["code"]) and inv["role"] == "member"
    http.post("/api/auth/register", json={"email": "bob@example.com", "password": PW, "name": "Bob",
                                          "invite": inv["code"]}).raise_for_status()
    b.call("collab_sign_in", server=world["server"].url, email="bob@example.com", password=PW)
    m = a.call("collab_members", org=s["org"])
    assert {x["name"]: x["role"] for x in m["members"]} == {"Ada": "owner", "Bob": "member"}
    s["bob"] = next(x["id"] for x in m["members"] if x["name"] == "Bob")
    assert m["invites"] == []  # (accepted)
    pm = a.call("collab_project_members", project="Rover")["members"]
    assert {x["name"]: x["role"] for x in pm} == {"Ada": "owner", "Bob": "editor"}
    # a member sees the team but not the invitations
    assert b.call("collab_members")["invites"] == []


def test_roles_change_and_invitations_can_be_withdrawn(world):
    a, b, s = world["a"], world["b"], world["state"]
    assert a.call("collab_set_role", org="Robotics", user=s["bob"], role="admin")["role"] == "admin"
    assert "only an owner" in b.fails("collab_set_role", org="Robotics", user=s["bob"], role="owner")
    open_inv = a.call("collab_invite", org="Robotics")  # (no address: anyone with the link)
    assert open_inv["email"] is None and open_inv["code"]
    listed = b.call("collab_members", org="Robotics")["invites"]  # (Bob is an admin now: he sees them)
    assert [i["id"] for i in listed] == [open_inv["id"]] and "token" not in listed[0] and "code" not in listed[0]
    b.call("collab_cancel_invite", org="Robotics", invite=open_inv["id"])
    assert a.call("collab_members")["invites"] == []
    assert "not a valid invitation id" in a.fails("collab_cancel_invite", invite="../../orgs")
    a.call("collab_set_project_role", project="Rover", user=s["bob"], role="viewer")
    assert {x["name"]: x["role"] for x in a.call("collab_project_members", project="Rover")["members"]}["Bob"] == "viewer"
    a.call("collab_remove_project_member", project="Rover", user=s["bob"])
    assert [x["name"] for x in a.call("collab_project_members", project="Rover")["members"]] == ["Ada"]
    a.call("collab_set_project_role", project="Rover", user=s["bob"], role="editor")


def test_bob_joins_a_second_organisation_with_the_link(world):
    a, b = world["a"], world["b"]
    lab = a.call("collab_create_org", name="Lab")
    link = a.call("collab_invite", org=lab["id"])["url"]
    joined = b.call("collab_accept_invite", code=link + "/")
    assert joined["org"]["name"] == "Lab" and joined["role"] == "member"
    assert "not valid" in b.fails("collab_accept_invite", code=link)  # (used)
    assert sorted(o["name"] for o in b.call("collab_projects")["orgs"]) == ["Lab", "Robotics"]
    b.call("collab_remove_member", org="Lab", user=b.call("collab_status")["user"]["id"])  # (leaves it)
    assert [o["name"] for o in b.call("collab_projects")["orgs"]] == ["Robotics"]


def test_a_part_made_in_the_cloud_opens_for_bob_after_ada_has_gone(world):
    a, b, s = world["a"], world["b"], world["state"]
    made = a.call("collab_new_part", project="Rover", name="Wheel")
    assert made["name"] == "Wheel" and made["project"] == "Rover" and made["role"] == "owner"
    assert a.call("list_documents")["active"] == "Wheel"
    sk = a.call("create_sketch", plane="Front")["sketch"]
    a.call("sketch_circle", sketch=sk, center=[0, 0], radius=30)
    a.call("extrude", sketch=sk, depth=12)
    docs = a.call("list_documents")["documents"]
    assert [d["dirty"] for d in docs if d["name"] == "Wheel"] == [False]  # (kept by the server: nothing to save)
    assert a.ask(settle=None)["digest"]["pending"] == 0
    a.close()  # (Ada's openswx quits)
    listed = b.call("collab_documents", project="Rover")["documents"]
    wheel = next(d for d in listed if d["name"] == "Wheel")
    assert wheel["online"] == [] and wheel["online_count"] == 0
    b.call("collab_open", document_id=wheel["id"])
    s["wheel"] = wheel["id"]
    assert [f["name"] for f in b.call("get_model")["features"]] == ["Sketch1", "Boss-Extrude1"]


def test_saving_a_cloud_part_writes_a_local_copy(world, tmp_path):
    b = world["b"]
    assert "save automatically" in b.fails("save_part")
    out = b.call("save_part", path=str(tmp_path / "wheel-copy"))
    assert out["copy"] is True and out["saved"].endswith("wheel-copy.oswprt") and (tmp_path / "wheel-copy.oswprt").exists()
    docs = b.call("list_documents")
    assert docs["active"] == "Wheel" and [d["path"] for d in docs["documents"] if d["name"] == "Wheel"] == [None]
    assert b.call("collab_status")["documents"][0]["document"] == "Wheel"  # (still the cloud part)


def test_with_the_server_gone_a_cloud_part_neither_opens_nor_is_made(world):
    b, s = world["b"], world["state"]
    b.call("collab_disable")
    b.call("close_document")
    before = b.call("list_documents")["documents"]
    world["server"].stop()
    for err in (b.fails("collab_open", document_id=s["wheel"]), b.fails("collab_new_part", project="Rover", name="Hub")):
        assert "cloud parts open only while connected" in err
    assert b.call("list_documents")["documents"] == before  # (nothing left behind)
    assert "not a valid document id" in b.fails("collab_open", document_id="../x")
