"""画布图规则：连线类型约束、运行输入的收集顺序、输出节点挂载。"""

import pytest

from lib.canvas.graph import (
    CanvasGraphError,
    add_node,
    add_run_outputs,
    connect,
    empty_graph,
    make_node,
    patch_node,
    remove_node,
    resolve_generate_inputs,
)


def _add(graph, node_type, x=0, y=0, path=None, **data):
    server_data = {"path": path} if path else None
    return add_node(graph, make_node(node_type, {"x": x, "y": y}, data, server_data=server_data))


def test_connect_requires_matching_handle_types():
    graph = empty_graph()
    text = _add(graph, "text", text="a cat")
    image = _add(graph, "image", path="canvas/uploads/a.png")
    gen = _add(graph, "image_generate")

    connect(graph, text["id"], "text", gen["id"], "prompt")
    connect(graph, image["id"], "image", gen["id"], "image1")
    with pytest.raises(CanvasGraphError):
        connect(graph, text["id"], "text", gen["id"], "image1")
    with pytest.raises(CanvasGraphError):
        connect(graph, image["id"], "image", gen["id"], "prompt")


def test_connect_is_idempotent_and_rejects_self_loop():
    graph = empty_graph()
    text = _add(graph, "text")
    gen = _add(graph, "image_generate")
    first = connect(graph, text["id"], "text", gen["id"], "prompt")
    again = connect(graph, text["id"], "text", gen["id"], "prompt")
    assert first["id"] == again["id"]
    assert len(graph["edges"]) == 1
    with pytest.raises(CanvasGraphError):
        connect(graph, gen["id"], "text", gen["id"], "prompt")


def test_generate_inputs_follow_canvas_order_and_skip_unfinished_images():
    graph = empty_graph()
    low = _add(graph, "text", y=200, text="second")
    high = _add(graph, "text", y=10, text="first")
    blank = _add(graph, "text", y=50, text="  ")
    done = _add(graph, "image", path="canvas/uploads/a.png")
    pending = _add(graph, "image")
    gen = _add(graph, "image_generate")
    for node in (low, high, blank):
        connect(graph, node["id"], "text", gen["id"], "prompt")
    for node in (done, pending):
        connect(graph, node["id"], "image", gen["id"], "image1")

    prompt, refs = resolve_generate_inputs(graph, gen["id"])

    assert prompt == "first\n\nsecond"
    assert refs == ["canvas/uploads/a.png"]


def test_run_outputs_stack_on_repeat_runs_and_attach_to_generator():
    graph = empty_graph()
    gen = _add(graph, "image_generate", x=100, y=0)

    image1 = add_run_outputs(graph, gen["id"], "p")
    image2 = add_run_outputs(graph, gen["id"], "p")

    assert image1["data"]["status"] == "queued"
    assert image2["position"]["y"] > image1["position"]["y"]
    edges = {(e["sourceHandle"], e["target"]) for e in graph["edges"] if e["source"] == gen["id"]}
    assert edges == {("image", image1["id"]), ("image", image2["id"])}


def test_patch_ignores_server_owned_fields():
    graph = empty_graph()
    node = _add(graph, "image_generate")
    patch_node(graph, node["id"], data={"aspect_ratio": "16:9", "status": "done", "path": "x"})
    assert node["data"]["aspect_ratio"] == "16:9"
    assert node["data"]["status"] == "idle"
    assert "path" not in node["data"]


def test_remove_busy_node_refused_and_edges_cleaned():
    graph = empty_graph()
    text = _add(graph, "text")
    gen = _add(graph, "image_generate")
    connect(graph, text["id"], "text", gen["id"], "prompt")
    remove_node(graph, text["id"])
    assert graph["edges"] == []
    gen["data"]["status"] = "running"
    with pytest.raises(CanvasGraphError):
        remove_node(graph, gen["id"])


