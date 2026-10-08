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
    connect(graph, image["id"], "image", gen["id"], "refs")
    with pytest.raises(CanvasGraphError):
        connect(graph, text["id"], "text", gen["id"], "refs")
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
        connect(graph, node["id"], "image", gen["id"], "refs")

    prompt, refs = resolve_generate_inputs(graph, gen["id"])

    assert prompt == "first\n\nsecond"
    assert refs == ["canvas/uploads/a.png"]


def test_run_outputs_stack_on_repeat_runs_and_attach_to_generator():
    graph = empty_graph()
    gen = _add(graph, "image_generate", x=100, y=0)

    text1, image1 = add_run_outputs(graph, gen["id"], "p")
    text2, image2 = add_run_outputs(graph, gen["id"], "p")

    assert image1["data"]["status"] == "queued"
    assert image2["position"]["y"] > image1["position"]["y"]
    edges = {(e["sourceHandle"], e["target"]) for e in graph["edges"] if e["source"] == gen["id"]}
    assert edges == {("text", text1["id"]), ("image", image1["id"]), ("text", text2["id"]), ("image", image2["id"])}


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
    connect(graph, sheet, "image", gen["id"], "refs")
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
    _, image = add_run_outputs(graph, gen["id"], "a fox")
    image["data"]["status"] = "done"
    assert stale_generated_images(graph, {}) == set()
    text["data"]["text"] = "a red fox"
    assert stale_generated_images(graph, {}) == {image["id"]}
