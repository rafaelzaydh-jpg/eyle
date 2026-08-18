from server import server


def test_completion_ceiling_always_maps_to_max_tokens():
    body, _, _ = server.prepare_upstream({"messages": [], "max_completion_tokens": 3577})
    assert body["max_tokens"] == 3577
    assert "max_completion_tokens" not in body


def test_internal_format_repair_uses_remaining_call_output_ceiling_only():
    usage = server.UsageAccumulator(completion_tokens=20, total_tokens=80, usage_calls=1, provider_total_calls=1)
    assert server._remaining_completion_cap({"max_completion_tokens": 100}, usage) == 80
    assert server._effective_completion_cap({"max_completion_tokens": 100}, usage) == 80