def test_derived_nodes_connect_by_kind_and_feed_run_inputs():
    from lib.canvas.graph import derived_id

    graph = empty_graph()
    gen = _add(graph, "image_generate")
    sheet = derived_id("char_sheet", "Hero")
    connect(graph, sheet, "image", gen["id"], "image1")
    with pytest.raises(CanvasGraphError):
        connect(graph, sheet, "image", gen["id"], "prompt")

    derived = {
        sheet: {"id": sheet, "type": "image", "position": {"x": 0, "y": 0}, "data": {"path": "characters/Hero.png"}}
    }
    assert resolve_generate_inputs(graph, gen["id"], derived) == ("", ["characters/Hero.png"])
    # 派生节点没加载时，指向它的连线被跳过而不是报错。
    assert resolve_generate_inputs(graph, gen["id"], {}) == ("", [])


def test_generated_image_goes_stale_when_prompt_inputs_change():
    from lib.canvas.graph import stale_generated_images

    graph = empty_graph()
    text = _add(graph, "text", text="a fox")
    gen = _add(graph, "image_generate")
    connect(graph, text["id"], "text", gen["id"], "prompt")
    image = add_run_outputs(graph, gen["id"], "a fox")
    image["data"]["status"] = "done"
    assert stale_generated_images(graph, {}) == set()
    text["data"]["text"] = "a red fox"
    assert stale_generated_images(graph, {}) == {image["id"]}


def test_reference_labels_follow_the_reference_order_and_build_a_legend():
    from lib.canvas.graph import derived_id, reference_legend, resolve_reference_labels

    graph = empty_graph()
    gen = _add(graph, "image_generate")
    first = add_node(graph, make_node("image", {"x": 0, "y": 10}, {"label": "hero.png"}, server_data={"path": "a.png"}))
    sheet = derived_id("char_sheet", "Hero")
    connect(graph, first["id"], "image", gen["id"], "image1")
    connect(graph, sheet, "image", gen["id"], "image1")
    derived = {
        sheet: {
            "id": sheet,
            "type": "image",
            "position": {"x": 0, "y": 99},
            "data": {"path": "k.png", "name": "Hero"},
        }
    }

    labels = resolve_reference_labels(graph, gen["id"], derived)

    assert labels == ["hero.png", "Hero"]
    assert reference_legend(labels).endswith("图1: hero.png\n图2: Hero")
    assert reference_legend([]) == ""


def _finished_run(graph, generator_id, path, prompt="a fox"):
    image = add_run_outputs(graph, generator_id, prompt)
    image["data"].update(status="done", path=path)
    return image


def test_connecting_a_generator_feeds_downstream_with_its_latest_finished_image():
    graph = empty_graph()
    upstream = _add(graph, "image_generate")
    downstream = _add(graph, "image_generate")
    connect(graph, upstream["id"], "image", downstream["id"], "image1")
    assert resolve_generate_inputs(graph, downstream["id"])[1] == []  # 上游还没跑过
    _finished_run(graph, upstream["id"], "canvas/images/old.png")
    _finished_run(graph, upstream["id"], "canvas/images/new.png")
    assert resolve_generate_inputs(graph, downstream["id"])[1] == ["canvas/images/new.png"]


def test_branch_plan_lists_only_upstream_nodes_that_need_a_run():
    from lib.canvas.graph import branch_plan

    graph = empty_graph()
    text = _add(graph, "text", text="a fox")
    first = _add(graph, "image_generate")
    second = _add(graph, "image_generate")
    connect(graph, text["id"], "text", first["id"], "prompt")
    connect(graph, first["id"], "image", second["id"], "image1")

    plan = branch_plan(graph, second["id"])
    assert [(p["id"], p["reason"]) for p in plan] == [(first["id"], "missing"), (second["id"], "target")]

    _finished_run(graph, first["id"], "canvas/images/a.png")
    assert [p["id"] for p in branch_plan(graph, second["id"])] == [second["id"]]

    text["data"]["text"] = "a red fox"  # 上游输入变了 → 它的产物过期，需要先重跑
    first_plan = branch_plan(graph, second["id"])
    assert [(p["id"], p["reason"]) for p in first_plan] == [(first["id"], "stale"), (second["id"], "target")]


