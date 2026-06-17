"""B2 refactor contract: every name historically importable from
``noodle.engine`` (public API + host/test-consumed internals) must remain
importable from the package facade after the monolith split."""


def test_engine_facade_exports():
    from noodle import engine

    for name in (
        # public API
        "execute", "run", "GraphError", "EventCallback",
        # host-consumed (runner.py / runtime server)
        "DEFAULT_NODE_TIMEOUTS", "PROCESS_ISOLATED_NODE_TYPES",
        # test-consumed internals
        "_worse_status", "_topo_order", "_needed_nodes", "_execute_nodes",
        "_build_plan",
        "_loop_items", "_loop_regions", "_validate_loop_regions", "LoopRegion",
        "_expand_metanodes",
        "_validate_input_kinds", "_validate_output_kinds",
        "_validate_connection_kinds", "AI_PORT_KINDS",
        "_auto_expand_dataset_inputs", "_auto_promote_outputs",
        "AUTO_PROMOTE_NODE_TYPES", "DATASET_PASSTHROUGH_NODE_TYPES",
        "DATASET_AUTO_EXPAND_CAP",
        "_MAX_AGENT_LOOP_ITERATIONS", "_dispatch_agent_action_request",
        "_approx_encoded_length",
    ):
        assert hasattr(engine, name), f"noodle.engine.{name} missing"


def test_package_init_reexports_engine_api():
    import noodle

    assert noodle.execute is not None
    assert noodle.run is not None
    assert noodle.GraphError is not None
