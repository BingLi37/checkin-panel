"""What clicking X does, and what "don't ask again" is allowed to mean.

The window and the tray icon are not automated — they need a real desktop, and a fake one
would only prove the fake works. What *is* tested is the rule underneath: `decide_close`
takes no GUI, so every branch of R4 is checkable here.
"""

from dataclasses import dataclass

import desktop_state


@dataclass(frozen=True)
class FakeAnswer:
	hide: bool
	remember: bool


def never_asked():
	raise AssertionError('the dialog must not be shown on this path')


def test_quitting_from_the_tray_closes_without_asking():
	"""退出 sets the flag then closes the window; asking there would pop a
	"keep running in the background" box on the way out."""
	decision = desktop_state.decide_close(quitting=True, hide_without_asking=False, ask=never_asked)

	assert decision == desktop_state.CloseDecision(allow_close=True, hide=False, remember=False)


def test_quitting_wins_even_when_the_box_was_ticked():
	decision = desktop_state.decide_close(quitting=True, hide_without_asking=True, ask=never_asked)

	assert decision.allow_close is True
	assert decision.hide is False


def test_a_remembered_answer_hides_silently():
	decision = desktop_state.decide_close(quitting=False, hide_without_asking=True, ask=never_asked)

	assert decision == desktop_state.CloseDecision(allow_close=False, hide=True, remember=False)


def test_the_first_close_asks_and_hides():
	decision = desktop_state.decide_close(
		quitting=False, hide_without_asking=False, ask=lambda: FakeAnswer(hide=True, remember=False)
	)

	assert decision == desktop_state.CloseDecision(allow_close=False, hide=True, remember=False)


def test_ticking_the_box_is_carried_out_of_the_dialog():
	decision = desktop_state.decide_close(
		quitting=False, hide_without_asking=False, ask=lambda: FakeAnswer(hide=True, remember=True)
	)

	assert decision.remember is True


def test_cancelling_leaves_the_window_exactly_as_it_was():
	"""Not a quit and not a hide: they did not mean to close it."""
	decision = desktop_state.decide_close(
		quitting=False, hide_without_asking=False, ask=lambda: FakeAnswer(hide=False, remember=False)
	)

	assert decision == desktop_state.CloseDecision(allow_close=False, hide=False, remember=False)


def test_a_missing_file_remembers_nothing(tmp_path):
	prefs = desktop_state.load(tmp_path / 'desktop.json')

	assert prefs == {}
	assert desktop_state.hide_without_asking(prefs) is False


def test_a_corrupt_file_asks_again_rather_than_hiding_silently(tmp_path):
	"""Failing open costs one extra dialog. Failing closed would hide the window with no
	explanation, which is far harder for the owner to make sense of."""
	path = tmp_path / 'desktop.json'
	path.write_text('{not json at all', encoding='utf-8')

	assert desktop_state.load(path) == {}


def test_a_json_list_is_not_preferences(tmp_path):
	path = tmp_path / 'desktop.json'
	path.write_text('["hide_without_asking"]', encoding='utf-8')

	assert desktop_state.load(path) == {}


def test_only_a_real_true_counts_as_consent():
	"""A truthy string or a 1 in that file is corruption, not an answer the owner gave."""
	assert desktop_state.hide_without_asking({'hide_without_asking': True}) is True
	assert desktop_state.hide_without_asking({'hide_without_asking': 'yes'}) is False
	assert desktop_state.hide_without_asking({'hide_without_asking': 1}) is False
	assert desktop_state.hide_without_asking({}) is False


def test_the_choice_round_trips_through_the_file(tmp_path):
	path = tmp_path / 'sub' / 'desktop.json'

	updated = desktop_state.remember_hide(path, {})

	assert desktop_state.hide_without_asking(updated) is True
	assert desktop_state.hide_without_asking(desktop_state.load(path)) is True


def test_saving_keeps_whatever_else_was_in_there(tmp_path):
	path = tmp_path / 'desktop.json'
	path.write_text('{"window_width": 1400}', encoding='utf-8')

	updated = desktop_state.remember_hide(path, desktop_state.load(path))

	assert updated['window_width'] == 1400
	assert desktop_state.load(path)['window_width'] == 1400


def test_an_unwritable_path_still_holds_for_this_session(tmp_path):
	"""The preference is a convenience; losing the write must not lose the answer."""
	blocker = tmp_path / 'blocker'
	blocker.write_text('not a directory', encoding='utf-8')

	updated = desktop_state.remember_hide(blocker / 'desktop.json', {})

	assert desktop_state.hide_without_asking(updated) is True