def test_template_round_trip_keeps_structure_but_not_outputs_or_status():
    from lib.canvas.graph import instantiate_template, make_template

    graph = empty_graph()
    text = _add(graph, "text", x=100, y=50, text="a fox")
    gen = _add(graph, "image_generate", x=500, y=50, aspect_ratio="16:9")
    connect(graph, text["id"], "text", gen["id"], "prompt")
    _finished_run(graph, gen["id"], "canvas/images/a.png")
    gen["data"]["status"] = "done"
    everything = [n["id"] for n in graph["nodes"]]

    template = make_template(graph, "fox", everything)

    assert sorted(n["type"] for n in template["nodes"]) == ["image_generate", "text"]
    assert len(template["edges"]) == 1
    assert all("status" not in n["data"] and "path" not in n["data"] for n in template["nodes"])

    fresh = empty_graph()
    created = instantiate_template(fresh, template, {"x": 1000, "y": 0})
    assert {n["data"].get("aspect_ratio") for n in created if n["type"] == "image_generate"} == {"16:9"}
    assert min(n["position"]["x"] for n in created) == 1000
    assert fresh["edges"][0]["sourceHandle"] == "text"
    assert {n["id"] for n in created}.isdisjoint(everything)
    prompt, _ = resolve_generate_inputs(fresh, next(n["id"] for n in created if n["type"] == "image_generate"))
    assert prompt == "a fox"


def test_empty_template_is_refused():
    from lib.canvas.graph import make_template

    graph = empty_graph()
    image = add_node(graph, make_node("image", {"x": 0, "y": 0}, server_data={"path": "a.png"}))
    with pytest.raises(CanvasGraphError):
        make_template(graph, "x", [image["id"]])


def test_video_and_audio_providers_wire_by_media_type_and_emit_matching_outputs():
    graph = empty_graph()
    text = _add(graph, "text", text="a duel")
    frame = add_node(graph, make_node("image", {"x": 0, "y": 0}, server_data={"path": "canvas/images/a.png"}))
    video = _add(graph, "video_generate")
    audio = _add(graph, "audio_generate")
    connect(graph, text["id"], "text", video["id"], "prompt")
    connect(graph, frame["id"], "image", video["id"], "frame")
    connect(graph, text["id"], "text", audio["id"], "text")
    with pytest.raises(CanvasGraphError):
        connect(graph, frame["id"], "image", video["id"], "prompt")
    with pytest.raises(CanvasGraphError):
        connect(graph, frame["id"], "image", audio["id"], "text")

    assert resolve_generate_inputs(graph, video["id"]) == ("a duel", ["canvas/images/a.png"])
    assert resolve_generate_inputs(graph, audio["id"]) == ("a duel", [])

    clip = add_run_outputs(graph, video["id"], "a duel")
    voice = add_run_outputs(graph, audio["id"], "a duel")
    assert (clip["type"], voice["type"]) == ("video", "audio")
    handles = {(e["source"], e["sourceHandle"]) for e in graph["edges"] if e["target"] in {clip["id"], voice["id"]}}
    assert handles == {(video["id"], "video"), (audio["id"], "audio")}


def test_a_video_provider_can_take_a_generators_latest_image_as_its_start_frame():
    graph = empty_graph()
    text = _add(graph, "text", text="a fox")
    upstream = _add(graph, "image_generate")
    video = _add(graph, "video_generate")
    connect(graph, text["id"], "text", video["id"], "prompt")
    connect(graph, upstream["id"], "image", video["id"], "frame")
    _finished_run(graph, upstream["id"], "canvas/images/frame.png")
    assert resolve_generate_inputs(graph, video["id"])[1] == ["canvas/images/frame.png"]


