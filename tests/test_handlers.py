from handlers import main_menu_keyboard


def test_main_menu_keyboard_contains_primary_commands() -> None:
    keyboard = main_menu_keyboard()

    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        ["/portfolio", "/rebalance"]
    ]
    assert keyboard.resize_keyboard is True
    assert keyboard.is_persistent is True
