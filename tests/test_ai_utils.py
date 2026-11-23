# -*- coding: utf-8 -*-
import unittest

from ai_utils import parse_quest_scene, swap_layout, transcript, truncate


class TestAIUtils(unittest.TestCase):
    def test_swap_layout_english_to_russian(self):
        self.assertEqual(swap_layout('ghbdtn'), 'привет')
        self.assertEqual(swap_layout('Ghbdtn'), 'Привет')

    def test_swap_layout_keeps_valid_text(self):
        text = 'Привет, как дела?'
        self.assertEqual(swap_layout(text), text)

    def test_truncate(self):
        self.assertEqual(truncate('abc', 5), 'abc')
        self.assertTrue(truncate('abcdef', 5).startswith('abcde'))
        self.assertTrue(truncate('abcdef', 5).endswith('…'))

    def test_transcript_labels(self):
        hist = [
            {'sender_id': 1, 'text': 'привет'},
            {'sender_id': 2, 'text': 'как дела'},
        ]
        self.assertEqual(transcript(hist, 1), 'Я: привет\nОн(а): как дела')

    def test_parse_quest_json(self):
        raw = '```json\n{"scene": "Ты в пещере.", "options": ["Идти", "Стоять"]}\n```'
        scene, options = parse_quest_scene(raw)
        self.assertEqual(scene, 'Ты в пещере.')
        self.assertEqual(options, ['Идти', 'Стоять'])

    def test_parse_quest_fallback(self):
        scene, options = parse_quest_scene('Просто текст без JSON')
        self.assertEqual(scene, 'Просто текст без JSON')
        self.assertEqual(len(options), 3)

    def test_parse_quest_empty(self):
        scene, options = parse_quest_scene('')
        self.assertTrue(scene)
        self.assertEqual(len(options), 3)


if __name__ == '__main__':
    unittest.main()