def test_character_sheet_nodes_feed_references_and_label_them_by_name():
    from lib.canvas.graph import derived_id, reference_legend, resolve_reference_labels

    graph = empty_graph()
    gen = _add(graph, "image_generate")
    sheet = derived_id("char_sheet", "阿离")
    connect(graph, sheet, "image", gen["id"], "image1")
    with pytest.raises(CanvasGraphError):
        connect(graph, sheet, "image", gen["id"], "prompt")
    derived = {
        sheet: {
            "id": sheet,
            "type": "image",
            "position": {"x": 0, "y": 0},
            "data": {"name": "阿离", "label": "阿离", "path": "characters/阿离.png"},
        }
    }

    assert resolve_generate_inputs(graph, gen["id"], derived)[1] == ["characters/阿离.png"]
    assert reference_legend(resolve_reference_labels(graph, gen["id"], derived)).endswith("图1: 阿离")
    # 角色还没有资产图时不提供参考图。
    derived[sheet]["data"]["path"] = None
    assert resolve_generate_inputs(graph, gen["id"], derived)[1] == []


def test_the_character_generator_output_resolves_to_its_sheet_node():
    from lib.canvas.graph import derived_id

    graph = empty_graph()
    gen = _add(graph, "image_generate")
    char_gen = derived_id("char_gen", "阿离")
    sheet = derived_id("char_sheet", "阿离")
    connect(graph, char_gen, "image", gen["id"], "image1")
    derived = {
        sheet: {"id": sheet, "type": "image", "position": {"x": 0, "y": 0}, "data": {"path": "characters/阿离.png"}}
    }
    assert resolve_generate_inputs(graph, gen["id"], derived)[1] == ["characters/阿离.png"]


def test_legacy_graphs_migrate_refs_to_image1_and_drop_prompt_copy_nodes():
    from lib.canvas.graph import normalize_legacy

    graph = empty_graph()
    gen = _add(graph, "image_generate")
    ref = add_node(graph, make_node("image", {"x": 0, "y": 0}, server_data={"path": "a.png"}))
    graph["edges"].append(
        {"id": "e1", "source": ref["id"], "sourceHandle": "image", "target": gen["id"], "targetHandle": "refs"}
    )
    copy = add_node(graph, make_node("text", {"x": 0, "y": 0}, {"text": "p", "generated": True}))
    graph["edges"].append(
        {"id": "e2", "source": gen["id"], "sourceHandle": "text", "target": copy["id"], "targetHandle": "in"}
    )

    normalize_legacy(graph)

    assert [e["targetHandle"] for e in graph["edges"]] == ["image1"]
    assert all(n["id"] != copy["id"] for n in graph["nodes"])


def test_a_derivative_generator_output_resolves_to_its_sheet_and_is_labelled_with_its_path():
    from lib.canvas.graph import derived_id, resolve_reference_labels

    graph = empty_graph()
    gen = _add(graph, "image_generate")
    d_gen = derived_id("deriv_gen", "阿离/幼年")
    d_sheet = derived_id("deriv_sheet", "阿离/幼年")
    connect(graph, d_gen, "image", gen["id"], "image1")
    derived = {
        d_sheet: {
            "id": d_sheet,
            "type": "image",
            "position": {"x": 0, "y": 0},
            "data": {"path": "characters/derivatives/阿离/幼年.png", "label": "阿离/幼年"},
        }
    }
    assert resolve_generate_inputs(graph, gen["id"], derived)[1] == ["characters/derivatives/阿离/幼年.png"]
    assert resolve_reference_labels(graph, gen["id"], derived) == ["阿离/幼年"]


def test_run_outputs_record_when_the_run_was_submitted() -> None:
    from lib.canvas.graph import STATUS_DONE, STATUS_RUNNING, add_run_outputs, status_timestamps

    graph = {"nodes": [], "edges": [], "revision": 0}
    source = add_node(graph, make_node("image_generate", {"x": 0, "y": 0}))

    media = add_run_outputs(graph, source["id"], "p")

    assert media["data"]["submitted_at"] == media["data"]["status_at"]
    assert set(status_timestamps({"status": STATUS_RUNNING})) == {"status_at", "started_at"}
    assert set(status_timestamps({"status": STATUS_DONE})) == {"status_at", "finished_at"}
    assert status_timestamps({"error": None}) == {}


