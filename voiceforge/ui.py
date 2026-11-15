from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path

import psutil
from PySide6.QtCore import (QAbstractTableModel, QModelIndex, Qt, QThread, Signal, QTimer,
                            QSortFilterProxyModel, QUrl, QSettings)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QListWidget, QStackedWidget, QFrame, QGridLayout, QProgressBar,
    QTableView, QHeaderView, QLineEdit, QPlainTextEdit, QFileDialog, QMessageBox,
    QFormLayout, QSpinBox, QDoubleSpinBox, QDialog, QDialogButtonBox, QComboBox,
    QSplitter, QAbstractItemView, QCheckBox, QScrollArea)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .project import Project, MODEL, DEFAULT_TOOLS, DEFAULT_SECRET, create_project, read_json, read_refs, jobs_from_csv, write_json
from .engine import Pipeline, verify, regenerate, legacy_snapshot
from .xedit_import import (CHILD_VOICES, is_export_folder, load_plan, prepare as prepare_from_export,
    saved_substitutions, unused_project_path)
from .i18n import LANG, LANGUAGES, save_language, tr
from . import __version__

STATUS = dict(pending=tr('В очереди'), running=tr('В работе'), wav_ready=tr('WAV готов'), done=tr('Готово'), failed=tr('Ошибка'))
ASSETS = Path(__file__).resolve().parent / 'assets'
SITE_URL = 'https://dwemermods.com'

T = {  # Dwemer Atelier tokens (same values as the MO2 theme and Atelier Texture Bridge)
    'view': '#171A20', 'base': '#1D2128', 'surf1': '#232830', 'surf2': '#2C313B', 'border': '#2E343E',
    'border_strong': '#3F4655', 'text': '#E4E1D9', 'bright': '#F4F1EA', 'muted': '#9AA1AE', 'disabled': '#5C6370',
    'accent': '#D6AC62', 'accent_hover': '#E8C27E', 'danger': '#E0645C', 'success': '#6DB37A', 'info': '#8FB3D9',
}

APP_QSS = f"""
QFrame#rail {{ background: {T['view']}; border-right: 1px solid {T['border']}; }}
QFrame#rail QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: 22px; font-weight: 600; color: {T['bright']}; }}
QLabel#site {{ font-size: 12px; font-weight: 600; color: {T['accent']}; letter-spacing: 1px; }}
QLabel#siteLink {{ color: {T['muted']}; font-size: 11px; }}
QLabel#engine {{ color: {T['muted']}; font-family: "Cascadia Mono", Consolas, monospace; font-size: 11px; }}
QLabel#project {{ font-size: 18px; font-weight: 600; color: {T['accent']}; }}
QLabel#title {{ font-size: 24px; font-weight: 600; color: {T['bright']}; }}
QLabel#muted {{ color: {T['muted']}; }}
QLabel#metric {{ font-size: 26px; font-weight: 600; color: {T['bright']}; }}
QFrame#card {{ background: {T['surf1']}; border: 1px solid {T['border']}; border-radius: 8px; }}
QFrame#card QLabel {{ background: transparent; }}
QPushButton {{ padding: 7px 14px; }}
QPushButton#primary {{ background: {T['accent']}; color: {T['view']}; font-weight: 600; border: 1px solid {T['accent']}; }}
QPushButton#primary:hover {{ background: {T['accent_hover']}; border-color: {T['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {T['surf2']}; color: {T['disabled']}; border-color: {T['border']}; }}
QPushButton#danger {{ color: {T['danger']}; }}
QListWidget#nav {{ background: transparent; border: none; }}
QListWidget#nav::item {{ padding: 11px 14px; margin: 2px 4px; border-radius: 6px; color: {T['muted']};
  border-left: 3px solid transparent; }}
QListWidget#nav::item:hover {{ background: {T['surf1']}; color: {T['text']}; }}
QListWidget#nav::item:selected {{ background: {T['surf2']}; color: {T['bright']}; border-left: 3px solid {T['accent']}; }}
QPlainTextEdit#log {{ background: {T['view']}; color: {T['muted']}; border: 1px solid {T['border']};
  font-family: "Cascadia Mono", Consolas, monospace; font-size: 11px; }}
QProgressBar {{ min-height: 8px; max-height: 8px; }}
"""


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, handler, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(handler)
    return widget


def page(title, subtitle):
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(30, 24, 30, 24)
    layout.setSpacing(16)
    layout.addWidget(label(title, 'title'))
    sub = label(subtitle, 'muted')
    sub.setWordWrap(True)
    layout.addWidget(sub)
    return widget, layout


class TaskThread(QThread):
    update = Signal(dict)
    result = Signal(object)
    problem = Signal(str)

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function(self.update.emit))
        except Exception as error:
            self.problem.emit(f'{type(error).__name__}: {error}')


class Trend(QWidget):
    def __init__(self):
        super().__init__()
        self.values = deque([0] * 90, maxlen=90)
        self.setMinimumHeight(150)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(12, 10, -12, -12)
        painter.fillRect(self.rect(), QColor(T['view']))
        painter.setPen(QPen(QColor(T['border']), 1))
        for level in (0, 25, 50, 75, 100):
            y = rect.bottom() - level / 100 * rect.height()
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
        painter.setPen(QPen(QColor(T['accent']), 2))
        vals = list(self.values)
        for i in range(1, len(vals)):
            painter.drawLine(int(rect.left() + (i-1) * rect.width() / 89), int(rect.bottom() - vals[i-1] / 100 * rect.height()),
                             int(rect.left() + i * rect.width() / 89), int(rect.bottom() - vals[i] / 100 * rect.height()))


