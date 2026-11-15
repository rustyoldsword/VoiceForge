import unittest

from voiceforge.project import share_machine_settings


class MachineSettingsTests(unittest.TestCase):
    def test_new_project_gets_the_remembered_tools_and_key(self):
        config = {'tools': '', 'secret': ''}
        remembered = {'tools': r'D:\VoiceTools', 'secret': r'C:\Keys\fish.env'}
        filled = share_machine_settings(config, remembered)
        self.assertEqual(config, {'tools': r'D:\VoiceTools', 'secret': r'C:\Keys\fish.env'})
        self.assertEqual(sorted(filled), ['secret', 'tools'])

    def test_configured_project_teaches_an_empty_memory(self):
        config = {'tools': r'D:\VoiceTools', 'secret': ''}
        remembered = {'tools': '', 'secret': ''}
        self.assertEqual(share_machine_settings(config, remembered), [])
        self.assertEqual(remembered['tools'], r'D:\VoiceTools')
        self.assertEqual(config['secret'], '')

    def test_a_project_keeps_its_own_tools(self):
        config = {'tools': r'E:\OtherTools', 'secret': ''}
        remembered = {'tools': r'D:\VoiceTools', 'secret': ''}
        self.assertEqual(share_machine_settings(config, remembered), [])
        self.assertEqual(config['tools'], r'E:\OtherTools')
        self.assertEqual(remembered['tools'], r'D:\VoiceTools')


if __name__ == '__main__':
    unittest.main()
