import re
import sys
import os
import io
import subprocess
import threading
import requests
from PyQt5.QtWidgets import (QApplication, QMainWindow, QPushButton, QVBoxLayout, QHBoxLayout, QWidget, QSlider, QLabel,
                             QFileDialog, QComboBox, QSpinBox, QLineEdit, QMessageBox, QDialog, QPlainTextEdit,
                             QGroupBox, QAbstractItemView)
from PyQt5.QtCore import Qt, QUrl, QTime, QTimer, pyqtSignal, QObject, QEvent
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QPushButton, QListWidget, QListWidgetItem)
from PyQt5.QtGui import QPalette
from datetime import datetime
from autocut import transcribe, cut
from autocut.transcribe import Transcribe
from autocut.type import LANG
from autocut.cut import Cutter
from PyQt5.QtCore import QThread, pyqtSignal

class ClickableSlider(QSlider):
    def __init__(self, orientation):
        super().__init__(orientation)
        self.dragging = False
        self.subtitle_file_exists = False  # 添加一个标志来跟踪srt文件是否存在

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            event.accept()
            self.dragging = True
            self.setValue(self.minimum() + int((self.maximum() - self.minimum()) * event.x() / self.width()))

    def mouseMoveEvent(self, event):
        if self.dragging:
            event.accept()
            self.setValue(self.minimum() + int((self.maximum() - self.minimum()) * event.x() / self.width()))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            self.setValue(self.minimum() + int((self.maximum() - self.minimum()) * event.x() / self.width()))
            self.sliderReleased.emit()  # 确保发出 sliderReleased 信号