class JobsModel(QAbstractTableModel):
    headers = ['ID / VoiceType', tr('Текст реплики'), 'FUZ', tr('Состояние'), tr('Этап'), tr('QA / ошибка')]

    def __init__(self):
        super().__init__()
        self.jobs, self.status = [], {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.jobs)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        job = self.jobs[index.row()]
        state = self.status.get(job['id'], {})
        if role == Qt.ItemDataRole.DisplayRole:
            qa = json.loads(state.get('qa') or '{}')
            flags = '; '.join(qa.get('flags', []))
            values = [job['id'], job['text'], len(job['targets']), STATUS.get(state.get('status'), tr('В очереди')),
                      state.get('stage') or '—', state.get('error') or flags or '—']
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return job['text'] + '\n\n' + '\n'.join(job['targets']) + '\n' + (state.get('error') or '')
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 3:
            return QColor({'done': T['success'], 'failed': T['danger'], 'running': T['accent_hover'], 'wav_ready': T['info']}.get(state.get('status'), T['muted']))

    def load(self, jobs, status):
        self.beginResetModel()
        self.jobs, self.status = jobs, status
        self.endResetModel()

    def refresh(self, status):
        self.status = status
        if self.jobs:
            self.dataChanged.emit(self.index(0, 3), self.index(len(self.jobs)-1, 5))


class JobsFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.query, self.state = '', ''

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        job = model.jobs[row]
        state = model.status.get(job['id'], {})
        return (not self.state or state.get('status') == self.state) and (
            not self.query or self.query in job['id'].lower() or self.query in job['text'].lower())


class ImportDialog(QDialog):
    def __init__(self, parent, home):
        super().__init__(parent)
        self.setWindowTitle(tr('Новый проект • импорт'))
        self.resize(760, 550)
        self.result_project = None
        layout = QVBoxLayout(self)
        layout.addWidget(label(tr('Создать проект озвучки'), 'title'))
        info = label(tr('CSV: *_lines.csv + *_paths.csv. JSON: существующий jobs.json.\nПути FUZ берутся из входных данных без вычисления имён по догадке.'), 'muted')
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.name = QLineEdit('New voice project')
        self.kind = QComboBox()
        self.kind.addItems([tr('Пара CSV'), 'jobs.json'])
        self.first, self.second = QLineEdit(), QLineEdit()
        self.refs, self.raw = QLineEdit(), QLineEdit()
        self.bundled = Path(home) / 'voices' / 'ref_candidates.json'
        if self.bundled.is_file():  # the vanilla voice library shipped with VoiceForge
            self.refs.setText(str(self.bundled))
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText(tr('Для относительных targets: Sound/Voice/Plugin.esp'))
        self.folder = QLineEdit(str(home / 'projects' / time.strftime('Project-%Y%m%d-%H%M%S')))
        form.addRow(tr('Название'), self.name)
        form.addRow(tr('Источник'), self.kind)
        for title, edit, directory in [('Lines CSV / jobs.json', self.first, False), ('Paths CSV', self.second, False),
            ('ref_candidates.json', self.refs, False), (tr('Готовые wav_raw (необязательно)'), self.raw, True),
            (tr('Новая папка проекта'), self.folder, True)]:
            row = QWidget()
            box = QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            box.addWidget(edit)
            box.addWidget(button('…', lambda checked=False, e=edit, d=directory: self.browse(e, d)))
            form.addRow(title, row)
        form.addRow(tr('Префикс путей JSON'), self.prefix)
        layout.addLayout(form)
        self.trust = QCheckBox(tr('Использовать готовые WAV из указанной папки для точного исходного jobs.json'))
        self.trust.setChecked(False)
        layout.addWidget(self.trust)
        self.error = label('', 'muted')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(self.accept_import)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)
        self.first.textChanged.connect(self.suggest)

    def browse(self, edit, directory):
        path = QFileDialog.getExistingDirectory(self, tr('Выберите папку')) if directory else QFileDialog.getOpenFileName(self, tr('Выберите файл'), '', 'Sources (*.json *.csv);;All files (*)')[0]
        if path:
            edit.setText(path)

    def suggest(self, text):
        parent = Path(text).parent
        if (parent / 'ref_candidates.json').is_file() and self.refs.text() in ('', str(self.bundled)):
            self.refs.setText(str(parent / 'ref_candidates.json'))
        if (parent / 'wav_raw').is_dir() and not self.raw.text():
            self.raw.setText(str(parent / 'wav_raw'))

    def accept_import(self):
        # Import can contain hundreds of thousands of rows; parse off the GUI thread.
        values = dict(kind=self.kind.currentIndex(), first=self.first.text(), second=self.second.text(),
            root=self.folder.text(), name=self.name.text(), refs=self.refs.text(), prefix=self.prefix.text().strip(),
            raw=self.raw.text() if self.trust.isChecked() else '')
        if values['kind'] == 0 and values['raw']:
            self.error.setText('Reuse of old WAVs requires the matching original jobs.json; CSV IDs differ.')
            return
        def perform(emit):
            if values['kind'] == 0:
                jobs, skipped = jobs_from_csv(values['first'], values['second'])
                prefix = ''
            else:
                jobs, skipped, prefix = read_json(values['first']), [], values['prefix']
                if not prefix and any(not t.replace('\\', '/').lower().startswith('sound/voice/') for j in jobs for t in j['targets']):
                    raise ValueError('Set the JSON target prefix, e.g. Sound/Voice/DiaryOfMine.esm')
            return create_project(values['root'], values['name'], jobs, references=values['refs'],
                raw_source=values['raw'], target_prefix=prefix, skipped=skipped)
        self.setEnabled(False)
        self.task = TaskThread(perform)
        self.task.result.connect(self.imported)
        self.task.problem.connect(self.import_error)
        self.task.start()

    def imported(self, root):
        self.result_project = root
        self.setEnabled(True)
        self.accept()

    def import_error(self, error):
        self.setEnabled(True)
        self.error.setText(error)

    def reject(self):
        if hasattr(self, 'task') and self.task.isRunning():
            return
        super().reject()


