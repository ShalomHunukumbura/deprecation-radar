from deprecation_radar.baseline import message_key


def test_baseline_key_drops_urls() -> None:
    pydantic = (
        "The `dict` method is deprecated; use `model_dump` instead. Deprecated in Pydantic V2.0 "
        "to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.14/migration/"
    )
    assert message_key(pydantic) == message_key(pydantic.replace("2.14", "2.15"))
    assert message_key("x (Background on this at: https://sqlalche.me/e/b8d9)") == "x (Background on this at:"