def test_reference_note_travels_with_the_image_into_the_legend() -> None:
    from lib.canvas.graph import apply_notes, default_note, resolve_reference_labels, set_note

    graph = {"nodes": [], "edges": [], "notes": {}, "revision": 0}
    generator = add_node(graph, make_node("image_generate", {"x": 0, "y": 0}))
    sheet = {
        "id": "p:char_sheet:阿离",
        "type": "image",
        "derived": True,
        "position": {"x": 0, "y": 0},
        "data": {"name": "阿离", "asset": "character", "role": "sheet", "path": "characters/a.png"},
    }
    derived = {sheet["id"]: sheet}
    graph["edges"].append(
        {"id": "e", "source": sheet["id"], "sourceHandle": "image", "target": generator["id"], "targetHandle": "image1"}
    )

    apply_notes([sheet], graph["notes"])
    assert sheet["data"]["note"] == default_note(sheet["data"]) == "Character sheet of 阿离."
    assert resolve_reference_labels(graph, generator["id"], derived) == ["Character sheet of 阿离."]

    set_note(graph, sheet["id"], "This is his face, front view.")
    apply_notes([sheet], graph["notes"])
    assert resolve_reference_labels(graph, generator["id"], derived) == ["This is his face, front view."]

    set_note(graph, sheet["id"], "")
    assert graph["notes"] == {}


def test_note_on_a_stored_image_node_lives_in_its_data() -> None:
    from lib.canvas.graph import set_note

    graph = {"nodes": [], "edges": [], "notes": {}, "revision": 0}
    image = add_node(graph, make_node("image", {"x": 0, "y": 0}))

    set_note(graph, image["id"], "  a cat  ")
    assert image["data"]["note"] == "a cat"
    set_note(graph, image["id"], "")
    assert "note" not in image["data"]


def _asset(graph, name, path, note="", derivative=None, kind="character"):
    """画布上一个资产：资产分组（标题 = 资产名）里一张图接到资产节点。同名分组只建一次。"""
    group = next(
        (n for n in graph["nodes"] if n["type"] == "group" and n["data"].get("label") == name),
        None,
    ) or add_node(graph, make_node("group", {"x": 0, "y": 0}, {"label": name, "asset": kind}))
    image = add_node(graph, make_node("image", {"x": 0, "y": 0}, {}, server_data={"path": path}))
    asset = add_node(graph, make_node("asset", {"x": 0, "y": 0}, {"note": note, "derivative": derivative or ""}))
    if not derivative:
        asset["data"].pop("derivative")
    for node in (image, asset):
        node["parentId"] = group["id"]
    graph["edges"].append(
        {
            "id": f"e-{asset['id']}",
            "source": image["id"],
            "sourceHandle": "image",
            "target": asset["id"],
            "targetHandle": "image",
        }
    )
    return asset


def _prompt_graph(prompt):
    graph = {"nodes": [], "edges": [], "notes": {}, "meta": {}, "revision": 0}
    text = add_node(graph, make_node("text", {"x": 0, "y": 0}, {"text": prompt}))
    generator = add_node(graph, make_node("image_generate", {"x": 400, "y": 0}))
    graph["edges"].append(
        {"id": "e1", "source": text["id"], "sourceHandle": "text", "target": generator["id"], "targetHandle": "prompt"}
    )
    return graph, generator["id"]


def test_mentions_become_numbered_references_and_pull_in_the_asset_image() -> None:
    from lib.canvas.graph import resolve_generate_inputs, resolve_reference_labels

    graph, generator = _prompt_graph("@Hán Thăng/trẻ meets @Hán Thăng, then @Hán Thăng/trẻ again, mail me@x.com")
    _asset(graph, "Hán Thăng", "c/h.png", "his face")
    _asset(graph, "Hán Thăng", "c/t.png", "young", derivative="trẻ")

    prompt, refs = resolve_generate_inputs(graph, generator)

    assert prompt == "图1 meets 图2, then 图1 again, mail me@x.com"
    assert refs == ["c/t.png", "c/h.png"]
    assert resolve_reference_labels(graph, generator) == ["young", "his face"]


