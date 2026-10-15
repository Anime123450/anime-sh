"""Regression coverage for issues reproduced by the UI audit."""

from __future__ import annotations

from scripts.tui_shots import _export_text

from .test_app import _make_app


async def test_help_description_wraps_to_fit_modal():
    app, _ = _make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("question_mark")
        await pilot.pause()

        rendered = _export_text(app).replace("│", " ")
        rendered = " ".join(rendered.split())
        expected = "The large panel at the top always describes the poster under the cursor."
        assert expected in rendered, f"help description was clipped: {rendered!r}"
