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
    writer = _add(graph, "h3_writer")
    gen = _add(graph, "image_generate")
    unit = derived_id("unit", 1, "E1U1")
    sheet = derived_id("keyframe", 1, "S1")
    connect(graph, unit, "unit", writer["id"], "units")
    connect(graph, unit, "text", gen["id"], "prompt")
    connect(graph, sheet, "image", gen["id"], "image1")
    with pytest.raises(CanvasGraphError):
        connect(graph, unit, "unit", gen["id"], "prompt")

    derived = {
        unit: {"id": unit, "type": "unit", "position": {"x": 0, "y": 0}, "data": {"text": "a duel at dawn"}},
        sheet: {
            "id": sheet,
            "type": "keyframe",
            "position": {"x": 0, "y": 0},
            "data": {"path": "keyframes/episode_1/S1.png"},
        },
    }
    assert resolve_generate_inputs(graph, gen["id"], derived) == ("a duel at dawn", ["keyframes/episode_1/S1.png"])
    # 该集没有展示（派生节点没加载）时，指向它的连线被跳过而不是报错。
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
    sheet = derived_id("keyframe", 1, "S1")
    connect(graph, first["id"], "image", gen["id"], "image1")
    connect(graph, sheet, "image", gen["id"], "image1")
    derived = {
        sheet: {
            "id": sheet,
            "type": "keyframe",
            "position": {"x": 0, "y": 99},
            "data": {"path": "k.png", "sheet_id": "S1"},
        }
    }

    labels = resolve_reference_labels(graph, gen["id"], derived)

    assert labels == ["hero.png", "keyframe S1"]
    assert reference_legend(labels).endswith("图1: hero.png\n图2: keyframe S1")
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
