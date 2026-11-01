from data_concierge.core.context import RoleDirectory


def test_a_listed_user_has_the_configured_role() -> None:
    assert RoleDirectory({7: "hr"}).role_of(7) == "hr"


def test_a_user_outside_the_map_gets_the_default_role() -> None:
    assert RoleDirectory({7: "hr"}).role_of(8) == "user"
