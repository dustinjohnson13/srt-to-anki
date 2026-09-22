"""Card assembly: which side each language lands on, and where the audio goes."""
import run

VOCAB = "• <b>gato</b> -> o gato (cat) <i>(noun)</i>"


class TestFormatCardStudyFront:
    def test_study_language_and_audio_on_the_front(self):
        front, back = run.format_card("O gato.", "The cat.", "deck_0001.mp3", "")
        assert front == "O gato. [sound:deck_0001.mp3]"
        assert back == "The cat."

    def test_vocabulary_is_appended_to_the_translation(self):
        front, back = run.format_card("O gato.", "The cat.", "a.mp3", VOCAB)
        assert back == f"The cat.<br><br><hr><br><b>Base Vocabulary:</b><br>{VOCAB}"

    def test_vocabulary_without_a_translation_omits_the_rule(self):
        """--no-translate leaves the back with vocabulary but no English."""
        front, back = run.format_card("O gato.", "", "a.mp3", VOCAB)
        assert back == f"<b>Base Vocabulary:</b><br>{VOCAB}"

    def test_no_translation_and_no_vocabulary_gives_an_empty_back(self):
        assert run.format_card("O gato.", "", "a.mp3", "")[1] == ""


class TestFormatCardEnglishFront:
    def test_english_on_the_front_study_language_and_audio_on_the_back(self):
        front, back = run.format_card(
            "O gato.", "The cat.", "deck_ab12.mp3", "", front="english"
        )
        assert front == "The cat."
        assert back == "O gato. [sound:deck_ab12.mp3]"

    def test_vocabulary_follows_the_study_language_onto_the_back(self):
        front, back = run.format_card("O gato.", "The cat.", "a.mp3", VOCAB, front="english")
        assert back == f"O gato. [sound:a.mp3]<br><br><hr><br><b>Base Vocabulary:</b><br>{VOCAB}"

    def test_audio_is_never_on_the_english_side(self):
        front, _ = run.format_card("O gato.", "The cat.", "a.mp3", VOCAB, front="english")
        assert "[sound:" not in front

    def test_surrounding_whitespace_is_trimmed(self):
        front, _ = run.format_card("O gato.", "  The cat.  ", "a.mp3", "", front="english")
        assert front == "The cat."


class TestBothDirectionsCarryAudioOnTheStudySide:
    def test_study_front(self):
        front, back = run.format_card("O gato.", "The cat.", "a.mp3", "")
        assert "[sound:a.mp3]" in front and "[sound:" not in back

    def test_english_front(self):
        front, back = run.format_card("O gato.", "The cat.", "a.mp3", "", front="english")
        assert "[sound:a.mp3]" in back and "[sound:" not in front
