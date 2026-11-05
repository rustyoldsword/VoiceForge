"""Interface language. Russian is the source text; EN maps it to English.

The language is read once at import from the same QSettings store the window uses, so module-level
labels (status names, table headers) are translated too. Changing it takes effect after a restart.
"""
from PySide6.QtCore import QLocale, QSettings

LANGUAGES = {'ru': 'Русский', 'en': 'English'}


def saved_language():
    value = QSettings('VoiceForge', 'Studio').value('language', '')
    if value in LANGUAGES:
        return value
    return 'ru' if QLocale.system().language() == QLocale.Language.Russian else 'en'


def save_language(value):
    QSettings('VoiceForge', 'Studio').setValue('language', value)


LANG = saved_language()

EN = {
    # statuses and table
    'В очереди': 'Queued',
    'В работе': 'Running',
    'WAV готов': 'WAV ready',
    'Готово': 'Done',
    'Ошибка': 'Error',
    'Текст реплики': 'Line text',
    'Состояние': 'State',
    'Этап': 'Stage',
    'QA / ошибка': 'QA / error',
    # import dialog
    'Новый проект • импорт': 'New project • import',
    'Создать проект озвучки': 'Create a voice project',
    'CSV: *_lines.csv + *_paths.csv. JSON: существующий jobs.json.\nПути FUZ берутся из входных данных без вычисления имён по догадке.':
        'CSV: *_lines.csv + *_paths.csv. JSON: an existing jobs.json.\nFUZ paths are taken from the input as is; no file names are guessed.',
    'Для относительных targets: Sound/Voice/Plugin.esp': 'For relative targets: Sound/Voice/Plugin.esp',
    'Название': 'Name',
    'Источник': 'Source',
    'Пара CSV': 'CSV pair',
    'Готовые wav_raw (необязательно)': 'Existing wav_raw (optional)',
    'Новая папка проекта': 'New project folder',
    'Префикс путей JSON': 'JSON path prefix',
    'Использовать готовые WAV из указанной папки для точного исходного jobs.json':
        'Reuse the WAVs from this folder (only for the exact original jobs.json)',
    'Выберите папку': 'Choose a folder',
    'Выберите файл': 'Choose a file',
    # main window
    'Готов к работе • создайте или откройте проект': 'Ready • create or open a project',
    '01   Обзор': '01   Overview',
    '02   Очередь реплик': '02   Line queue',
    '03   Эталоны голосов': '03   Voice references',
    '04   Мониторинг': '04   Monitoring',
    '05   Настройки': '05   Settings',
    '+ Новый проект': '+ New project',
    'Открыть проект': 'Open project',
    'Производство озвучки': 'Voice production',
    'От реплик и эталонов до проверенного Skyrim FUZ — в одном локальном приложении.':
        'From lines and references to verified Skyrim FUZ files, in one local application.',
    'Проект не открыт': 'No project open',
    'УНИКАЛЬНЫХ РЕПЛИК': 'UNIQUE LINES',
    'ГОТОВО FUZ-ЗАДАНИЙ': 'FUZ JOBS DONE',
    'ОШИБОК': 'ERRORS',
    'ЗАДАНИЙ / МИН': 'JOBS / MIN',
    'Создайте проект из CSV или импортируйте jobs.json.': 'Create a project from CSV or import a jobs.json.',
    'Запустить конвейер': 'Run pipeline',
    '5 проб WAV': '5 sample WAVs',
    'Только сборка FUZ': 'Build FUZ only',
    'Пауза': 'Pause',
    'Продолжить': 'Resume',
    'Остановить': 'Stop',
    'Проверить все FUZ': 'Verify all FUZ',
    'Экспорт мода .zip': 'Export mod .zip',
    'Открыть результат': 'Open output',
    'CPU —   RAM —   Диск —': 'CPU —   RAM —   Disk —',
    'ЖУРНАЛ СОБЫТИЙ': 'EVENT LOG',
    'Запуски, повторы, ошибки API и результаты проверки появятся здесь.':
        'Runs, retries, API errors and verification results appear here.',
    'Очередь реплик': 'Line queue',
    'Поиск по тексту и голосу, прослушивание WAV и точечная перегенерация с резервной копией.':
        'Search by text and voice, play WAVs and regenerate selected lines with a backup.',
    'Поиск текста или VoiceType…': 'Search text or VoiceType…',
    'Все состояния': 'All states',
    'Прослушать WAV': 'Play WAV',
    'Стоп аудио': 'Stop audio',
    'Собрать выделенные': 'Build selected',
    'Перегенерировать выделенные': 'Regenerate selected',
    'Проба выделенных WAV': 'Sample selected WAVs',
    'Библиотека голосов': 'Voice library',
    'Несколько оригинальных аудио и точные транскрипты на VoiceType. Эталоны передаются в каждом запросе Fish.':
        'A few original clips with exact transcripts per VoiceType. References are sent with every Fish request.',
    'Путь к ref_candidates.json; рядом должна лежать папка refs/': 'Path to ref_candidates.json; the refs/ folder must sit next to it',
    'Выбрать библиотеку': 'Choose library',
    'Пусто = оригинальные эталоны Skyrim; иначе Fish reference_id': 'Empty = original Skyrim references; otherwise a Fish reference_id',
    'VoiceType / персонаж': 'VoiceType / character',
    'Сохранить выбор голоса': 'Save voice choice',
    'Откройте проект, чтобы проверить покрытие голосов.': 'Open a project to check voice coverage.',
    'Живой мониторинг': 'Live monitoring',
    'Внешние SQLite-базы открываются только для чтения. Текущие сборщики продолжают работать независимо.':
        'External SQLite databases are opened read-only. Running builders keep working on their own.',
    '+ Подключить SQLite': '+ Add SQLite',
    'Подключить папку сборок': 'Add builds folder',
    'Очистить список': 'Clear list',
    'Настройки проекта': 'Project settings',
    'Ограничения ресурсов, инструменты сборки и путь к ключу. Сам ключ не отображается и не копируется в проект.':
        'Resource limits, build tools and the key file path. The key itself is never shown or copied into the project.',
    'Папка инструментов': 'Tools folder',
    'Файл с FISH_API_KEY': 'File with FISH_API_KEY',
    'Не задан': 'Not set',
    'Fish запросов': 'Fish requests',
    'Целевая загрузка CPU': 'Target CPU load',
    'Предел температуры (если датчик доступен)': 'Temperature limit (if a sensor exists)',
    'Повторов неудачных заданий': 'Retries of failed jobs',
    'Минимальная длительность клипа для LIP': 'Minimum clip length for LIP',
    'Сохранить настройки': 'Save settings',
    'Язык интерфейса': 'Interface language',
    'Язык сменится после перезапуска программы.': 'The language changes after the program restarts.',
    'HTTP 401/402 останавливает очередь. Автоматического перехода на платную модель нет.\n'
    'Название free в API не гарантирует неизменность тарифа: доступ и цену определяет Fish Audio.\n'
    'Температура доступна при наличии датчика Libre/OpenHardwareMonitor; CPU-лимит регулируется по обратной связи.\n'
    'LipGenerator работает с USEnglish. Форматная QA не заменяет проверку произношения и тест в игре.':
        'HTTP 401/402 stops the queue. There is no automatic switch to a paid model.\n'
        'The free name in the API does not guarantee the plan: Fish Audio sets access and price.\n'
        'Temperature works with a Libre/OpenHardwareMonitor sensor; the CPU limit is a feedback loop.\n'
        'LipGenerator runs with USEnglish. Format QA does not replace listening and an in-game test.',
    'Открыть project.json': 'Open project.json',
    'Проект открыт: ': 'Project opened: ',
    'Открыт проект: ': 'Opened project: ',
    '(нет транскрипта)': '(no transcript)',
    'Нужно голосов: {needed}. Без эталонов: {missing}.': 'Voices needed: {needed}. Without references: {missing}.',
    'Fish reference_id должен содержать 32 шестнадцатеричных символа.': 'A Fish reference_id must be 32 hexadecimal characters.',
    'Для смены голоса у существующих файлов создайте новый проект. Это сохраняет соответствие аудио и выбранного голоса.':
        'To change the voice of existing files, create a new project. This keeps audio and the chosen voice in step.',
    'Файл .env.local': '.env.local file',
    'Настройки сохранены.': 'Settings saved.',
    'Выделите хотя бы одну реплику.': 'Select at least one line.',
    'Очередь продолжена.': 'Queue resumed.',
    'Пауза: уже запущенные запросы и внешние процессы завершаются штатно.':
        'Paused: requests and external processes already running finish normally.',
    'Остановка после текущих операций; сетевой таймаут до 180 s, LIP до 60 s.':
        'Stopping after the current operations; network timeout up to 180 s, LIP up to 60 s.',
    'Фоновая операция завершена.': 'Background operation finished.',
    'WAV ещё не создан.': 'The WAV has not been created yet.',
    'Предыдущие файлы {count} заданий сохранены в backups/.': 'Previous files of {count} jobs were saved to backups/.',
    'Экспорт отдельного MO2-мода': 'Export a separate MO2 mod',
    'Экспорт готов: ': 'Export ready: ',
    'SQLite сборщика': 'Builder SQLite',
    'Папка с SQLite-базами сборщика': 'Folder with builder SQLite databases',
    'Подключено внешних баз: {count}. Режим только чтения.': 'External databases connected: {count}. Read-only.',
    '    Последняя запись: {age} s назад (давность сама по себе не доказывает зависание).':
        '    Last write: {age} s ago (age alone does not prove a hang).',
    'АКТИВНЫЕ ПРОЦЕССЫ КОНВЕЙЕРА': 'ACTIVE PIPELINE PROCESSES',
    'CPU  {cpu:.0f}%       RAM  {used:.1f} / {total:.1f} GB       Свободно на диске  {free:.1f} GB       Температурный предел — в настройках':
        'CPU  {cpu:.0f}%       RAM  {used:.1f} / {total:.1f} GB       Free disk  {free:.1f} GB       Temperature limit is in Settings',
    '{done:,} / {total:,} заданий • {paths:,} игровых путей • WAV готовы: {wav:,} • В работе: {running}':
        '{done:,} / {total:,} jobs • {paths:,} game paths • WAVs ready: {wav:,} • Running: {running}',
    ' • Осталось ≈ {eta:.0f} мин': ' • About {eta:.0f} min left',
    'Завершение текущих операций. Закройте окно после остановки.': 'Finishing current operations. Close the window after it stops.',
}


def tr(text, **values):
    result = EN.get(text, text) if LANG == 'en' else text
    return result.format(**values) if values else result
