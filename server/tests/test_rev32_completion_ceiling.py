from server import server


def test_completion_ceiling_always_maps_to_max_tokens():
    body, _, _ = server.prepare_upstream({"messages": [], "max_completion_tokens": 3577})
    assert body["max_tokens"] == 3577
    assert "max_completion_tokens" not in body


def test_provider_budget_can_only_reduce_output_cap():
    body, _, _ = server.prepare_upstream({"messages": [], "max_completion_tokens": 5000, "provider_token_budget_remaining": 1200})
    assert body["max_tokens"] == 1200


def test_internal_repair_uses_remaining_provider_reported_budget():
    usage = server.UsageAccumulator(completion_tokens=20, total_tokens=80, usage_calls=1, provider_total_calls=1)
    assert server._remaining_completion_cap({"max_completion_tokens": 100}, usage) == 80
    assert server._remaining_provider_budget({"provider_token_budget_remaining": 100}, usage) == 20
    assert server._effective_completion_cap({"max_completion_tokens": 100, "provider_token_budget_remaining": 100}, usage) == 20