def test_a_mention_of_an_already_connected_asset_reuses_its_number() -> None:
    from lib.canvas.graph import resolve_generate_inputs

    graph, generator = _prompt_graph("@阿离 smiles")
    asset = _asset(graph, "阿离", "c/a.png", "her")
    graph["edges"].append(
        {"id": "e2", "source": asset["id"], "sourceHandle": "asset", "target": generator, "targetHandle": "image1"}
    )

    prompt, refs = resolve_generate_inputs(graph, generator)

    assert (prompt, refs) == ("图1 smiles", ["c/a.png"])


def test_longest_name_wins_and_prefix_of_a_longer_word_is_not_a_mention() -> None:
    from lib.canvas.graph import resolve_generate_inputs

    graph, generator = _prompt_graph("@Xe Land Rover and @Xe, not @Xenon")
    _asset(graph, "Xe", "c/1.png")
    _asset(graph, "Xe Land Rover", "c/2.png")

    prompt, refs = resolve_generate_inputs(graph, generator)

    assert prompt == "图1 and 图2, not @Xenon"
    assert refs == ["c/2.png", "c/1.png"]


def test_mention_of_an_asset_without_an_image_fails_when_strict_and_stays_literal_otherwise() -> None:
    from lib.canvas.graph import CanvasGraphError, resolve_generate_inputs

    graph, generator = _prompt_graph("@阿宁 waves")
    _asset(graph, "阿宁", None)

    assert resolve_generate_inputs(graph, generator) == ("@阿宁 waves", [])
    with pytest.raises(CanvasGraphError) as caught:
        resolve_generate_inputs(graph, generator, strict=True)
    assert caught.value.code == "canvas_mention_no_image"


def test_asset_node_takes_its_image_from_whatever_feeds_it_and_the_latest_run_wins() -> None:
    from lib.canvas.graph import resolve_asset_node

    graph, generator = _prompt_graph("p")
    group = add_node(graph, make_node("group", {"x": 0, "y": 0}, {"label": "阿离", "asset": "character"}))
    asset = add_node(graph, make_node("asset", {"x": 0, "y": 0}))
    asset["parentId"] = group["id"]
    graph["edges"].append(
        {"id": "eg", "source": generator, "sourceHandle": "image", "target": asset["id"], "targetHandle": "image"}
    )
    assert "path" not in resolve_asset_node(graph, asset)["data"]

    done = add_node(
        graph, make_node("image", {"x": 0, "y": 0}, {}, server_data={"path": "canvas/images/x.png", "version": 3})
    )
    done["data"].update(generated=True, status="done")
    graph["edges"].append(
        {"id": "eo", "source": generator, "sourceHandle": "image", "target": done["id"], "targetHandle": "in"}
    )

    resolved = resolve_asset_node(graph, asset)["data"]
    assert (resolved["path"], resolved["version"], resolved["name"], resolved["asset"]) == (
        "canvas/images/x.png",
        3,
        "阿离",
        "character",
    )


def test_a_finished_run_inside_an_asset_group_replaces_the_picture_the_asset_node_shows() -> None:
    from lib.canvas.graph import wire_result_to_asset

    graph = {"nodes": [], "edges": [], "notes": {}, "meta": {}, "revision": 0}
    group = add_node(graph, make_node("group", {"x": 0, "y": 0}, {"label": "阿离", "asset": "character"}))
    generator = add_node(graph, make_node("image_generate", {"x": 0, "y": 0}))
    old = add_node(graph, make_node("image", {"x": 0, "y": 0}))
    new = add_node(graph, make_node("image", {"x": 0, "y": 0}))
    asset = add_node(graph, make_node("asset", {"x": 0, "y": 0}))
    for node in (generator, asset):
        node["parentId"] = group["id"]
    graph["edges"].append(
        {"id": "e0", "source": old["id"], "sourceHandle": "image", "target": asset["id"], "targetHandle": "image"}
    )

    wire_result_to_asset(graph, generator["id"], new["id"])

    feeds = [e["source"] for e in graph["edges"] if e["target"] == asset["id"]]
    assert feeds == [new["id"]]


