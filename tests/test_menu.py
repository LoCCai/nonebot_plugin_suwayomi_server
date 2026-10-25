from plugin_pkg.menu import COMMANDS, VERSION, build_menu_data, format_help, metadata_extra


def test_help_and_metadata_cover_same_commands():
    menu = build_menu_data()
    assert len(menu) == len(COMMANDS) == 14
    assert len({row["qweb"]["key"] for row in menu}) == 14
    for row in menu:
        command = row["pmn_triggers"][0]["value"]
        assert command in format_help()
        assert all(4 <= len(intent) <= 80 for intent in row["pmn_llm_intents"])
    assert metadata_extra()["version"] == VERSION
    assert metadata_extra()["pmn"]["menu_group"]["key"] == "entertainment"


def test_catalog_callers_receive_detached_metadata():
    first = metadata_extra()
    first["menu_data"][0]["func"] = "changed"
    first["pmn"]["menu_group"]["key"] = "changed"
    second = metadata_extra()
    assert second["menu_data"][0]["func"] == "漫画帮助"
    assert second["pmn"]["menu_group"]["key"] == "entertainment"


def test_administrative_and_subscription_effects_are_explicit():
    items = {row["qweb"]["key"]: row for row in build_menu_data()}
    assert "超级管理员" in items["update"]["trigger_condition"]
    assert "所有订阅会话" in items["update"]["detail_des"]
    assert "不是漫画 ID" in items["subscribe"]["detail_des"]
    assert "明确意图" in items["push-on"]["detail_des"]
    assert "保留订阅更新通知" in items["push-off"]["detail_des"]
