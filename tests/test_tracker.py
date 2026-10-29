from desktop import tracker
from desktop.tracker import Rect


def test_ingame_panel_grows_with_the_game_resolution():
    p1080 = tracker.place_ingame(Rect(0, 0, 1920, 1080))
    p1440 = tracker.place_ingame(Rect(0, 0, 2560, 1440))
    assert p1080.scale == 1.0 and p1080.width == tracker.INGAME_BASE
    assert round(p1440.scale, 2) == 1.33 and p1440.width == round(tracker.INGAME_BASE * 1440 / 1080)
    assert p1440.x + p1440.width <= 2560 and p1440.y + p1440.height < 1440 * 0.72  # clear of the minimap


def test_ingame_scale_setting_multiplies_the_size():
    big = tracker.place_ingame(Rect(0, 0, 2560, 1440), size=1.25)
    assert round(big.scale, 3) == round(1440 / 1080 * 1.25, 3)
    assert big.width == round(tracker.INGAME_BASE * big.scale)