def test_asset_node_connects_to_reference_inputs_but_not_to_text_inputs() -> None:
    from lib.canvas.graph import CanvasGraphError, connect

    graph = {"nodes": [], "edges": [], "notes": {}, "meta": {}, "revision": 0}
    generator = add_node(graph, make_node("image_generate", {"x": 0, "y": 0}))
    asset = add_node(graph, make_node("asset", {"x": 0, "y": 0}))

    edge = connect(graph, asset["id"], "asset", generator["id"], "image1")
    assert edge["targetHandle"] == "image1"
    with pytest.raises(CanvasGraphError):
        connect(graph, asset["id"], "asset", generator["id"], "prompt")


def test_an_image_can_be_connected_into_an_asset_node() -> None:
    from lib.canvas.graph import connect

    graph = {"nodes": [], "edges": [], "notes": {}, "meta": {}, "revision": 0}
    image = add_node(graph, make_node("image", {"x": 0, "y": 0}))
    asset = add_node(graph, make_node("asset", {"x": 0, "y": 0}))

    assert connect(graph, image["id"], "image", asset["id"], "image")["targetHandle"] == "image"


def test_bracketed_script_mentions_are_expanded_too() -> None:
    from lib.canvas.graph import resolve_generate_inputs

    graph, generator = _prompt_graph("@[Phòng khách] @[Hán Thăng/trẻ] nhìn @[Không có], @Phòng khách")
    _asset(graph, "Phòng khách", "s/a.png", "the hall", kind="scene")
    _asset(graph, "Hán Thăng", "c/h.png", "his face", derivative="trẻ")

    prompt, refs = resolve_generate_inputs(graph, generator)

    assert prompt == "图1 图2 nhìn @[Không có], 图1"
    assert refs == ["s/a.png", "c/h.png"]


def test_grouping_moves_children_into_the_group_frame_and_ungrouping_restores_them() -> None:
    from lib.canvas.graph import group_nodes, ungroup

    graph = {"nodes": [], "edges": [], "notes": {}, "revision": 0}
    a = add_node(graph, make_node("text", {"x": 500, "y": 300}))
    b = add_node(graph, make_node("image_generate", {"x": 900, "y": 340}))

    group = group_nodes(graph, [a["id"], b["id"]], "Face", {"x": 450, "y": 250}, {"width": 800, "height": 400})

    assert graph["nodes"][0] is group
    assert (a["parentId"], a["position"]) == (group["id"], {"x": 50.0, "y": 50.0})
    assert b["position"] == {"x": 450.0, "y": 90.0}
    assert group["data"] == {"label": "Face", "width": 800.0, "height": 400.0}

    ungroup(graph, group["id"])

    assert [n["id"] for n in graph["nodes"]] == [a["id"], b["id"]]
    assert "parentId" not in a
    assert a["position"] == {"x": 500.0, "y": 300.0}


def test_deleting_a_group_frame_keeps_its_nodes() -> None:
    from lib.canvas.graph import group_nodes, remove_node

    graph = {"nodes": [], "edges": [], "notes": {}, "revision": 0}
    a = add_node(graph, make_node("text", {"x": 10, "y": 10}))
    b = add_node(graph, make_node("text", {"x": 20, "y": 20}))
    group = group_nodes(graph, [a["id"], b["id"]], "", {"x": 0, "y": 0}, {"width": 100, "height": 100})

    remove_node(graph, group["id"])

    assert {n["id"] for n in graph["nodes"]} == {a["id"], b["id"]}


