from pathlib import Path


def test_takeover_never_synthesizes_mouse_input():
    source = (Path(__file__).resolve().parents[1] / 'native/widget.m').read_text()
    for forbidden in ('CGEventPost', 'CGEventCreateMouseEvent', 'CGWarpMouseCursorPosition',
                      'CGDisplayMoveCursorToPoint', 'mouseEventWithType:', 'revealHostControls:'):
        assert forbidden not in source, f'Takeover must never control the desktop pointer: {forbidden}'