class XEditProjectDialog(QDialog):
    """Confirms a project from an xEdit export: name, child voices, voices for speaking creatures."""

    def __init__(self, parent, folder, plan, available, previous):
        super().__init__(parent)
        self.setWindowTitle(tr('Проект из выгрузки xEdit'))
        self.resize(980, 640 if plan.speaking else 360)
        self.plan, self.selectors = plan, {}
        layout = QVBoxLayout(self)
        layout.addWidget(label(tr('Проект из выгрузки xEdit'), 'title'))
        where = label(str(folder), 'muted')
        where.setWordWrap(True)
        layout.addWidget(where)
        form = QFormLayout()
        self.name = QLineEdit(Path(folder).name)
        form.addRow(tr('Название'), self.name)
        layout.addLayout(form)
        dropped = len((set(plan.noise) | set(plan.silent)) - set(plan.speaking))
        summary = label(tr('Голосов без эталонов пропущено: {dropped}. Они попали в выгрузку только из широких условий (например, «любой мужчина») или не говорят ничего, кроме лая и рыка. Полный разбор будет в voice-plan.txt рядом с выгрузкой.', dropped=dropped), 'muted')
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.children = QCheckBox(tr('Не озвучивать детские голоса (FemaleChild, MaleChild)'))
        layout.addWidget(self.children)
        if plan.speaking:
            info = label(tr('Эти голоса без эталона действительно говорят. Выберите голос из библиотеки, с которого клонировать. Файлы FUZ остаются в папке исходного голоса, заимствуется только эталон.'), 'muted')
            info.setWordWrap(True)
            layout.addWidget(info)
            body = QHBoxLayout()
            rows = QWidget()
            grid = QGridLayout(rows)
            grid.setContentsMargins(0, 0, 0, 0)
            values = [tr('— не озвучивать —')] + sorted(available - CHILD_VOICES)
            for row, vt in enumerate(sorted(plan.speaking)):
                grid.addWidget(button(f'{vt}  ({len(plan.speaking[vt])})', lambda checked=False, v=vt: self.show_lines(v)), row, 0)
                box = QComboBox()
                box.addItems(values)
                if previous.get(vt) in values:
                    box.setCurrentText(previous[vt])
                box.currentIndexChanged.connect(lambda _index, v=vt: self.show_lines(v))
                grid.addWidget(box, row, 1)
                self.selectors[vt] = box
            grid.setRowStretch(len(plan.speaking), 1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(rows)
            body.addWidget(scroll, 1)
            self.lines = QPlainTextEdit()
            self.lines.setObjectName('log')
            self.lines.setReadOnly(True)
            body.addWidget(self.lines, 1)
            layout.addLayout(body, 1)
            self.show_lines(sorted(plan.speaking)[0])
        else:
            layout.addStretch(1)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = controls.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(tr('Создать проект'))
        ok.setObjectName('primary')
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def show_lines(self, vt):
        lines = self.plan.speaking[vt]
        self.lines.setPlainText(vt + ': ' + tr('реплик {count}', count=len(lines)) + '\n\n'
                                + '\n'.join('- ' + line.text for line in lines))

    def substitutions(self):
        return {vt: box.currentText() for vt, box in self.selectors.items() if box.currentIndex() > 0}


class Window(QMainWindow):
    def __init__(self, home):
        super().__init__()
        self.home = Path(home)
        self.project, self.pipeline, self.task = None, None, None
        self.baseline_done, self.start_time = 0, 0
        self.previous_counts = {}
        self.settings = QSettings('VoiceForge', 'Studio')
        self.external = self.settings.value('external', [], type=list)
        self.setWindowTitle('VoiceForge • Skyrim Voice Studio')
        self.setWindowIcon(QIcon(str(ASSETS / 'icon.ico')))
        self.resize(1440, 930)
        self.setMinimumSize(1100, 760)
        self.setAcceptDrops(True)
        root = QWidget()
        self.setCentralWidget(root)
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        rail = QFrame()
        rail.setObjectName('rail')
        rail.setFixedWidth(228)
        rail_box = QVBoxLayout(rail)
        rail_box.setContentsMargins(14, 22, 14, 18)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ASSETS / 'dwemermods-logo.png')).scaledToHeight(
            60, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label('VoiceForge', 'brand'))
        names.addWidget(label('DWEMER MODS', 'site'))
        brand.addLayout(names)
        brand.setAlignment(names, Qt.AlignmentFlag.AlignVCenter)
        brand.addStretch()
        rail_box.addLayout(brand)
        rail_box.addSpacing(4)
        rail_box.addWidget(label('Skyrim Voice Studio', 'muted'))
        rail_box.addSpacing(20)
        self.nav = QListWidget()
        self.nav.setObjectName('nav')
        self.nav.addItems([tr('01   Обзор'), tr('02   Очередь реплик'), tr('03   Эталоны голосов'), tr('04   Мониторинг'), tr('05   Настройки')])
        rail_box.addWidget(self.nav, 1)
        rail_box.addWidget(button(tr('+ Новый проект'), self.new_project, True))
        rail_box.addWidget(button(tr('Из выгрузки xEdit…'), self.choose_export))
        rail_box.addWidget(button(tr('Открыть проект'), self.open_dialog))
        rail_box.addSpacing(16)
        rail_box.addWidget(label('ENGINE  Fish Audio\nMODEL   s2.1-pro-free\nBUILD   ' + __version__, 'engine'))
        site = label(f'<a style="color:{T["accent"]}; text-decoration:none" href="{SITE_URL}">dwemermods.com</a>', 'siteLink')
        site.setOpenExternalLinks(True)
        rail_box.addWidget(site)
        body.addWidget(rail)
        self.stack = QStackedWidget()
        body.addWidget(self.stack)
        self.build_overview()
        self.build_queue()
        self.build_references()
        self.build_monitor()
        self.build_settings()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2500)
        self.monitor_tick = 0
        self.refresh_busy = False
        self.statusBar().showMessage(tr('Готов к работе • создайте или откройте проект'))
        self.refresh()

    def build_overview(self):
        widget, layout = page(tr('Производство озвучки'), tr('От реплик и эталонов до проверенного Skyrim FUZ — в одном локальном приложении.'))
        self.project_title = label(tr('Проект не открыт'), 'project')
        layout.addWidget(self.project_title)
        drop = QFrame()
        drop.setObjectName('card')
        drop_box = QHBoxLayout(drop)
        drop_box.setContentsMargins(18, 12, 18, 12)
        drop_text = label(tr('Перетащите сюда папку выгрузки xEdit (VoiceForge-…) — проект создастся сам. Её можно перетащить и на значок VoiceForge.exe.'), 'muted')
        drop_text.setWordWrap(True)
        drop_box.addWidget(drop_text, 1)
        drop_box.addWidget(button(tr('Выбрать папку…'), self.choose_export))
        layout.addWidget(drop)
        grid = QGridLayout()
        self.metrics = {}
        for col, (key, title) in enumerate([('jobs', tr('УНИКАЛЬНЫХ РЕПЛИК')), ('done', tr('ГОТОВО FUZ-ЗАДАНИЙ')), ('failed', tr('ОШИБОК')), ('rate', tr('ЗАДАНИЙ / МИН'))]):
            card = QFrame()
            card.setObjectName('card')
            box = QVBoxLayout(card)
            box.setContentsMargins(18, 18, 18, 18)
            box.addWidget(label(title, 'muted'))
            self.metrics[key] = label('—', 'metric')
            box.addWidget(self.metrics[key])
            grid.addWidget(card, 0, col)
        layout.addLayout(grid)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.progress_text = label(tr('Создайте проект из CSV или импортируйте jobs.json.'), 'muted')
        layout.addWidget(self.progress_text)
        controls = QHBoxLayout()
        self.run_button = button(tr('Запустить конвейер'), lambda: self.start('full'), True)
        self.sample_button = button(tr('5 проб WAV'), lambda: self.start('samples', sample=True))
        self.build_button = button(tr('Только сборка FUZ'), lambda: self.start('build'))
        self.pause_button = button(tr('Пауза'), self.pause)
        self.stop_button = button(tr('Остановить'), self.stop)
        self.stop_button.setObjectName('danger')
        for b in (self.run_button, self.sample_button, self.build_button, self.pause_button, self.stop_button):
            controls.addWidget(b)
        layout.addLayout(controls)
        actions = QHBoxLayout()
        self.verify_button = button(tr('Проверить все FUZ'), self.check_files)
        self.zip_button = button(tr('Экспорт мода .zip'), self.export_zip)
        actions.addWidget(self.verify_button)
        actions.addWidget(self.zip_button)
        actions.addWidget(button(tr('Открыть результат'), self.open_output))
        actions.addStretch()
        layout.addLayout(actions)
        self.resources = label(tr('CPU —   RAM —   Диск —'), 'muted')
        layout.addWidget(self.resources)
        self.trend = Trend()
        layout.addWidget(self.trend)
        layout.addWidget(label(tr('ЖУРНАЛ СОБЫТИЙ'), 'muted'))
        self.log = QPlainTextEdit()
        self.log.setObjectName('log')
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        self.log.setPlaceholderText(tr('Запуски, повторы, ошибки API и результаты проверки появятся здесь.'))
        layout.addWidget(self.log, 1)
        self.stack.addWidget(widget)

    def build_queue(self):
        widget, layout = page(tr('Очередь реплик'), tr('Поиск по тексту и голосу, прослушивание WAV и точечная перегенерация с резервной копией.'))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('Поиск текста или VoiceType…'))
        self.state_filter = QComboBox()
        self.state_filter.addItem(tr('Все состояния'), '')
        for key, value in STATUS.items():
            self.state_filter.addItem(value, key)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.state_filter)
        layout.addLayout(filters)
        self.model = JobsModel()
        self.proxy = JobsFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 225)
        self.table.setColumnWidth(2, 55)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 190)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        controls.addWidget(button(tr('Прослушать WAV'), self.play))
        controls.addWidget(button(tr('Стоп аудио'), lambda: self.player.stop()))
        self.selection_run = button(tr('Собрать выделенные'), lambda: self.start('full', selected=self.selected()))
        self.regen_button = button(tr('Перегенерировать выделенные'), self.regen)
        controls.addWidget(self.selection_run)
        self.selection_sample = button(tr('Проба выделенных WAV'), lambda: self.start('samples', selected=self.selected()))
        controls.addWidget(self.selection_sample)
        controls.addWidget(self.regen_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.search.textChanged.connect(self.filter_jobs)
        self.state_filter.currentIndexChanged.connect(self.filter_jobs)
        self.stack.addWidget(widget)

    def build_references(self):
        widget, layout = page(tr('Библиотека голосов'), tr('Несколько оригинальных аудио и точные транскрипты на VoiceType. Эталоны передаются в каждом запросе Fish.'))
        self.reference_path = QLineEdit()
        self.reference_path.setPlaceholderText(tr('Путь к ref_candidates.json; рядом должна лежать папка refs/'))
        row = QHBoxLayout()
        row.addWidget(self.reference_path, 1)
        self.ref_button = button(tr('Выбрать библиотеку'), self.choose_refs)
        row.addWidget(self.ref_button)
        layout.addLayout(row)
        mapping = QFormLayout()
        self.voice_type = QComboBox()
        self.voice_type.currentTextChanged.connect(self.voice_mapping_selected)
        self.voice_id = QLineEdit()
        self.voice_id.setPlaceholderText(tr('Пусто = оригинальные эталоны Skyrim; иначе Fish reference_id'))
        mapping.addRow(tr('VoiceType / персонаж'), self.voice_type)
        mapping.addRow('Fish library reference_id', self.voice_id)
        self.voice_save = button(tr('Сохранить выбор голоса'), self.save_voice_mapping)
        mapping.addRow('', self.voice_save)
        layout.addLayout(mapping)
        self.reference_view = QPlainTextEdit()
        self.reference_view.setReadOnly(True)
        layout.addWidget(self.reference_view, 1)
        self.ref_status = label(tr('Откройте проект, чтобы проверить покрытие голосов.'), 'muted')
        self.ref_status.setWordWrap(True)
        layout.addWidget(self.ref_status)
        self.stack.addWidget(widget)

    def build_monitor(self):
        widget, layout = page(tr('Живой мониторинг'), tr('Внешние SQLite-базы открываются только для чтения. Текущие сборщики продолжают работать независимо.'))
        controls = QHBoxLayout()
        controls.addWidget(button(tr('+ Подключить SQLite'), self.add_monitor))
        controls.addWidget(button(tr('Подключить папку сборок'), self.discover_monitors))
        controls.addWidget(button(tr('Очистить список'), self.clear_monitors))
        controls.addStretch()
        layout.addLayout(controls)
        self.monitor_text = QPlainTextEdit()
        self.monitor_text.setReadOnly(True)
        layout.addWidget(self.monitor_text, 1)
        self.stack.addWidget(widget)

    def build_settings(self):
        widget, layout = page(tr('Настройки проекта'), tr('Ограничения ресурсов, инструменты сборки и путь к ключу. Сам ключ не отображается и не копируется в проект.'))
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.tools_edit = QLineEdit(DEFAULT_TOOLS)
        self.secret_edit = QLineEdit(DEFAULT_SECRET)
        form.addRow(tr('Название'), self.name_edit)
        for title, edit, folder in [(tr('Папка инструментов'), self.tools_edit, True), (tr('Файл с FISH_API_KEY'), self.secret_edit, False)]:
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(button('…', lambda checked=False, e=edit, d=folder: self.pick_setting(e, d)))
            form.addRow(title, row)
        self.fish_workers = QSpinBox()
        self.fish_workers.setRange(1, 3)
        self.lip_workers, self.xwm_workers = QSpinBox(), QSpinBox()
        for spin in (self.lip_workers, self.xwm_workers):
            spin.setRange(1, 32)
        self.cpu_limit = QSpinBox()
        self.cpu_limit.setRange(10, 100)
        self.cpu_limit.setSuffix(' %')
        self.temp_limit = QSpinBox()
        self.temp_limit.setRange(0, 100)
        self.temp_limit.setSpecialValueText(tr('Не задан'))
        self.temp_limit.setSuffix(' °C')
        self.retries = QSpinBox()
        self.retries.setRange(0, 3)
        self.padding = QDoubleSpinBox()
        self.padding.setRange(0, 2)
        self.padding.setSingleStep(0.1)
        self.padding.setSuffix(' s')
        for title, spin in [(tr('Fish запросов'), self.fish_workers), ('LipGenerator', self.lip_workers), ('xWMAEncode', self.xwm_workers),
            (tr('Целевая загрузка CPU'), self.cpu_limit), (tr('Предел температуры (если датчик доступен)'), self.temp_limit),
            (tr('Повторов неудачных заданий'), self.retries), (tr('Минимальная длительность клипа для LIP'), self.padding)]:
            form.addRow(title, spin)
        self.language = QComboBox()
        for code, title in LANGUAGES.items():
            self.language.addItem(title, code)
        self.language.setCurrentIndex(list(LANGUAGES).index(LANG))
        self.language.currentIndexChanged.connect(self.change_language)
        form.addRow(tr('Язык интерфейса'), self.language)
        layout.addLayout(form)
        self.save_button = button(tr('Сохранить настройки'), self.save_settings, True)
        layout.addWidget(self.save_button)
        text = label(tr('HTTP 401/402 останавливает очередь. Автоматического перехода на платную модель нет.\n'
            'Название free в API не гарантирует неизменность тарифа: доступ и цену определяет Fish Audio.\n'
            'Температура доступна при наличии датчика Libre/OpenHardwareMonitor; CPU-лимит регулируется по обратной связи.\n'
            'LipGenerator работает с USEnglish. Форматная QA не заменяет проверку произношения и тест в игре.'), 'muted')
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch()
        self.stack.addWidget(widget)

    def change_language(self):
        save_language(self.language.currentData())
        self.add_log(tr('Язык сменится после перезапуска программы.'))
        self.statusBar().showMessage(tr('Язык сменится после перезапуска программы.'))

    def filter_jobs(self):
        self.proxy.query = self.search.text().strip().lower()
        self.proxy.state = self.state_filter.currentData()
        self.proxy.invalidateFilter()

    def selected(self):
        return [self.model.jobs[self.proxy.mapToSource(index).row()]['id'] for index in self.table.selectionModel().selectedRows()]

    def busy(self):
        return bool(self.task and self.task.isRunning())

    def new_project(self):
        if self.busy():
            return
        dialog = ImportDialog(self, self.home)
        if dialog.exec() and dialog.result_project:
            self.load_project(dialog.result_project)

    def dragEnterEvent(self, event):
        if any(url.isLocalFile() and is_export_folder(url.toLocalFile()) for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and is_export_folder(url.toLocalFile()):
                event.acceptProposedAction()
                QTimer.singleShot(0, lambda path=url.toLocalFile(): self.new_from_export(path))
                return

    def choose_export(self):
        if self.busy():
            return
        folder = QFileDialog.getExistingDirectory(self, tr('Папка выгрузки xEdit (lines.csv + paths.csv)'))
        if folder:
            self.new_from_export(folder)

    def export_references(self, folder):
        """ref_candidates.json next to the export, else the library chosen in VoiceForge, else the bundled one."""
        own = Path(folder) / 'ref_candidates.json'
        if own.is_file():
            return own
        last = self.settings.value('references', '')
        if last and Path(last).is_file():
            return Path(last)
        bundled = self.home / 'voices' / 'ref_candidates.json'
        if bundled.is_file():
            return bundled
        QMessageBox.information(self, 'VoiceForge', tr('Выберите ref_candidates.json вашей библиотеки эталонов (рядом с ним должна лежать папка refs). VoiceForge запомнит выбор.'))
        path = QFileDialog.getOpenFileName(self, 'ref_candidates.json', '', 'JSON (*.json)')[0]
        if not path:
            return None
        self.settings.setValue('references', path)
        return Path(path)

    def new_from_export(self, folder):
        if self.busy():
            return
        folder = Path(folder)
        if not is_export_folder(folder):
            self.error(tr('В папке нет lines.csv и paths.csv из выгрузки xEdit: ') + str(folder))
            return
        refs = self.export_references(folder)
        if not refs:
            return
        self.add_log(tr('Разбор выгрузки xEdit: ') + str(folder))
        self.statusBar().showMessage(tr('Разбор выгрузки xEdit…'))
        self.task = TaskThread(lambda emit: load_plan(folder, refs))
        self.task.result.connect(lambda result: self.export_planned(folder, refs, *result))
        self.task.problem.connect(self.error)
        self.task.start()

    def export_planned(self, folder, refs, plan, available):
        dialog = XEditProjectDialog(self, folder, plan, available, saved_substitutions(folder))
        if not dialog.exec():
            self.statusBar().showMessage(tr('Создание проекта отменено.'))
            return
        name = dialog.name.text().strip() or folder.name
        substitutions = dialog.substitutions()
        child = 'exclude' if dialog.children.isChecked() else 'keep'
        root = unused_project_path(folder.parent, folder.name)
        self.statusBar().showMessage(tr('Создание проекта…'))
        self.task = TaskThread(lambda emit: prepare_from_export(folder, root, refs, name, substitutions=substitutions,
                                                                child_voices=child, plan=plan))
        self.task.result.connect(self.export_project_created)
        self.task.problem.connect(self.error)
        self.task.start()

    def export_project_created(self, result):
        self.add_log(tr('Проект создан: заданий {jobs:,}, файлов FUZ {targets:,}, голосов {voices}. Разбор голосов: {plan}',
                        jobs=result['jobs'], targets=result['targets'], voices=result['voice_types'], plan=result['voice_plan']))
        if result['unvoiced_speaking_voice_types']:
            self.add_log(tr('Без голоса оставлены говорящие существа: ') + ', '.join(
                f'{vt} ({n})' for vt, n in result['unvoiced_speaking_voice_types'].items()))
        QTimer.singleShot(0, lambda: self.load_project(result['project']))

    def open_dialog(self):
        if self.busy():
            return
        filename = QFileDialog.getOpenFileName(self, tr('Открыть project.json'), str(self.home), 'VoiceForge project (project.json)')[0]
        if filename:
            self.load_project(Path(filename).parent)

    def load_project(self, root):
        if self.busy():
            return
        self.task = TaskThread(lambda emit: Project(root))
        self.task.result.connect(self.project_loaded)
        self.task.problem.connect(self.error)
        self.task.finished.connect(self.refresh)
        self.task.start()

    def project_loaded(self, project):
        self.project = project
        self.project_title.setText(project.config['name'])
        self.model.load(project.jobs, project.statuses())
        self.baseline_done = project.counts().get('done', 0)
        self.start_time = 0
        self.name_edit.setText(project.config['name'])
        self.tools_edit.setText(project.config['tools'])
        self.secret_edit.setText(project.config['secret'])
        for widget, key in [(self.fish_workers, 'fish_workers'), (self.lip_workers, 'lip_workers'),
            (self.xwm_workers, 'xwm_workers'), (self.cpu_limit, 'cpu_limit'), (self.temp_limit, 'temp_limit'),
            (self.retries, 'auto_retries'), (self.padding, 'pad_min_duration')]:
            widget.setValue(project.config.get(key, 0))
        self.reference_path.setText(project.config.get('references', ''))
        self.voice_type.clear()
        self.voice_type.addItems(sorted({j['vt'] for j in project.jobs}))
        self.show_references()
        self.settings.setValue('lastProject', str(project.root))
        self.add_log(tr('Открыт проект: ') + str(project.root))
        self.statusBar().showMessage(tr('Проект открыт: ') + project.config['name'])

    def show_references(self):
        if not self.project:
            return
        try:
            path = self.project.config.get('references')
            refs = read_refs(path)
            needed = {j['vt'] for j in self.project.jobs}
            voice_ids = self.project.config.get('voice_ids', {})
            missing = sorted(needed - set(refs) - set(voice_ids))
            rows = []
            for vt in sorted(needed):
                if voice_ids.get(vt):
                    rows.append(f'{vt}    Fish library voice: {voice_ids[vt]}\n')
                    continue
                clips = refs.get(vt, [])
                rows.append(f'{vt}    {len(clips)} reference clips')
                for clip in clips:
                    rows.append('    ' + clip['file'] + '\n    ' + clip.get('text', tr('(нет транскрипта)')))
                rows.append('')
            self.reference_view.setPlainText('\n'.join(rows))
            self.ref_status.setText(tr('Нужно голосов: {needed}. Без эталонов: {missing}.', needed=len(needed), missing=len(missing))
                                    + ('\n' + ', '.join(missing) if missing else ''))
        except Exception as error:
            self.ref_status.setText(str(error))

    def choose_refs(self):
        if not self.project or self.busy():
            return
        path = QFileDialog.getOpenFileName(self, 'ref_candidates.json', '', 'JSON (*.json)')[0]
        if path:
            self.reference_path.setText(path)
            self.project.config['references'] = path
            self.settings.setValue('references', path)
            self.project.save()
            self.show_references()

    def voice_mapping_selected(self, vt):
        if self.project:
            self.voice_id.setText(self.project.config.get('voice_ids', {}).get(vt, ''))

    def save_voice_mapping(self):
        if not self.project or self.busy():
            return
        vt, value = self.voice_type.currentText(), self.voice_id.text().strip()
        import re
        if value and not re.fullmatch(r'[a-fA-F0-9]{32}', value):
            self.error(tr('Fish reference_id должен содержать 32 шестнадцатеричных символа.'))
            return
        if any(self.project.wav(j).is_file() for j in self.project.jobs if j['vt'] == vt):
            self.error(tr('Для смены голоса у существующих файлов создайте новый проект. Это сохраняет соответствие аудио и выбранного голоса.'))
            return
        mapping = self.project.config.setdefault('voice_ids', {})
        if value:
            mapping[vt] = value
        else:
            mapping.pop(vt, None)
        self.project.save()
        self.show_references()

    def pick_setting(self, edit, folder):
        value = QFileDialog.getExistingDirectory(self, tr('Папка инструментов')) if folder else QFileDialog.getOpenFileName(self, tr('Файл .env.local'), '', 'All files (*)')[0]
        if value:
            edit.setText(value)

    def save_settings(self):
        if not self.project or self.busy():
            return
        self.project.config.update(name=self.name_edit.text(), tools=self.tools_edit.text(), secret=self.secret_edit.text(),
            fish_workers=self.fish_workers.value(), lip_workers=self.lip_workers.value(), xwm_workers=self.xwm_workers.value(),
            cpu_limit=self.cpu_limit.value(), temp_limit=self.temp_limit.value(), auto_retries=self.retries.value(),
            pad_min_duration=self.padding.value())
        self.project.save()
        self.project_title.setText(self.project.config['name'])
        self.add_log(tr('Настройки сохранены.'))

    def start(self, mode, sample=False, selected=None):
        if not self.project or self.busy():
            return
        if selected == []:
            self.error(tr('Выделите хотя бы одну реплику.'))
            return
        if sample:
            # Five distinct voice types when available, rather than the first five of one voice.
            seen, selected = set(), []
            for job in self.project.jobs:
                if job['vt'] not in seen:
                    seen.add(job['vt'])
                    selected.append(job['id'])
                    if len(selected) == 5:
                        break
        self.baseline_done = self.project.counts().get('done', 0)
        self.start_time = time.monotonic()
        self.pipeline = Pipeline(self.project)
        self.task = TaskThread(lambda emit: self.run_pipeline(emit, mode, selected))
        self.task.update.connect(self.on_event)
        self.task.problem.connect(self.error)
        self.task.finished.connect(self.run_finished)
        self.task.start()
        self.refresh()

    def run_pipeline(self, emit, mode, selected):
        self.pipeline.emit = emit
        return self.pipeline.run(mode, selected)

    def pause(self):
        if self.pipeline and self.busy():
            if self.pipeline.paused.is_set():
                self.pipeline.paused.clear()
                self.pause_button.setText(tr('Пауза'))
                self.add_log(tr('Очередь продолжена.'))
            else:
                self.pipeline.paused.set()
                self.pause_button.setText(tr('Продолжить'))
                self.add_log(tr('Пауза: уже запущенные запросы и внешние процессы завершаются штатно.'))

    def stop(self):
        if self.pipeline and self.busy():
            self.pipeline.stop.set()
            self.pipeline.paused.clear()
            self.add_log(tr('Остановка после текущих операций; сетевой таймаут до 180 s, LIP до 60 s.'))

    def run_finished(self):
        self.pipeline = None
        self.pause_button.setText(tr('Пауза'))
        self.refresh()
        self.add_log(tr('Фоновая операция завершена.'))
        QApplication.alert(self, 5000)

    def on_event(self, event):
        if event['kind'] == 'log':
            self.add_log(event['message'])

    def add_log(self, message):
        self.log.appendPlainText(time.strftime('%H:%M:%S') + '  ' + message)

    def error(self, message):
        self.add_log(message)
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, 'VoiceForge', message)

    def play(self):
        if not self.project:
            return
        selected = self.selected()
        if not selected:
            return
        path = self.project.wav(self.project.by_id[selected[0]])
        if not path.exists():
            self.error(tr('WAV ещё не создан.'))
            return
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def regen(self):
        if not self.project or self.busy():
            return
        selected = self.selected()
        if not selected:
            return
        try:
            regenerate(self.project, selected)
            self.add_log(tr('Предыдущие файлы {count} заданий сохранены в backups/.', count=len(selected)))
            self.start('full', selected=selected)
        except Exception as error:
            self.error(str(error))

    def check_files(self):
        if not self.project or self.busy():
            return
        self.task = TaskThread(lambda emit: verify(self.project, emit))
        self.task.update.connect(self.on_event)
        self.task.result.connect(lambda report: self.add_log('QA: ' + json.dumps(report['counts'])))
        self.task.problem.connect(self.error)
        self.task.finished.connect(self.run_finished)
        self.task.start()

    def open_output(self):
        if self.project:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.root / 'output')))

    def export_zip(self):
        if not self.project or self.busy():
            return
        filename = QFileDialog.getSaveFileName(self, tr('Экспорт отдельного MO2-мода'), str(self.project.root / 'VoiceForge-mod.zip'), 'ZIP (*.zip)')[0]
        if not filename:
            return
        def export(emit):
            report = verify(self.project, emit)
            if not report['clean']:
                raise ValueError('Export requires a clean complete file verification. See reports/verification.json.')
            path = Path(filename)
            if path.resolve().is_relative_to((self.project.root / 'output').resolve()):
                raise ValueError('Choose a ZIP path outside output/')
            if path.exists():
                raise ValueError('Choose a new ZIP filename; an existing archive will not be replaced.')
            import zipfile
            with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_STORED) as archive:
                for file in (self.project.root / 'output').rglob('*.fuz'):
                    archive.write(file, file.relative_to(self.project.root / 'output'))
                archive.writestr('docs/VoiceForge-report.json', json.dumps(report, indent=2))
            return str(path)
        self.task = TaskThread(export)
        self.task.update.connect(self.on_event)
        self.task.result.connect(lambda path: self.add_log(tr('Экспорт готов: ') + path))
        self.task.problem.connect(self.error)
        self.task.finished.connect(self.run_finished)
        self.task.start()

    def add_monitor(self):
        path = QFileDialog.getOpenFileName(self, tr('SQLite сборщика'), '', 'SQLite (*.sqlite *.db)')[0]
        if path and path not in self.external:
            self.external.append(path)
            self.settings.setValue('external', self.external)
            self.monitor_tick = 0

    def discover_monitors(self):
        selected = QFileDialog.getExistingDirectory(self, tr('Папка с SQLite-базами сборщика'))
        if not selected:
            return
        root = Path(selected)
        for database in sorted(root.glob('*.sqlite')):
            path = str(database)
            if path not in self.external:
                self.external.append(path)
        self.settings.setValue('external', self.external)
        self.monitor_tick = 0
        self.add_log(tr('Подключено внешних баз: {count}. Режим только чтения.', count=len(self.external)))

    def clear_monitors(self):
        self.external = []
        self.settings.setValue('external', [])
        self.monitor_text.clear()

    def monitor_work(self, emit):
        rows = []
        for path in self.external:
            try:
                result = legacy_snapshot(path)
                rows.append(Path(path).name + '\n' + path)
                for mod, status, count in result['counts']:
                    rows.append(f'    {mod:20} {status:12} {count:,}')
                stamp = result['updated']
                age = int(time.time() - stamp) if stamp else None
                rows.append(tr('    Последняя запись: {age} s назад (давность сама по себе не доказывает зависание).', age=age))
                for ident, error in result['errors']:
                    rows.append(f'    ERROR {ident}: {error}')
                rows.append('')
            except Exception as error:
                rows.append(f'{path}: {error}')
        rows.append(tr('АКТИВНЫЕ ПРОЦЕССЫ КОНВЕЙЕРА'))
        for process in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (process.info['name'] or '').lower()
                args = process.info['cmdline'] or []
                if name in ('lipgenerator.exe', 'xwmaencode.exe') or (name.startswith('python') and any('fuzgen.py' in arg or 'fish_clone.py' in arg for arg in args)):
                    rows.append(f'    PID {process.pid}  {process.info["name"]}')
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return '\n'.join(rows)

    def refresh(self):
        cpu = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        disk = shutil.disk_usage(self.project.root if self.project else self.home)
        self.resources.setText(tr('CPU  {cpu:.0f}%       RAM  {used:.1f} / {total:.1f} GB       Свободно на диске  {free:.1f} GB       Температурный предел — в настройках',
                                  cpu=cpu, used=memory.used / 2**30, total=memory.total / 2**30, free=disk.free / 2**30))
        self.trend.values.append(cpu)
        self.trend.update()
        busy, opened = self.busy(), self.project is not None
        for control in (self.run_button, self.sample_button, self.build_button, self.verify_button, self.zip_button,
                        self.selection_run, self.selection_sample, self.regen_button, self.save_button, self.ref_button, self.voice_save):
            control.setEnabled(opened and not busy)
        self.pause_button.setEnabled(busy and self.pipeline is not None)
        self.stop_button.setEnabled(busy and self.pipeline is not None)
        if opened and not self.refresh_busy:
            self.refresh_busy = True
            current = self.project
            self.status_task = TaskThread(lambda emit: (current.counts(), current.statuses()))
            self.status_task.result.connect(lambda result, p=current: self.apply_status(result) if self.project is p else None)
            self.status_task.problem.connect(lambda error: self.add_log('Status read: ' + error))
            self.status_task.finished.connect(lambda: setattr(self, 'refresh_busy', False))
            self.status_task.start()
        if self.monitor_tick % 8 == 0 and not (hasattr(self, 'monitor_task') and self.monitor_task.isRunning()):
            self.monitor_task = TaskThread(self.monitor_work)
            self.monitor_task.result.connect(self.monitor_text.setPlainText)
            self.monitor_task.problem.connect(lambda error: self.add_log('Monitor: ' + error))
            self.monitor_task.start()
        self.monitor_tick += 1

    def apply_status(self, result):
        counts, statuses = result
        total, done, failed = len(self.project.jobs), counts.get('done', 0), counts.get('failed', 0)
        self.model.refresh(statuses)
        self.metrics['jobs'].setText(f'{total:,}')
        self.metrics['done'].setText(f'{done:,}')
        self.metrics['failed'].setText(f'{failed:,}')
        rate = max(0, done - self.baseline_done) / max(time.monotonic() - self.start_time, 1) * 60 if self.start_time else 0
        self.metrics['rate'].setText(f'{rate:.1f}' if self.start_time else '—')
        self.progress.setValue(int(done / total * 1000) if total else 0)
        eta = (total-done) / rate if rate else None
        self.progress_text.setText(tr('{done:,} / {total:,} заданий • {paths:,} игровых путей • WAV готовы: {wav:,} • В работе: {running}',
            done=done, total=total, paths=sum(len(j['targets']) for j in self.project.jobs),
            wav=counts.get('wav_ready', 0), running=counts.get('running', 0))
            + (tr(' • Осталось ≈ {eta:.0f} мин', eta=eta) if eta else ''))

    def closeEvent(self, event):
        if self.busy():
            if self.pipeline:
                self.stop()
            self.statusBar().showMessage(tr('Завершение текущих операций. Закройте окно после остановки.'))
            event.ignore()
            return
        # Status and monitor reads are short; wait for them instead of ignoring the close.
        self.timer.stop()
        for attr in ('status_task', 'monitor_task'):
            task = getattr(self, attr, None)
            if task and task.isRunning():
                task.wait(10000)
        event.accept()