def test_grouping_rejects_groups_nodes_already_grouped_and_single_nodes() -> None:
    from lib.canvas.graph import CanvasGraphError, group_nodes

    graph = {"nodes": [], "edges": [], "notes": {}, "revision": 0}
    a, b, c = (add_node(graph, make_node("text", {"x": i, "y": 0})) for i in range(3))
    group = group_nodes(graph, [a["id"], b["id"]], "g", {"x": 0, "y": 0}, {"width": 50, "height": 50})

    for ids, code in (
        ([a["id"], c["id"]], "canvas_group_invalid"),
        ([group["id"], c["id"]], "canvas_group_invalid"),
        ([c["id"]], "canvas_group_too_small"),
    ):
        with pytest.raises(CanvasGraphError) as caught:
            group_nodes(graph, ids, "x", {"x": 0, "y": 0}, {"width": 50, "height": 50})
        assert caught.value.code == code


def test_nodes_can_be_dropped_into_a_project_group_and_released_again() -> None:
    from lib.canvas.graph import CanvasGraphError, set_members

    graph = {"nodes": [], "edges": [], "notes": {}, "revision": 0}
    text = add_node(graph, make_node("text", {"x": 900, "y": 900}))

    set_members(graph, [text["id"]], "p:character:阿离", {text["id"]: {"x": 40, "y": 80}})
    assert (text["parentId"], text["position"]) == ("p:character:阿离", {"x": 40.0, "y": 80.0})

    set_members(graph, [text["id"]], None, {text["id"]: {"x": 500, "y": 600}})
    assert "parentId" not in text
    assert text["position"] == {"x": 500.0, "y": 600.0}

    for bad_group in ("p:char_text:阿离", "text_unknown"):
        with pytest.raises(CanvasGraphError):
            set_members(graph, [text["id"]], bad_group, {})
    with pytest.raises(CanvasGraphError):
        set_members(graph, ["p:char_sheet:阿离"], "p:character:阿离", {})


def test_a_user_group_moves_to_the_front_when_it_gains_members() -> None:
    from lib.canvas.graph import group_nodes, set_members

    graph = {"nodes": [], "edges": [], "notes": {}, "revision": 0}
    a, b, c = (add_node(graph, make_node("text", {"x": i, "y": 0})) for i in range(3))
    group = group_nodes(graph, [a["id"], b["id"]], "g", {"x": 0, "y": 0}, {"width": 50, "height": 50})
    graph["nodes"].append(graph["nodes"].pop(0))  # 模拟分组被排到了后面

    set_members(graph, [c["id"]], group["id"], {c["id"]: {"x": 5, "y": 5}})

    assert graph["nodes"][0] is group


def test_a_run_output_stays_in_the_providers_group_and_the_group_grows_to_hold_it():
    graph = empty_graph()
    group = make_node("group", {"x": 0, "y": 0}, {"label": "角色", "width": 400.0, "height": 300.0})
    generator = make_node("image_generate", {"x": 40, "y": 60})
    generator["parentId"] = group["id"]
    graph["nodes"].extend([group, generator])

    media = add_run_outputs(graph, generator["id"], "一张脸")

    assert media["parentId"] == group["id"]
    assert group["data"]["width"] >= media["position"]["x"] + 300
    assert group["data"]["height"] >= media["position"]["y"] + 300


def test_loading_pulls_an_earlier_stray_output_into_its_providers_group():
    from lib.canvas.graph import normalize_legacy

    graph = empty_graph()
    group = make_node("group", {"x": 0, "y": 0}, {"label": "角色"})
    generator = make_node("image_generate", {"x": 40, "y": 60})
    generator["parentId"] = group["id"]
    stray = make_node("image", {"x": 400, "y": 60}, {"generated": True})
    graph["nodes"].extend([group, generator, stray])
    graph["edges"].append(
        {"id": "e1", "source": generator["id"], "sourceHandle": "image", "target": stray["id"], "targetHandle": "in"}
    )

    normalize_legacy(graph)

    assert stray["parentId"] == group["id"]