class LogThread(QObject, threading.Thread):
    log_signal = pyqtSignal(str)
    transcription_done_signal = pyqtSignal()

    def __init__(self, command):
        super().__init__()
        self.command = command

    def run(self):
        try:
            process = subprocess.Popen(
                self.command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            while process.poll() is None:
                output_line = process.stdout.readline()
                self.log_signal.emit(output_line)
                if "Done transcription" in output_line:
                    self.transcription_done_signal.emit()
        except subprocess.CalledProcessError as e:
            self.log_signal.emit(f"命令执行错误: {e.returncode}\n")
            self.log_signal.emit(f"错误输出: {e.output}\n")

class VideoPlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoCut Video Editor")
        self.setGeometry(100, 100, 1200, 700)

        # 获取主屏幕的几何信息
        screen_geometry = QApplication.desktop().screenGeometry()

        # 计算窗口居中时的位置
        x = int((screen_geometry.width() - self.width()) / 2)
        y = int((screen_geometry.height() - self.height()) / 2)

        self.move(x, y)

        self.player = QMediaPlayer()
        self.timeLabel = QLabel()
        self.create_ui()
        self.selected_video_directory = None
        self.dragging_slider = False
        self.subtitle_generated = False
        self.spacebar_enabled = True
        self.mouse_enabled = True
        self.log_thread = None

        # 添加动画效果相关的属性
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_animation)
        self.animation_message = ""
        self.animation_dots = 0
        self.animation_max_dots = 3  # 最大点数

    def start_animation(self, message):
        """启动动画效果"""
        self.animation_message = message
        self.animation_dots = 0
        self.animation_timer.start(500)  # 例如，每500毫秒更新一次

    def stop_animation(self):
        """停止动画效果"""
        self.animation_timer.stop()
        self.update_log(self.animation_message + "\n")  # 添加一个换行符以结束动画

    def update_animation(self):
        """更新动画效果"""
        self.animation_dots = (self.animation_dots + 1) % (self.animation_max_dots + 1)
        animated_message = self.animation_message + "." * self.animation_dots
        self.update_log(animated_message, replace_last=True)

    def create_ui(self):
        mainLayout = QHBoxLayout()  # 主布局为水平布局

        # 左侧布局
        leftLayout = QVBoxLayout()  # 创建一个垂直布局

        # 创建视频播放窗口
        videoWidget = QVideoWidget()
        videoWidget.setMinimumSize(800, 600)
        videoWidget.setMaximumSize(800, 600)
        self.player.setVideoOutput(videoWidget)
        leftLayout.addWidget(videoWidget)  # 将视频播放窗口添加到左侧布局

        # 创建控制面板
        controlLayout = QHBoxLayout()
        self.slider = ClickableSlider(Qt.Horizontal)
        self.slider.setFixedWidth(600)
        self.slider.sliderMoved.connect(self.set_position)
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.slider.sliderReleased.connect(self.handle_slider_release)
        controlLayout.addWidget(self.slider)

        self.playbutton = QPushButton("播放/暂停")
        self.playbutton.clicked.connect(self.toggle_play_pause)
        controlLayout.addWidget(self.playbutton)

        timeLayout = QHBoxLayout()
        timeLayout.setAlignment(Qt.AlignCenter)
        self.timeLabel.setText("00:00 / 00:00")
        timeLayout.addWidget(self.timeLabel)
        timeLayout.addStretch()

        controlLayout.addLayout(timeLayout)
        leftLayout.addLayout(controlLayout)  # 将控制面板添加到左侧布局

        mainLayout.addLayout(leftLayout)  # 将左侧布局添加到主布局

        # 右侧布局
        rightLayout = QVBoxLayout()

        # 创建日志窗口
        logGroupBox = QGroupBox("日志窗口")
        logLayout = QVBoxLayout()
        # 设置 QGroupBox 的样式
        logGroupBox.setStyleSheet("""
            QGroupBox {
                font: bold 14px;
                border: 1px solid gray;
                border-radius: 5px;
                margin-top: 1ex; /* leave space at the top for the title */
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
                color: blue;
            }

            QPushButton {
                background-color: #A3C1DA;
                color: black;
            }

            QLabel {
                color: green;
                font: bold;
            }
        """)
        self.logTextEdit = QPlainTextEdit()
        self.logTextEdit.setReadOnly(True)
        logLayout.addWidget(self.logTextEdit)
        logGroupBox.setLayout(logLayout)
        rightLayout.addWidget(logGroupBox)  # 将日志窗口添加到左侧布局

        # 文件选择布局
        fileSelectionLayout = QVBoxLayout()
        fileSelectionLabel = QLabel("第一步：选择视频文件（MP4, AVI, MKV, MOV, FLV, WMV等格式）")
        self.directory_display = QLineEdit()
        self.directory_display.setReadOnly(True)
        self.filebutton = QPushButton("选择视频")
        self.filebutton.clicked.connect(self.open_file)
        fileSelectionLayout.addWidget(fileSelectionLabel)
        fileSelectionLayout.addWidget(self.directory_display)
        fileSelectionLayout.addWidget(self.filebutton)
        rightLayout.addLayout(fileSelectionLayout)

        # 语言选择布局
        languageSelectionLayout = QVBoxLayout()
        languageSelectionLabel = QLabel("第二步：选择字幕语言（如果非视频语言，程序将尝试自动翻译）")
        self.languageComboBox = QComboBox()
        self.languageComboBox.addItems(["zh", "en", "Afrikaans", "Arabic", "Armenian", "Azerbaijani", "Belarusian",
                                        "Bosnian", "Bulgarian", "Catalan", "Croatian", "Czech", "Danish", "Dutch",
                                        "Estonian", "Finnish", "French", "Galician", "German", "Greek", "Hebrew",
                                        "Hindi", "Hungarian", "Icelandic", "Indonesian", "Italian", "Japanese",
                                        "Kannada", "Kazakh", "Korean", "Latvian", "Lithuanian", "Macedonian", "Malay",
                                        "Marathi", "Maori", "Nepali", "Norwegian", "Persian", "Polish", "Portuguese",
                                        "Romanian", "Russian", "Serbian", "Slovak", "Slovenian", "Spanish", "Swahili",
                                        "Swedish", "Tagalog", "Tamil", "Thai", "Turkish", "Ukrainian", "Urdu",
                                        "Vietnamese", "Welsh"])
        languageSelectionLayout.addWidget(languageSelectionLabel)
        languageSelectionLayout.addWidget(self.languageComboBox)
        rightLayout.addLayout(languageSelectionLayout)

        # 第三步：生成字幕
        subtitleGenerationLayout = QVBoxLayout()
        subtitleStepLabel = QLabel("第三步：生成字幕")
        subtitleGenerationLayout.addWidget(subtitleStepLabel)

        self.generateSubtitlesButton = QPushButton("生成字幕")
        self.generateSubtitlesButton.clicked.connect(self.generate_subtitles)
        self.generateSubtitlesButton.setStyleSheet(
            "QPushButton { background-color: #003366; color: white; font-size: 16px; padding: 10px; }")
        subtitleGenerationLayout.addWidget(self.generateSubtitlesButton)
        rightLayout.addLayout(subtitleGenerationLayout)

        # 第四步：短视频智能编辑
        aiToolButtonLayout = QVBoxLayout()
        aiToolStepLabel = QLabel("第四步：短视频智能编辑")
        aiToolButtonLayout.addWidget(aiToolStepLabel)

        self.nextStepButton = QPushButton("AI 视频编辑合成工具")
        self.nextStepButton.setStyleSheet(
            "QPushButton { background-color: #006400; color: white; font-size: 16px; padding: 10px; }")
        self.nextStepButton.clicked.connect(self.go_to_subtitle_generation)
        aiToolButtonLayout.addWidget(self.nextStepButton)
        rightLayout.addLayout(aiToolButtonLayout)

        # 底部退出按钮布局
        exitButtonLayout = QHBoxLayout()
        self.exitButton = QPushButton("退出")
        self.exitButton.clicked.connect(self.close_app)
        exitButtonLayout.addStretch()
        exitButtonLayout.addWidget(self.exitButton)
        rightLayout.addLayout(exitButtonLayout)

        mainLayout.addLayout(rightLayout)  # 将右侧布局添加到主布局

        centralWidget = QWidget()
        centralWidget.setLayout(mainLayout)
        self.setCentralWidget(centralWidget)

        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)

    def generate_subtitles(self):
        # if self.selected_video_directory is None:
        #     QMessageBox.warning(self, '警告', '请先选择视频再生成字幕')
        #     return
        #
        # video_path = self.directory_display.text()
        # base_path, _ = os.path.splitext(video_path)
        # srt_file = base_path + ".srt"
        # md_file = base_path + ".md"
        #
        # if os.path.exists(srt_file) or os.path.exists(md_file):
        #     reply = QMessageBox.question(self, '字幕文件已存在', '字幕文件已存在。是否重新生成？',
        #                                  QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        #     if reply == QMessageBox.No:
        #         return
        #     else:
        #         if os.path.exists(srt_file):
        #             os.remove(srt_file)
        #         if os.path.exists(md_file):
        #             os.remove(md_file)
        #         self.subtitle_generated = False
        #
        # language = self.languageComboBox.currentText()
        # command = f"python -m autocut -t {video_path} --lang {language}"
        #
        # self.update_log("正在生成字幕，生成字幕需要一些时间，请稍候...\n")
        #
        # if self.log_thread is None or not self.log_thread.is_alive():
        #     self.log_thread = LogThread(command)
        #     self.log_thread.log_signal.connect(self.format_and_update_log)
        #     self.log_thread.transcription_done_signal.connect(self.handle_transcription_done)
        #     self.log_thread.start()

        if self.selected_video_directory is None:
            QMessageBox.warning(self, '警告', '请先选择视频再生成字幕')
            return

        video_path = self.directory_display.text()
        base_path, _ = os.path.splitext(video_path)
        srt_file = base_path + ".srt"
        md_file = base_path + ".md"

        if os.path.exists(srt_file) or os.path.exists(md_file):
            reply = QMessageBox.question(self, '字幕文件已存在', '字幕文件已存在。是否重新生成？',
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return
            else:
                if os.path.exists(srt_file):
                    os.remove(srt_file)
                if os.path.exists(md_file):
                    os.remove(md_file)

        self.generateSubtitlesButton.setEnabled(False)  # 禁用按钮
        language = self.languageComboBox.currentText()

        # 创建 Transcribe 参数对象
        transcribe_args = TranscribeArgs(inputs=[video_path], lang=language, whisper_model='base', prompt='')

        # 创建 Transcribe 实例
        transcriber = Transcribe(transcribe_args)

        # 创建并启动字幕生成线程
        self.subtitle_thread = SubtitleGenerationThread(transcriber)
        self.subtitle_thread.finished.connect(self.on_subtitle_generation_finished)
        self.subtitle_thread.log_signal.connect(self.update_log)
        self.subtitle_thread.start()

    def on_subtitle_generation_finished(self):
        self.generateSubtitlesButton.setEnabled(True)  # 启用按钮
        # self.update_log("字幕生成完毕。\n")

    def update_log(self, message, replace_last=False):
        # 将消息添加到日志框
        if replace_last:
            self.logTextEdit.moveCursor(QTextCursor.End)
            self.logTextEdit.moveCursor(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
            self.logTextEdit.textCursor().removeSelectedText()
        self.logTextEdit.appendPlainText(message)

    def format_and_update_log(self, log_line):
        # 格式化日志信息
        formatted_log_line = self.format_log_line(log_line)
        self.logTextEdit.insertPlainText(formatted_log_line)
        cursor = self.logTextEdit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.logTextEdit.setTextCursor(cursor)

    @staticmethod
    def format_log_line(log_line):
        # 在此处添加代码以格式化日志行
        # 示例：将 "[autocut:transcribe.py:L38] INFO" 替换为更清晰的格式
        # if "INFO" in log_line:
        #     log_line = log_line.replace("INFO", "AutoEditer -")
        return log_line

    def update_log(self, log_line):
        self.logTextEdit.insertPlainText(log_line)
        cursor = self.logTextEdit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.logTextEdit.setTextCursor(cursor)

    def toggle_play_pause(self):
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def set_position(self, position):
        self.player.setPosition(position)

    def position_changed(self, position):
        if not self.dragging_slider:
            self.slider.setValue(position)
        self.update_time_labels()

    def duration_changed(self, duration):
        self.slider.setRange(0, duration)
        self.update_time_labels()

    def open_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video Files (*.mp4 *.avi *.mkv)")
        if filename:
            if self.directory_display.text() != filename:
                self.subtitle_generated = False
                self.selected_video_directory = os.path.dirname(filename)
                self.directory_display.setText(filename)
                self.player.setMedia(QMediaContent(QUrl.fromLocalFile(filename)))
                self.player.play()

    def close_app(self):
        reply = QMessageBox.question(self, '退出应用', '确认退出应用吗？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            QApplication.quit()

    def go_to_subtitle_generation(self):
        # 检查是否存在同名的srt文件
        video_path = self.directory_display.text()
        video_directory = os.path.dirname(video_path)  # 获取视频文件所在的目录
        video_filename = os.path.basename(video_path)  # 获取视频文件名
        srt_file = os.path.join(video_directory, os.path.splitext(video_filename)[0] + ".srt")

        if os.path.exists(srt_file):
            self.subtitle_file_exists = True
            self.player.pause()  # 暂停主窗口的视频播放
            self.hide()
            subtitle_generator = SubtitleGenerator(self.selected_video_directory, video_filename, self)
            subtitle_generator.exec_()
        else:
            QMessageBox.warning(self, '警告', '请先生成字幕文件')

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and self.spacebar_enabled:
            self.toggle_play_pause()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.mouse_enabled:
            self.toggle_play_pause()
        else:
            super().mousePressEvent(event)

    def slider_pressed(self):
        self.dragging_slider = True

    def handle_slider_release(self):
        if self.player.duration() > 0:
            release_position = self.slider.value()
            self.set_position(release_position)
            self.dragging_slider = False

    def update_time_labels(self):
        current_time = QTime(0, 0).addMSecs(self.player.position())
        total_time = QTime(0, 0).addMSecs(self.player.duration())
        self.timeLabel.setText(f"{current_time.toString('mm:ss')} / {total_time.toString('mm:ss')}")

    def handle_transcription_done(self):
        self.transcription_done = True
        QMessageBox.information(self, '字幕生成完毕', '字幕生成完毕\n')

class CustomListWidgetItem(QListWidgetItem):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            for item in self.selectedItems():
                item.setCheckState(Qt.Checked if item.checkState() == Qt.Unchecked else Qt.Unchecked)
        else:
            super().keyPressEvent(event)

class CustomListWidget(QListWidget):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            for item in self.selectedItems():
                item.setCheckState(Qt.Checked if item.checkState() == Qt.Unchecked else Qt.Unchecked)
        else:
            super().keyPressEvent(event)

class SubtitleGenerator(QDialog):
    def __init__(self, video_directory, video_filename, main_window):
        super().__init__()
        super().__init__()
        self.setWindowTitle("Subtitle Generator")
        self.setGeometry(100, 100, 1200, 750)

        # 获取主屏幕的几何信息
        screen_geometry = QApplication.desktop().screenGeometry()

        # 计算窗口居中时的位置
        x = int((screen_geometry.width() - self.width()) / 2)
        y = int((screen_geometry.height() - self.height()) / 2)

        self.move(x, y)

        self.video_directory = video_directory
        self.video_filename = video_filename
        self.videoPlayer = QMediaPlayer()  # 创建 QMediaPlayer 实例
        self.stop_playback = False
        self.is_auto_playing = False
        self.selected_items = []  # 用于跟踪选中的列表项
        self.main_window = main_window  # 传递主窗口的引用
        self.logTextEdit = QPlainTextEdit()  # 创建用于显示日志的文本编辑控件
        self.logTextEdit.setReadOnly(True)   # 设置为只读
        self.totalSelectedDurationLabel = QLabel("")
        self.videoPlayer.positionChanged.connect(self.check_subtitle_end)
        self.current_end_time = 0
        self.selected_subtitles = []  # 初始化 selected_subtitles 为空列表
        self.next_subtitle_index = 0
        self.totalVideoDuration = 0
        self.delayed_end_time = None
        self.create_ui()

    def create_ui(self):
        mainLayout = QHBoxLayout()  # 水平布局

        # 左侧布局
        leftLayout = QVBoxLayout()  # 创建一个垂直布局

        # 创建视频播放窗口
        videoWidget = QVideoWidget()
        videoWidget.setMinimumSize(800, 450)
        self.videoPlayer.setVideoOutput(videoWidget)
        leftLayout.addWidget(videoWidget)  # 将视频播放窗口添加到左侧布局

        # 创建日志窗口
        logGroupBox = QGroupBox("操作日志")
        logLayout = QVBoxLayout()
        logGroupBox.setStyleSheet("""
            QGroupBox {
                font: bold 14px;
                border: 1px solid gray;
                border-radius: 5px;
                margin-top: 1ex; /* leave space at the top for the title */
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
                color: blue;
            }

            QPushButton {
                background-color: #A3C1DA;
                color: black;
            }

            QLabel {
                color: green;
                font: bold;
            }
        """)
        self.logTextEdit = QPlainTextEdit()
        self.logTextEdit.setReadOnly(True)
        logLayout.addWidget(self.logTextEdit)
        logGroupBox.setLayout(logLayout)
        leftLayout.addWidget(logGroupBox)  # 将日志窗口添加到左侧布局

        mainLayout.addLayout(leftLayout)  # 将左侧布局添加到主布局

        # 右侧布局
        rightLayout = QVBoxLayout()

        # manualEditLabel = QLabel("视频字幕编辑操作提示：")
        # manualEditLabelTips = QLabel("1、点击字幕文件播放片段")
        # manualEditLabelTips1 = QLabel("2、拖动鼠标多选字幕，空格键选中")
        # rightLayout.addWidget(manualEditLabel)
        # rightLayout.addWidget(manualEditLabelTips)
        # rightLayout.addWidget(manualEditLabelTips1)

        # 字幕列表分组框
        subtitlesGroupBox = QGroupBox("字幕列表 -鼠标或空格点击操作")
        subtitlesLayout = QVBoxLayout()  # 分组框的垂直布局

        # 字幕列表样式设置
        subtitlesGroupBox.setStyleSheet("""
            QGroupBox {
                font: bold 14px;
                border: 1px solid #4A4A4A;
                border-radius: 10px;
                margin-top: 10px;
                background-color: #F5F5F5;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 3px 0 3px;
                padding: 0 10px;
                color: #333333;
            }
            QListWidget {
                background-color: #FFFFFF;
                color: #000000;
                border: none;
            }
            QListWidget::item {
                padding: 5px;
                border-bottom: 1px solid #E0E0E0;
            }
            QListWidget::item:selected {
                background-color: #87CEEB;
                color: #333333;
            }
            QListWidget::item:hover {
                background-color: #B0E0E6;
            }
        """)

        # 字幕列表控件
        self.subtitles_list = CustomListWidget()
        self.subtitles_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.subtitles_list.setFocusPolicy(Qt.StrongFocus)
        self.subtitles_list.itemClicked.connect(self.update_selected_items)

        # 将字幕列表添加到分组框布局中
        subtitlesLayout.addWidget(self.subtitles_list)
        subtitlesGroupBox.setLayout(subtitlesLayout)

        # 将分组框添加到主布局中
        rightLayout.addWidget(subtitlesGroupBox)

        # 字幕列表样式设置
        self.subtitles_list.setStyleSheet("""
            QListWidget {
                background-color: #f0f0f0;
                color: #333;
                border: 1px solid #ddd;
            }
            QListWidget::item {
                padding: 5px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background-color: #cde;
                color: #000;
            }
        """)
        # 字幕列表设置为多选模式
        self.subtitles_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.subtitles_list.setFocusPolicy(Qt.StrongFocus)
        self.subtitles_list.itemClicked.connect(self.update_selected_items)


        # 读取字幕文件
        srt_file = os.path.join(self.video_directory, os.path.splitext(self.video_filename)[0] + ".srt")
        if os.path.exists(srt_file):
            self.load_subtitles(srt_file)

        manualEditGroupBox = QGroupBox("手工编辑操作")
        manualEditLayout = QVBoxLayout()  # 可以选择垂直或水平布局，根据需要调整
        # 设置 QGroupBox 的样式
        manualEditGroupBox.setStyleSheet("""
            QGroupBox {
                font: bold 14px;
                border: 1px solid gray;
                border-radius: 5px;
                margin-top: 1ex; /* leave space at the top for the title */
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
                color: blue;
            }

            QPushButton {
                background-color: #A3C1DA;
                color: black;
            }

            QLabel {
                color: green;
                font: bold;
            }
        """)

        # 全选和全不选按钮
        selectButtonsLayout = QHBoxLayout()
        self.selectAllButton = QPushButton("全选")
        self.deselectAllButton = QPushButton("全不选")
        selectButtonsLayout.addWidget(self.selectAllButton)
        selectButtonsLayout.addWidget(self.deselectAllButton)
        manualEditLayout.addLayout(selectButtonsLayout)

        # 视频播放控制
        controlLayout = QHBoxLayout()

        self.videoPlayButton = QPushButton("播放选中字幕")
        self.videoPlayButton.clicked.connect(self.play_selected_subtitles)
        controlLayout.addWidget(self.videoPlayButton)

        # 停止播放按钮
        self.stopPlaybackButton = QPushButton("停止播放")
        self.stopPlaybackButton.clicked.connect(self.stop_playback_action)
        controlLayout.addWidget(self.stopPlaybackButton)

        # 时间显示标签
        # self.currentTimeLabel = QLabel("00:00:00")
        # self.totalTimeLabel = QLabel("00:00:00")
        manualEditLayout.addWidget(self.totalSelectedDurationLabel)

        # timeLayout = QHBoxLayout()
        # timeLayout.addWidget(self.currentTimeLabel)
        # timeLayout.addStretch()
        # timeLayout.addWidget(self.totalTimeLabel)
        #
        # rightLayout.addLayout(timeLayout)

        self.videoSlider = ClickableSlider(Qt.Horizontal)
        self.videoSlider.sliderMoved.connect(self.set_video_position)
        controlLayout.addWidget(self.videoSlider)

        self.videoTimeLabel = QLabel()
        controlLayout.addWidget(self.videoTimeLabel)

        manualEditLayout.addLayout(controlLayout)

        manualEditGroupBox.setLayout(manualEditLayout)

        rightLayout.addWidget(manualEditGroupBox)

        # 按钮布局
        buttonLayout = QVBoxLayout()
        self.cutVideoButton = QPushButton("AI 智能剪切视频")
        self.cutVideoButton.setStyleSheet("background-color: #FF5733; color: white; font-size: 16px; padding: 5px")

        self.saveVideoButton = QPushButton("合成短视频并保存")
        self.saveVideoButton.setStyleSheet("background-color: #FFC300; color: white;font-size: 16px; padding: 5px")

        self.saveVideoButton.clicked.connect(self.save_video)
        buttonLayout.addWidget(self.cutVideoButton)
        buttonLayout.addWidget(self.saveVideoButton)

        self.cutVideoButton.clicked.connect(self.cut_video)

        rightLayout.addLayout(buttonLayout)

        ExitLayout = QHBoxLayout()
        backButton = QPushButton("返回主窗口")
        backButton.clicked.connect(self.go_back_to_main_window)
        ExitLayout.addWidget(backButton)

        closeButton = QPushButton("关闭")
        closeButton.clicked.connect(self.close_dialog)
        ExitLayout.addWidget(closeButton)

        rightLayout.addLayout(ExitLayout)

        mainLayout.addLayout(rightLayout)
        self.setLayout(mainLayout)

        # 加载视频文件到 QMediaPlayer
        video_file_path = os.path.join(self.video_directory, self.video_filename)
        self.videoPlayer.setMedia(QMediaContent(QUrl.fromLocalFile(video_file_path)))
        self.update_total_duration_label()  # 更新总时长标签

        # 连接 QMediaPlayer 的信号
        self.videoPlayer.positionChanged.connect(self.update_slider_position)
        self.videoPlayer.durationChanged.connect(self.update_slider_range)
        self.videoSlider.sliderReleased.connect(self.slider_released)
        self.subtitles_list.itemClicked.connect(self.on_subtitle_clicked)
        self.subtitles_list.itemChanged.connect(self.update_selected_items)


        # 连接信号
        # self.videoPlayer.positionChanged.connect(self.update_current_time_label)
        self.videoPlayer.durationChanged.connect(self.update_total_time_label)
        self.videoPlayer.positionChanged.connect(self.update_current_subtitle_selection)

        self.selectAllButton.clicked.connect(self.select_all_subtitles)
        self.deselectAllButton.clicked.connect(self.deselect_all_subtitles)
        self.videoPlayer.stateChanged.connect(self.on_player_state_changed)

    # def update_current_time_label(self, position):
        # self.currentTimeLabel.setText(self.format_time(position))

    # 新增的方法
    def on_player_state_changed(self, state):
        if state == QMediaPlayer.StoppedState and self.is_auto_playing:
            self.play_next_subtitle()

    def update_total_time_label(self, duration):
        # self.totalTimeLabel.setText(self.format_time(duration))
        self.update_total_duration_label()

    def format_time(self, ms):
        seconds = round(ms / 1000)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)

    def go_back_to_main_window(self):
        self.accept()  # 关闭子窗口
        self.main_window.show()  # 显示主窗口

    def update_total_video_duration(self):
        # 获取视频的总时长
        duration = self.videoPlayer.duration()
        duration_str = self.format_time(duration)
        return duration_str

    def update_total_duration_label(self):
        total_duration = self.videoPlayer.duration()
        # self.totalTimeLabel.setText(self.format_time(total_duration))
        self.totalSelectedDurationLabel.setText(f"选中字幕视频的总长度：00:00:00 / {self.format_time(total_duration)}")

    def update_selected_items(self, item):
        # 更新列表项的选中状态
        if item.checkState() == Qt.Checked:
            if item not in self.selected_items:
                self.selected_items.append(item)
        else:
            if item in self.selected_items:
                self.selected_items.remove(item)

        # 更新选中字幕的总时长
        # 更新选中字幕的总时长
        total_duration = 0
        for index in range(self.subtitles_list.count()):
            item = self.subtitles_list.item(index)
            if item.checkState() == Qt.Checked:
                start_time, end_time = item.data(Qt.UserRole)
                total_duration += end_time - start_time

        # 更新总时长标签
        total_duration_str = self.format_time(total_duration)
        total_video_duration_str = self.update_total_video_duration()  # 获取并更新视频的总时长
        self.totalSelectedDurationLabel.setText(f"选中字幕视频的总长度：{total_duration_str} / {total_video_duration_str}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            for index in range(self.subtitles_list.count()):
                item = self.subtitles_list.item(index)
                if item.checkState() == Qt.Unchecked:
                    item.setCheckState(Qt.Checked)
                else:
                    item.setCheckState(Qt.Unchecked)
            event.accept()  # 标记事件已处理
        else:
            super().keyPressEvent(event)

    def stop_playback_action(self):
        self.stop_playback = False
        self.is_auto_playing = False
        self.videoPlayer.pause()

    def select_all_subtitles(self):
        for index in range(self.subtitles_list.count()):
            item = self.subtitles_list.item(index)
            item.setCheckState(Qt.Checked)

    def deselect_all_subtitles(self):
        for index in range(self.subtitles_list.count()):
            item = self.subtitles_list.item(index)
            item.setCheckState(Qt.Unchecked)

    def update_current_subtitle_selection(self, position):
        if not self.is_auto_playing:
            return

        current_index = None
        for index in range(self.subtitles_list.count()):
            start_time, end_time = self.subtitles_list.item(index).data(Qt.UserRole)
            if start_time <= position < end_time:
                current_index = index
                break

        if current_index is not None:
            self.subtitles_list.setCurrentRow(current_index)
            self.subtitles_list.scrollToItem(self.subtitles_list.item(current_index),
                                             QAbstractItemView.PositionAtCenter)
    def load_subtitles(self, srt_file):
        with open(srt_file, 'r', encoding='utf-8') as file:
            subtitles = file.read().split('\n\n')
            for subtitle in subtitles:
                parts = subtitle.split('\n')
                if len(parts) >= 3:
                    number = parts[0]
                    time_range = parts[1]
                    text = '\n'.join(parts[2:])
                    item = CustomListWidgetItem(f"{number}. {time_range}\n{text}")
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)  # 添加复选框
                    item.setCheckState(Qt.Unchecked)  # 默认状态为未选中
                    self.subtitles_list.addItem(item)
                    start_time, end_time = self.parse_time_range(time_range)
                    item.setData(Qt.UserRole, (start_time, end_time))

    def get_selected_subtitles(self):
        selected_subtitles = []
        for index in range(self.subtitles_list.count()):
            item = self.subtitles_list.item(index)
            if item.checkState() == Qt.Checked:  # 检查是否被选中
                start_time, end_time = item.data(Qt.UserRole)
                selected_subtitles.append((start_time, end_time))
        return selected_subtitles

    def play_selected_subtitles(self):
        self.is_auto_playing = True
        self.selected_subtitles = self.get_selected_subtitles()
        print(self.selected_subtitles)
        if not self.selected_subtitles:
            QMessageBox.information(self, "提示", "没有选中的字幕。")
            self.is_auto_playing = False
            return
        self.delayed_end_time = 0
        self.play_next_subtitle()

    def play_next_subtitle(self):
        # self.is_auto_playing = True
        if self.is_auto_playing == False:
            return

        if not self.selected_subtitles:
            self.stop_playback = False
            self.is_auto_playing = False
            self.videoPlayer.pause()
            print("所有字幕播放完成")
            return

        # 获取下一个要播放的字幕片段
        next_start_time, next_end_time = self.selected_subtitles[0]

        # 检查是否有记录的延迟结束时间
        if self.delayed_end_time and self.delayed_end_time > next_start_time:
            start_time = self.delayed_end_time
            print(f"使用延迟结束时间作为开始: {start_time}")
            _ , self.current_end_time = self.selected_subtitles.pop(0)
        else:
            start_time, self.current_end_time = self.selected_subtitles.pop(0)
            print(f"播放字幕片段：开始时间 {start_time}, 结束时间 {self.current_end_time}")

        self.videoPlayer.setPosition(start_time)
        self.videoPlayer.play()

    def check_subtitle_end(self, position):
        if self.is_auto_playing == False:
            return

        print(f"当前播放位置: {position}, 预定结束位置: {self.current_end_time}")
        if position >= self.current_end_time:
            self.delayed_end_time = position
            print(f"记录的延迟结束时间: {self.delayed_end_time}")
            self.play_next_subtitle()

    def parse_time_range(self, time_range):
        start_str, end_str = time_range.split(' --> ')
        start_time = self.time_str_to_msecs(start_str)
        end_time = self.time_str_to_msecs(end_str)
        return start_time, end_time

    def time_str_to_msecs(self, time_str):
        h, m, s = map(float, time_str.replace(',', '.').split(':'))
        return int((h * 3600 + m * 60 + s) * 1000)

    def on_subtitle_clicked(self, item):
        self.is_auto_playing = False
        start_time, end_time = item.data(Qt.UserRole)
        self.videoPlayer.setPosition(start_time)  # 设置视频播放器的位置到字幕开始时间
        self.videoPlayer.play()  # 开始播放视频
        print("是 on_subtitle_clicked 这里播放的视频吗？");

        # 设置定时器以在字幕结束时停止播放
        self.play_timer = QTimer(self)
        self.play_timer.setSingleShot(True)
        self.play_timer.timeout.connect(self.stop_video_at_end)
        self.play_timer.start(end_time - start_time)

    def stop_video_at_end(self):
        if self.is_auto_playing == False:
            print("stop_video_at_end 停止视频了");
            self.videoPlayer.pause()  # 暂停视频播放

    def toggle_video_play_pause(self):
        if self.videoPlayer.state() == QMediaPlayer.PlayingState:
            self.videoPlayer.pause()
        else:
            self.videoPlayer.play()
            print("是 toggle_video_play_pause 这里播放的视频吗？");

    def set_video_position(self, position):
        self.videoPlayer.setPosition

    def update_slider_position(self, position):
        if not self.videoSlider.isSliderDown():
            self.videoSlider.setValue(position)

    def update_slider_range(self, duration):
        self.videoSlider.setRange(0, duration)

    def slider_released(self):
        position = self.videoSlider.value()
        self.videoPlayer.setPosition(position)

    def show_cutting_settings(self):
        self.cuttingDialog = CuttingSettingsDialog(self)
        self.cuttingDialog.show()

    def cut_video(self):
        # 添加剪切视频的逻辑
        temp_srt_file = os.path.join(self.video_directory, "temp_subtitles_ai.srt")

        if os.path.exists(temp_srt_file):
            os.remove(temp_srt_file)

        with open(temp_srt_file, 'w', encoding='utf-8') as file:
            for index in range(self.subtitles_list.count()):
                item = self.subtitles_list.item(index)
                # if item.checkState() == Qt.Checked:
                #     parts = item.text().split('\n', 1)  # 分割为最多两个部分
                #     if len(parts) == 2:
                #         number_part = parts[0].split('.')[0].strip()  # 提取序号
                #         time_range_part = parts[0].split('.', 1)[1].strip()  # 提取时间范围
                #         subtitle_text_part = parts[1].strip()  # 提取字幕文本
                #         file.write(f"{number_part}\n{time_range_part}\n{subtitle_text_part}\n\n")
                #     else:
                #         print(f"字幕格式不正确: {item.text()}")  # 调试输出
                #         QMessageBox.warning(self, '警告', '创建临时字幕文件失败，请检查视频所在目录是否有读写权限')
                parts = item.text().split('\n', 1)  # 分割为最多两个部分
                if len(parts) == 2:
                    number_part = parts[0].split('.')[0].strip()  # 提取序号
                    time_range_part = parts[0].split('.', 1)[1].strip()  # 提取时间范围
                    subtitle_text_part = parts[1].strip()  # 提取字幕文本
                    file.write(f"{number_part}\n{time_range_part}\n{subtitle_text_part}\n\n")
                else:
                    print(f"字幕格式不正确: {item.text()}")  # 调试输出
                    QMessageBox.warning(self, '警告', '创建临时字幕文件失败，请检查视频所在目录是否有读写权限')

        self.show_cutting_settings()
        pass

    def update_subtitles_selection(self, new_subtitle_file):
        # 解析编辑后的字幕
        pattern = re.compile(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}")
        edited_subs = pattern.findall(new_subtitle_file)
        print(edited_subs)

        # # 创建时间戳到字幕编号的映射
        # timestamps_map = {sub[0] for sub in edited_subs}
        # print(timestamps_map)
        # 遍历字幕列表并更新选中状态
        for index in range(self.subtitles_list.count()):
            item = self.subtitles_list.item(index)
            item_timestamp = item.text().split('\n')[0].strip()  # 去除前导和尾随空格
            edited_item_timestamp = pattern.findall(item_timestamp)
            # print("item_timestamp:" + item_timestamp)

            # 检查item_timestamp是否在edited_subs中的时间戳列表中
            if edited_item_timestamp[0] in edited_subs:
                print("item_timestamp checked:" + edited_item_timestamp[0])
                item.setCheckState(Qt.Checked)  # 标记为选中
            else:
                print("item_timestamp unchecked:" + edited_item_timestamp[0])
                item.setCheckState(Qt.Unchecked)  # 标记为未选中
        # # 读取新的字幕文件
        # print("根据智能剪辑的结果设置字幕列表")
        # return
        # with open(new_subtitle_file, 'r', encoding='utf-8') as file:
        #     new_subtitles = file.read().split('\n\n')
        #
        # # 解析新字幕文件中的时间范围
        # new_subtitles_ranges = []
        # for subtitle in new_subtitles:
        #     parts = subtitle.split('\n')
        #     if len(parts) >= 3:
        #         time_range = parts[1]
        #         start_time, end_time = self.parse_time_range(time_range)
        #         new_subtitles_ranges.append((start_time, end_time))
        #
        # # 更新字幕列表的选中状态
        # for index in range(self.subtitles_list.count()):
        #     item = self.subtitles_list.item(index)
        #     start_time, end_time = item.data(Qt.UserRole)
        #     print("time_range:" + time_range)
        #     print("item.text():" + item.text())
        #     if time_range in item.text():
        #         item.setCheckState(Qt.Checked)
        #         print("checked")
        #     else:
        #         item.setCheckState(Qt.Unchecked)
        #         print("unchecked")

    def save_video(self):
        # 添加保存视频的逻辑
        print("save_video called")
        selected_subtitles = self.get_selected_subtitles()
        if not selected_subtitles:
            QMessageBox.warning(self, '警告', '没有选中的字幕')
            return

        # 获取视频文件的完整路径
        video_file_path = os.path.join(self.video_directory, self.video_filename)

        # 构建剪辑后的视频文件名
        base_name, ext = os.path.splitext(video_file_path)
        cut_video_file_path = f"{base_name}_cut{ext}"

        # 检查剪辑后的文件是否存在，如果存在则删除
        if os.path.exists(cut_video_file_path):
            try:
                os.remove(cut_video_file_path)
            except OSError as e:
                # self.update_log(f'无法删除已存在的剪辑文件: {e}')
                self.update_autocut_log(f'无法删除已存在的剪辑文件: {e}' + "请手工删除后重试！\n")
                return

        temp_srt_file = os.path.join(self.video_directory, "temp_subtitles.srt")

        if os.path.exists(temp_srt_file):
            os.remove(temp_srt_file)

        with open(temp_srt_file, 'w', encoding='utf-8') as file:
            for index in range(self.subtitles_list.count()):
                item = self.subtitles_list.item(index)
                if item.checkState() == Qt.Checked:
                    parts = item.text().split('\n', 1)  # 分割为最多两个部分
                    if len(parts) == 2:
                        number_part = parts[0].split('.')[0].strip()  # 提取序号
                        time_range_part = parts[0].split('.', 1)[1].strip()  # 提取时间范围
                        subtitle_text_part = parts[1].strip()  # 提取字幕文本
                        file.write(f"{number_part}\n{time_range_part}\n{subtitle_text_part}\n\n")
                    else:
                        print(f"字幕格式不正确: {item.text()}")  # 调试输出

        # QMessageBox.information(self, '通知', '临时字幕文件已保存')

        # 检查临时字幕文件是否存在
        if os.path.exists(temp_srt_file):
            # 获取视频文件的完整路径
            video_file_path = os.path.join(self.video_directory, self.video_filename)

            # 创建 Cutter 参数对象
            cutter_args = CutterArgs(inputs=[video_file_path, temp_srt_file])
            print("文件目录：" + video_file_path + " " + temp_srt_file)

            # 禁用保存视频按钮
            self.saveVideoButton.setEnabled(False)

            # 创建并启动视频合成线程
            self.video_merge_thread = VideoMergeThread(cutter_args)
            self.video_merge_thread.finished.connect(self.on_video_merge_finished)
            self.video_merge_thread.log_signal.connect(self.update_log)
            self.video_merge_thread.start()
        else:
            QMessageBox.warning(self, '警告', '临时字幕文件未找到')

    def on_video_merge_finished(self):
        # 视频合成完成后的操作
        self.update_log("视频合成完毕。")
        self.saveVideoButton.setEnabled(True)  # 启用保存视频按钮

    def merge_video_with_subtitles(self, srt_file):
        # 获取视频文件的完整路径
        video_file_path = os.path.join(self.video_directory, self.video_filename)

        # 构建剪辑后的视频文件名
        base_name, ext = os.path.splitext(video_file_path)
        cut_video_file_path = f"{base_name}_cut{ext}"

        # 检查剪辑后的文件是否存在，如果存在则删除
        if os.path.exists(cut_video_file_path):
            try:
                os.remove(cut_video_file_path)
            except OSError as e:
                # self.update_log(f'无法删除已存在的剪辑文件: {e}')
                self.update_autocut_log(f'无法删除已存在的剪辑文件: {e}' + "请手工删除后重试！\n")
                return

        self.update_autocut_log("正在合并字幕文件并生成视频，这个过程可能需要较长时间，请稍候...\n")

        # 创建 Cutter 参数对象
        cutter_args = CutterArgs(inputs=[video_file_path, srt_file])

        # 创建 Cutter 实例并运行
        cutter = Cutter(cutter_args)

        # 重定向标准输出到日志框
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            cutter.run()
            # 获取输出并显示在日志框
            output = sys.stdout.getvalue()
            self.update_log(output)
        except Exception as e:
            self.update_log(f'剪辑过程中发生错误: {e}')
        finally:
            # 恢复标准输出
            sys.stdout = old_stdout

    def update_log(self, message):
        # 将消息添加到日志框
        self.logTextEdit.appendPlainText(message)

    def update_autocut_log(self, log_line):
        # 更新信息提示框
        self.logTextEdit.insertPlainText(log_line)
        cursor = self.logTextEdit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.logTextEdit.setTextCursor(cursor)

    def handle_autocut_done(self):
        # autocut 完成后的处理
        QMessageBox.information(self, '合并完成', '视频已成功合并')
        self.saveVideoButton.setEnabled(True)


    def close_dialog(self):
        self.accept()

class SubtitleGenerationThread(QThread):
    finished = pyqtSignal()
    log_signal = pyqtSignal(str)

    def __init__(self, transcriber):
        QThread.__init__(self)
        self.transcriber = transcriber

    def run(self):
        # 重定向标准输出到字符串
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            self.log_signal.emit("->>开始生成字幕，此过程可能耗时较长，请耐心等待......\n")
            self.log_signal.emit("->>如果您是第一次运行程序，需要下载相应的模型数据。\n")
            self.transcriber.run()
            output = sys.stdout.getvalue()
            self.log_signal.emit(output)
            self.log_signal.emit("->>恭喜您！视频字幕已经成功完成。\n")
            self.log_signal.emit("->>请点击[AI视频编辑合成工具]，继续进行视频编辑工作。\n")

        except Exception as e:
            self.log_signal.emit(f'->>生成字幕过程中发生错误: {e}\n')
        finally:
            sys.stdout = old_stdout
            self.finished.emit()

class CuttingSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("智能剪切参数设置")
        self.setGeometry(100, 100, 500, 300)
        # 获取主屏幕的几何信息
        screen_geometry = QApplication.desktop().screenGeometry()

        # 计算窗口居中时的位置
        x = int((screen_geometry.width() - self.width()) / 2)
        y = int((screen_geometry.height() - self.height()) / 2)

        self.move(x, y)
        self.create_ui()

    def create_ui(self):
        layout = QVBoxLayout(self)

        # 视频长度设置
        lengthLayout = QHBoxLayout()
        self.lengthLabel = QLabel("视频长度（分钟）:")
        self.lengthSpinBox = QSpinBox()
        self.lengthSpinBox.setRange(1, 60)  # 假设视频长度范围为1到60分钟
        lengthLayout.addWidget(self.lengthLabel)
        lengthLayout.addWidget(self.lengthSpinBox)
        layout.addLayout(lengthLayout)

        # 剪切风格选项
        styleLayout = QVBoxLayout()
        self.styleLabel = QLabel("剪切风格:")
        self.styleComboBox = QComboBox()
        # self.styleComboBox.addItems(["1、精彩片段", "2、对话片段", "3、叙事剪辑", "4、自动推荐内容"])
        self.styleComboBox.addItem("精彩片段", "1")
        self.styleComboBox.addItem("对话片段", "2")
        self.styleComboBox.addItem("叙事剪辑", "3")
        styleLayout.addWidget(self.styleLabel)
        styleLayout.addWidget(self.styleComboBox)
        layout.addLayout(styleLayout)

        # 日志窗口
        self.logTextEdit = QPlainTextEdit()
        self.logTextEdit.setReadOnly(True)
        layout.addWidget(self.logTextEdit)

        # 按钮
        self.startButton = QPushButton("开始剪辑")
        self.startButton.clicked.connect(self.start_cutting)
        layout.addWidget(self.startButton)

    def start_cutting(self):
        duration = self.lengthSpinBox.value()
        style = self.styleComboBox.currentText()
        self.logTextEdit.appendPlainText(f"->>开始剪辑：长度 {duration} 分钟，风格 '{style}'")

        # 假设 self.subtitle_file 是要处理的字幕文件路径
        temp_srt_file = os.path.join(self.parent().video_directory, "temp_subtitles_ai.srt")

        selected_style = self.styleComboBox.currentData()  # 获取选中的风格编号

        cutting_params = {"duration": duration, "style": style}
        self.request_thread = CuttingRequestThread(temp_srt_file, cutting_params, selected_style)
        self.request_thread.finished.connect(self.on_cutting_finished)
        self.request_thread.start()

    def on_cutting_finished(self, new_subtitle_file):
        # 在这里更新 UI，显示处理后的字幕文件路径
        self.logTextEdit.appendPlainText(f"->>新字幕文件为：{new_subtitle_file}")
        self.parent().update_subtitles_selection(new_subtitle_file)
        self.logTextEdit.appendPlainText(f"->>剪辑完成，字幕列表已经更新")
        self.logTextEdit.appendPlainText(f"->>请继续编辑或是点击【合成短视频并保存】按钮生成短视频！")

class CuttingRequestThread(QThread):
    finished = pyqtSignal(str)  # 用于传递返回的字幕文件路径

    def __init__(self, subtitle_file, cutting_params, style):
        QThread.__init__(self)
        self.subtitle_file = subtitle_file
        self.cutting_params = cutting_params
        self.style = style  # 添加 style 属性

    def run(self):
        new_subtitle_file = self.send_request_to_server(self.subtitle_file, self.cutting_params, self.style)
        self.finished.emit(new_subtitle_file)

    def send_request_to_server(self, subtitle_file, cutting_params, style):
        # 服务器的 URL
        url = "https://app.editclippro.com/process_subtitles"

        # 确保正确读取和发送字幕文件内容
        with open(subtitle_file, 'r', encoding='utf-8') as file:
            subtitles_content = file.read()

        data = {
            'user_id': 'your_user_id',
            'subtitles': subtitles_content,
            'duration': cutting_params['duration'],
            'style': style
        }

        try:
            response = requests.post(url, json=data)
            response.raise_for_status()
            response_data = response.json()
            return response_data.get('processed_subtitles')
        except requests.RequestException as e:
            print(f"请求失败: {e}")
            return None

class CutterArgs:
    def __init__(self, inputs, encoding='utf-8', force=False, bitrate='2000k'):
        self.inputs = inputs
        self.encoding = encoding
        self.force = force
        self.bitrate = bitrate

class TranscribeArgs:
    def __init__(self, inputs, lang, encoding='utf-8', force=False, whisper_mode='whisper', whisper_model='base', device='cpu', vad='0', prompt=''):
        self.inputs = inputs
        self.lang = lang
        self.encoding = encoding
        self.force = force
        self.whisper_mode = whisper_mode
        self.whisper_model = whisper_model
        self.device = device
        self.vad = vad
        self.prompt = prompt  # 添加 prompt 属性

class LogThread(QObject, threading.Thread):
    log_signal = pyqtSignal(str)
    transcription_done_signal = pyqtSignal()

    def __init__(self, command):
        super().__init__()
        self.command = command

    def format_log_line(self, line):
        current_time = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        if "]" in line:
            return current_time + "AutoEditer - " + " " + line.split(']', 1)[-1]
        else:
            return current_time + "AutoEditer - " + line

        print(current_time)

    def run(self):
        try:
            process = subprocess.Popen(
                self.command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            while process.poll() is None:
                output_line = process.stdout.readline()
                formatted_line = self.format_log_line(output_line)
                print(formatted_line)
                self.log_signal.emit(formatted_line)
                if "Done transcription" in output_line:
                    self.transcription_done_signal.emit()
        except subprocess.CalledProcessError as e:
            formatted_error = self.format_log_line(f"命令执行错误: {e.returncode}\n")
            self.log_signal.emit(formatted_error)
            formatted_output = self.format_log_line(f"错误输出: {e.output}\n")
            self.log_signal.emit(formatted_output)

class VideoMergeThread(QThread):
    finished = pyqtSignal()
    log_signal = pyqtSignal(str)

    def __init__(self, cutter_args):
        QThread.__init__(self)
        self.cutter_args = cutter_args

    def run(self):
        # 创建 Cutter 实例
        cutter = Cutter(self.cutter_args)

        # 重定向标准输出到字符串
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            self.log_signal.emit("开始合成视频...")
            cutter.run()
            output = sys.stdout.getvalue()
            self.log_signal.emit(output)
            self.log_signal.emit("视频合成过程完成。")
        except Exception as e:
            self.log_signal.emit(f'视频合成过程中发生错误: {e}')
        finally:
            sys.stdout = old_stdout
            self.finished.emit()

class AutocutThread(QObject, threading.Thread):
    log_signal = pyqtSignal(str)
    autocut_done_signal = pyqtSignal()

    def __init__(self, command):
        super().__init__()
        self.command = command

    def format_log_line(self, line):
        current_time = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        if "]" in line:
            # 分割日志行，并检查']'后是否有内容
            split_line = line.split(']', 1)
            if len(split_line) > 1 and split_line[1].strip():
                return current_time + " AutoEditer - " + split_line[1].strip() + '\n'
            else:
                return current_time + " AutoEditer - " + line.strip() + '\n'
        elif line.strip():
            return current_time + " AutoEditer - " + line.strip() + '\n'
        else:
            return ""

    def run(self):

        self.log_signal.emit(self.format_log_line("正在合并字幕生成短视频，生成字幕需要一些时间，请稍候...\n"))

        try:
            process = subprocess.Popen(
                self.command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            while process.poll() is None:
                output_line = process.stdout.readline()
                print("output_line" + output_line)
                formatted_line = self.format_log_line(output_line)
                print(formatted_line)
                self.log_signal.emit(formatted_line)
            self.autocut_done_signal.emit()
        except subprocess.CalledProcessError as e:
            formatted_error = self.format_log_line(f"命令执行错误: {e.returncode}\n")
            self.log_signal.emit(formatted_error)
            formatted_output = self.format_log_line(f"错误输出: {e.output}\n")
            self.log_signal.emit(formatted_output)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    player = VideoPlayer()
    player.show()
    sys.exit(app.exec_())