def dark_title_bar(widget):
    """Windows 11: dark caption in the window's base colour (no-op elsewhere)."""
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)
        for attr, name in ((35, 'view'), (34, 'border')):
            c = QColor(T[name])
            ref = ctypes.c_int(c.red() | c.green() << 8 | c.blue() << 16)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ref), 4)
    except Exception:
        pass


def apply_theme(app):
    """Dwemer Atelier QSS with absolute glyph paths, the Atelier UI font, then the app's own rules."""
    for name in ('AtelierUI-Regular.ttf', 'AtelierUI-SemiBold.ttf', 'AtelierUI-Bold.ttf'):
        QFontDatabase.addApplicationFont(str(ASSETS / 'font' / name))
    font = QFont()
    font.setFamilies(['Atelier UI', 'Segoe UI Variable Text', 'Segoe UI'])
    font.setPointSize(10)
    app.setFont(font)
    theme = ASSETS / 'theme'
    qss = (theme / 'Dwemer Atelier.qss').read_text(encoding='utf-8')
    qss = qss.replace('./Dwemer Atelier/', theme.as_posix() + '/Dwemer Atelier/')
    app.setStyleSheet(qss + APP_QSS)


def run(home):
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('DwemerMods.VoiceForge')
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setWindowIcon(QIcon(str(ASSETS / 'icon.ico')))
    apply_theme(app)
    window = Window(home)
    window.show()
    dark_title_bar(window)
    folders = [arg for arg in sys.argv[1:] if not arg.startswith('--') and is_export_folder(arg)]
    if '--project' in sys.argv:
        window.load_project(sys.argv[sys.argv.index('--project') + 1])
    elif folders:  # an export folder dropped onto VoiceForge.exe
        QTimer.singleShot(0, lambda: window.new_from_export(folders[0]))
    elif '--smoke-test' not in sys.argv and window.settings.value('lastProject'):
        path = Path(window.settings.value('lastProject'))
        if (path / 'project.json').is_file():
            window.load_project(path)
    if '--smoke-test' in sys.argv:
        result_path = Path(sys.argv[sys.argv.index('--smoke-test') + 1])
        def finish_smoke():
            window.timer.stop()
            for attr in ('task', 'status_task', 'monitor_task'):
                task = getattr(window, attr, None)
                if task:
                    task.wait(30000)
            app.processEvents()
            window.grab().save(str(result_path.with_suffix('.png')))
            write_json(result_path, dict(ok=True, pages=window.stack.count(),
                project=window.project.config['name'] if window.project else None))
            app.quit()
        QTimer.singleShot(3000, finish_smoke)
    return app.exec()
