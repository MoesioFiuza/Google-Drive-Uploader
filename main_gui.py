# main_gui.py
import os
import sys
import logging
import time
import math

try:
    from PySide6.QtWidgets import (
        QApplication, QFileDialog, QMessageBox, QMainWindow,
        QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton,
        QProgressBar, QSizePolicy, QSpacerItem,
        QGraphicsOpacityEffect
    )
    from PySide6.QtCore import (
        Qt, QThread, Signal, QTimer, QPropertyAnimation,
        QEasingCurve, QAbstractAnimation, QParallelAnimationGroup, QRect
    )
    from PySide6.QtGui import QIcon, QPixmap, QMovie, QPainter, QPen, QColor, QPalette, QScreen
    GUI_AVAILABLE = True
except ImportError as e:
    GUI_AVAILABLE = False
    print("PySide6 não encontrado. A interface gráfica não estará disponível.")
    print("Execute 'pip install PySide6' para instalar.")
    sys.exit(1)

try:
    from drive_uploader import DriveUploader, CLIENT_SECRETS_FILE
except ImportError:
    print("ERRO: Não foi possível encontrar o arquivo 'drive_uploader.py'.")
    print("Certifique-se de que 'drive_uploader.py' está no mesmo diretório que este script.")
    if GUI_AVAILABLE:
        app_check = QApplication.instance()
        if app_check is None: app_check = QApplication(sys.argv)
        QMessageBox.critical(None,"Erro de Importação", "Não foi possível encontrar 'drive_uploader.py'.\nVerifique se o arquivo está no lugar correto.")
    sys.exit(1)

LOG_FILENAME = 'drive_upload.log'
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    filename=LOG_FILENAME,
                    filemode='a')
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logging.getLogger().addHandler(console_handler)

def format_size(size_bytes):
    if size_bytes < 0: return "0 B"
    if size_bytes == 0: return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    if size_bytes < 1: return "0 B"
    try:
        i = int(math.floor(math.log(size_bytes, 1024)))
    except ValueError:
        i = 0
    i = min(i, len(size_name) - 1)
    p = math.pow(1024, i)
    try:
        s = round(size_bytes / p, 2)
    except ZeroDivisionError:
        s = 0
    return f"{s} {size_name[i]}"

def format_time(seconds):
    if seconds is None or seconds < 0 or not isinstance(seconds, (int, float)):
         return "--:--:--"
    try:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h < 0 or m < 0 or s < 0: return "--:--:--"
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception as e:
        logging.warning(f"Erro ao formatar tempo {seconds}: {e}")
        return "--:--:--"

class UploadWorker(QThread):
    scan_complete = Signal(int, int)
    update_status = Signal(str)
    update_current_folder = Signal(str)
    update_file_progress = Signal(str, int, int)
    update_overall_progress = Signal(int, int)
    finished = Signal(bool, int, int)
    error_occurred = Signal(str)

    def __init__(self, uploader: DriveUploader, local_path: str, drive_id: str):
        super().__init__()
        self.uploader = uploader
        self.local_path = local_path
        self.drive_id = drive_id
        self._is_running = True
        self.files_processed_count = 0
        self.bytes_processed_so_far = 0
        self.total_bytes_target = 0
        self.current_file_progress_bytes = {}

    def run(self):
        logging.info("UploadWorker: Iniciado.")
        self.uploader.reset_stop_request()
        self._is_running = True
        self.files_processed_count = 0
        self.bytes_processed_so_far = 0
        self.total_bytes_target = 0
        self.current_file_progress_bytes.clear()
        success = False
        files_uploaded_final = 0
        bytes_uploaded_final = 0

        try:
            self.update_status.emit("Escaneando diretório local...")
            total_files, total_bytes = self.uploader.scan_local_directory(self.local_path)
            if total_files == -1:
                self.update_status.emit("Escaneamento cancelado.")
                self.finished.emit(False, 0, 0)
                return

            if not self._is_running:
                 logging.info("UploadWorker: Parada detectada após scan.")
                 self.finished.emit(False, 0, 0)
                 return

            self.total_bytes_target = total_bytes
            self.scan_complete.emit(total_files, total_bytes)
            if total_files == 0:
                self.update_status.emit("Nenhum arquivo encontrado para upload.")
                self.finished.emit(True, 0, 0)
                return
            elif total_bytes == 0:
                 self.update_status.emit("Aviso: Arquivos encontrados, mas tamanho total é 0.")

            if self._is_running:
                self.update_status.emit("Iniciando upload...")
                success, files_uploaded_final, bytes_uploaded_final = self.uploader.upload_directory(
                    self.local_path,
                    self.drive_id,
                    progress_callback=self.handle_file_progress_update,
                    status_callback=self.handle_status_update,
                    current_folder_callback=self.handle_current_folder_update
                )

        except FileNotFoundError as e:
             logging.error(f"UploadWorker: Erro de arquivo não encontrado: {e}", exc_info=True)
             self.error_occurred.emit(f"Erro Crítico: {e}")
             success = False
        except ConnectionAbortedError as e:
             logging.error(f"UploadWorker: Erro de autenticação: {e}", exc_info=True)
             self.error_occurred.emit(f"Erro de Autenticação: {e}")
             success = False
        except ConnectionError as e:
             logging.error(f"UploadWorker: Erro de conexão com Drive: {e}", exc_info=True)
             self.error_occurred.emit(f"Erro de Conexão: {e}")
             success = False
        except Exception as e:
            logging.error(f"UploadWorker: Erro inesperado: {e}", exc_info=True)
            self.error_occurred.emit(f"Erro inesperado: {e}")
            success = False
        finally:
            if self._is_running or not success:
                 self.finished.emit(success, files_uploaded_final, bytes_uploaded_final)
            logging.info(f"UploadWorker: Finalizado. Sucesso reportado: {success}")

    def handle_status_update(self, message: str):
        if self._is_running:
            self.update_status.emit(message)

    def handle_current_folder_update(self, folder_name: str):
         if self._is_running:
            self.update_current_folder.emit(folder_name)

    def handle_file_progress_update(self, filename: str, percentage: int, file_size_bytes: int):
        if self._is_running:
            bytes_done_this_file = int((percentage / 100.0) * file_size_bytes)
            previous_bytes = self.current_file_progress_bytes.get(filename, 0)
            byte_delta = bytes_done_this_file - previous_bytes

            if byte_delta > 0:
                 self.bytes_processed_so_far += byte_delta

            if self.total_bytes_target > 0:
                self.bytes_processed_so_far = min(self.bytes_processed_so_far, self.total_bytes_target)
            else:
                 self.bytes_processed_so_far = max(0, self.bytes_processed_so_far)

            self.current_file_progress_bytes[filename] = bytes_done_this_file

            if percentage == 100 and previous_bytes < file_size_bytes:
                self.files_processed_count += 1

            self.update_file_progress.emit(filename, percentage, file_size_bytes)
            self.update_overall_progress.emit(self.files_processed_count, self.bytes_processed_so_far)

    def stop(self):
        logging.info("UploadWorker: Recebida solicitação para parar.")
        self._is_running = False
        self.uploader.request_stop()

class CustomProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, event):
        super().paintEvent(event)

class ElegantNotification(QWidget):
    def __init__(self, message, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("ElegantNotificationWidget")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)

        icon_label = QLabel()
        icon_pixmap = QPixmap('success_icon.png')
        if icon_pixmap.isNull():
            logging.warning("Ícone 'success_icon.png' para notificação não encontrado.")
            icon_label.setText("✓")
            icon_label.setMinimumWidth(30)
        else:
            icon_label.setPixmap(icon_pixmap.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(icon_label)

        message_label = QLabel(message)
        layout.addWidget(message_label)

        self.animation = QPropertyAnimation(self, b"geometry")
        self.animation.setDuration(400)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)

    def show_notification(self):
        try:
            primary_screen = QApplication.primaryScreen()
            if not primary_screen:
                 logging.error("Não foi possível obter a tela primária para notificação.")
                 self.show() # Tenta mostrar na posição padrão
                 QTimer.singleShot(3500, self.close)
                 return

            screen_geometry = primary_screen.availableGeometry()
            popup_width = 350
            popup_height = 80
            margin = 15
            start_x = screen_geometry.width()
            end_x = screen_geometry.width() - popup_width - margin
            y = screen_geometry.height() - popup_height - margin

            self.setGeometry(start_x, y, popup_width, popup_height)
            self.show()

            self.animation.setStartValue(QRect(start_x, y, popup_width, popup_height))
            self.animation.setEndValue(QRect(end_x, y, popup_width, popup_height))
            self.animation.start()

            QTimer.singleShot(3500, self.close)
        except Exception as e:
            logging.error(f"Erro ao mostrar notificação: {e}")
            self.close() # Fecha se der erro


class UploadWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Drive Uploader")
        self.setGeometry(100, 100, 700, 500)
        icon_path = 'drive_icon.png'
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            logging.warning(f"Ícone da janela não encontrado: {icon_path}")

        self.uploader = DriveUploader()
        self.worker: UploadWorker | None = None
        self.total_files = 0
        self.total_size_bytes = 0
        self.upload_start_time = None
        self.source_path = ""
        self.destination_id = ""
        self.eta_image_valid = False
        self.anim_left_valid = False
        self.anim_middle_valid = False
        self.anim_movie_middle = None
        self.fade_group = None

        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_stylesheet()

        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._update_elapsed_time_display)
        self.elapsed_timer.setInterval(1000)

        logging.info("Janela principal inicializada.")

    def _apply_stylesheet(self):
         qss_file = 'stylesheet.qss'
         try:
             with open(qss_file, 'r', encoding='utf-8') as f:
                 style = f.read()
                 self.setStyleSheet(style)
                 logging.info(f"Stylesheet '{qss_file}' aplicado.")
         except FileNotFoundError:
              logging.warning(f"Arquivo de stylesheet '{qss_file}' não encontrado.")
         except Exception as e:
              logging.error(f"Erro ao ler ou aplicar stylesheet '{qss_file}': {e}")

    def _create_widgets(self):
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.source_label = QLabel("Pasta de Origem:")
        self.source_lineedit = QLineEdit()
        self.source_lineedit.setReadOnly(True)
        self.source_lineedit.setPlaceholderText("Selecione a pasta local...")
        self.source_lineedit.setToolTip("Selecione a pasta local que deseja fazer upload")
        self.source_button = QPushButton(" Selecionar...")
        self.source_button.setIcon(QIcon.fromTheme('document-open', QIcon()))

        self.dest_label = QLabel("ID Pasta Destino Drive:")
        self.dest_lineedit = QLineEdit()
        self.dest_lineedit.setPlaceholderText("Cole o ID da pasta do Google Drive aqui")
        self.dest_lineedit.setToolTip("Cole o ID da pasta do Google Drive de destino")

        self.progress_bar = CustomProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Pronto")

        self.status_label_title = QLabel("Status:")
        self.status_label_value = QLabel("Pronto")
        self.folder_label_title = QLabel("Pasta Atual:")
        self.folder_label_value = QLabel("-")
        self.files_label_title = QLabel("Arquivos:")
        self.files_label_value = QLabel("0 / 0")
        self.size_label_title = QLabel("Tamanho:")
        self.size_label_value = QLabel("0 B / 0 B")
        self.eta_label_title = QLabel("Tempo Restante (ETA):")
        self.eta_label_value = QLabel("--:--:--")
        self.elapsed_label_title = QLabel("Tempo Decorrido:")
        self.elapsed_label_value = QLabel("00:00:00")
        self.start_button = QPushButton(" Iniciar Upload")
        self.start_button.setToolTip("Iniciar o processo de upload")
        self.start_button.setIcon(QIcon.fromTheme('media-playback-start', QIcon()))

        self.cancel_button = QPushButton(" Cancelar")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("Cancelar o upload em andamento")
        self.cancel_button.setIcon(QIcon.fromTheme('process-stop', QIcon()))

        self.eta_image_label = QLabel()
        self.eta_image_label.setObjectName("EtaImageLabel")
        self.eta_image_path = "ETA.webp"
        self.eta_pixmap = QPixmap(self.eta_image_path)
        if self.eta_pixmap.isNull():
            logging.warning(f"Não foi possível carregar a imagem do ETA: {self.eta_image_path}")
            self.eta_image_valid = False
        else:
            self.eta_image_label.setPixmap(self.eta_pixmap)
            self.eta_image_valid = True
        self.eta_image_label.setAlignment(Qt.AlignCenter)
        self.eta_image_label.setVisible(False)

        self.anim_label_left = QLabel(self.central_widget)
        self.anim_label_middle = QLabel(self.central_widget)
        self.anim_pixmap_left = QPixmap("file.jpg")
        self.anim_movie_middle_path = "comeback.webp"
        self.anim_movie_middle = QMovie(self.anim_movie_middle_path)

        self.anim_left_valid = not self.anim_pixmap_left.isNull()
        self.anim_middle_valid = self.anim_movie_middle.isValid()

        if not self.anim_left_valid: logging.warning("Não foi possível carregar file.jpg")
        if not self.anim_middle_valid: logging.warning(f"Não foi possível carregar ou validar a animação: {self.anim_movie_middle_path}")

        if self.anim_left_valid:
            self.anim_label_left.setPixmap(self.anim_pixmap_left)
            self.anim_label_left.setScaledContents(True)

        if self.anim_middle_valid:
            self.anim_label_middle.setMovie(self.anim_movie_middle)

        self.opacity_effect_left = QGraphicsOpacityEffect(self.anim_label_left)
        self.opacity_effect_middle = QGraphicsOpacityEffect(self.anim_label_middle)
        self.anim_label_left.setGraphicsEffect(self.opacity_effect_left)
        self.anim_label_middle.setGraphicsEffect(self.opacity_effect_middle)

        self.opacity_effect_left.setOpacity(1.0)
        self.opacity_effect_middle.setOpacity(1.0)
        self.anim_label_left.setVisible(False)
        self.anim_label_middle.setVisible(False)
        self.anim_label_left.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.anim_label_middle.setAttribute(Qt.WA_TransparentForMouseEvents)


    def _create_layouts(self):
        main_layout = QVBoxLayout(self.central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        source_layout = QHBoxLayout()
        source_layout.setSpacing(8)
        source_layout.addWidget(self.source_label)
        source_layout.addWidget(self.source_lineedit, 1)
        source_layout.addWidget(self.source_button)
        main_layout.addLayout(source_layout)

        dest_layout = QHBoxLayout()
        dest_layout.setSpacing(8)
        dest_layout.addWidget(self.dest_label)
        dest_layout.addWidget(self.dest_lineedit, 1)
        main_layout.addLayout(dest_layout)

        main_layout.addSpacing(10)
        main_layout.addWidget(self.progress_bar)
        main_layout.addSpacing(15)

        info_layout = QGridLayout()
        info_layout.setHorizontalSpacing(20)
        info_layout.setVerticalSpacing(8)
        info_layout.addWidget(self.status_label_title, 0, 0, Qt.AlignRight)
        info_layout.addWidget(self.status_label_value, 0, 1, 1, 3)
        info_layout.addWidget(self.folder_label_title, 1, 0, Qt.AlignRight)
        info_layout.addWidget(self.folder_label_value, 1, 1, 1, 3)
        info_layout.addWidget(self.files_label_title, 2, 0, Qt.AlignRight)
        info_layout.addWidget(self.files_label_value, 2, 1)
        info_layout.addWidget(self.size_label_title, 2, 2, Qt.AlignRight)
        info_layout.addWidget(self.size_label_value, 2, 3)
        info_layout.addWidget(self.elapsed_label_title, 3, 0, Qt.AlignRight)
        info_layout.addWidget(self.elapsed_label_value, 3, 1)
        info_layout.addWidget(self.eta_label_title, 3, 2, Qt.AlignRight)
        info_layout.addWidget(self.eta_label_value, 3, 3)
        info_layout.addWidget(self.eta_image_label, 4, 2, 1, 2)
        info_layout.setColumnStretch(1, 1)
        info_layout.setColumnStretch(3, 1)
        main_layout.addLayout(info_layout)

        main_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.cancel_button)
        main_layout.addLayout(button_layout)

    def _connect_signals(self):
        self.source_button.clicked.connect(self._select_source_directory)
        self.start_button.clicked.connect(self._start_upload)
        self.cancel_button.clicked.connect(self._cancel_upload)

    def _select_source_directory(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Selecione a Pasta de Origem Local",
            self.source_path if os.path.isdir(self.source_path) else os.path.expanduser("~")
        )
        if path:
            self.source_path = path
            self.source_lineedit.setText(path)
            logging.info(f"Pasta de origem selecionada: {path}")

    def _update_elapsed_time_display(self):
        if self.upload_start_time:
            elapsed_seconds = time.time() - self.upload_start_time
            self.elapsed_label_value.setText(format_time(elapsed_seconds))
        else:
             self.elapsed_label_value.setText("00:00:00")

    def _start_upload(self):
        self.source_path = self.source_lineedit.text()
        self.destination_id = self.dest_lineedit.text().strip()

        if not self.source_path or not os.path.isdir(self.source_path):
            QMessageBox.warning(self, "Entrada Inválida", "Por favor, selecione uma pasta de origem válida.")
            return
        if not self.destination_id:
            QMessageBox.warning(self, "Entrada Inválida", "Por favor, insira o ID da pasta de destino do Google Drive.")
            return

        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.source_button.setEnabled(False)
        self.dest_lineedit.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label_value.setText("Iniciando...")
        self.folder_label_value.setText("-")
        self.files_label_value.setText("0 / 0")
        self.size_label_value.setText("0 B / 0 B")
        self.eta_label_value.setText("--:--:--")
        self.elapsed_label_value.setText("00:00:00")
        self.eta_image_label.setVisible(False)

        if self.anim_left_valid:
            self._position_and_start_animation()
        else:
            logging.warning("Pulando animação devido à falha no carregamento de file.jpg")
            self._execute_upload_logic()

    def _position_and_start_animation(self):
        win_width = self.central_widget.width()
        win_height = self.central_widget.height()
        img_width = max(50, win_width // 4)
        img_height = max(50, win_height // 2)

        left_x = win_width // 8
        left_y = (win_height - img_height) // 2
        self.anim_label_left.setGeometry(left_x, left_y, img_width, img_height)

        middle_x = (win_width - img_width) // 2
        middle_y = (win_height - img_height) // 2
        self.anim_label_middle.setGeometry(middle_x, middle_y, img_width, img_height)

        self.opacity_effect_left.setOpacity(1.0)
        self.opacity_effect_middle.setOpacity(1.0)
        self.anim_label_middle.setVisible(False)

        self.anim_label_left.setVisible(True)
        self.anim_label_left.raise_()

        QTimer.singleShot(500, self._show_middle_image)

    def _show_middle_image(self):
        if self.anim_middle_valid:
            self.anim_label_middle.setVisible(True)
            self.anim_label_middle.raise_()
            self.anim_movie_middle.start()
            QTimer.singleShot(10000, self._fade_out_images)
        else:
            logging.warning("Pulando para fade out (imagem do meio inválida)")
            QTimer.singleShot(10000, self._fade_out_images)

    def _fade_out_images(self):
        if self.anim_middle_valid:
            self.anim_movie_middle.stop()

        self.fade_group = QParallelAnimationGroup(self)
        fade_duration = 1000

        if self.anim_left_valid:
            opacity_anim_left = QPropertyAnimation(self.opacity_effect_left, b"opacity", self)
            opacity_anim_left.setDuration(fade_duration)
            opacity_anim_left.setStartValue(1.0)
            opacity_anim_left.setEndValue(0.0)
            opacity_anim_left.setEasingCurve(QEasingCurve.InOutQuad)
            self.fade_group.addAnimation(opacity_anim_left)

            start_geom_left = self.anim_label_left.geometry()
            center_left = start_geom_left.center()
            end_geom_left = QRect(center_left.x(), center_left.y(), 1, 1)
            scale_left = QPropertyAnimation(self.anim_label_left, b"geometry", self)
            scale_left.setDuration(fade_duration)
            scale_left.setStartValue(start_geom_left)
            scale_left.setEndValue(end_geom_left)
            scale_left.setEasingCurve(QEasingCurve.InOutQuad)
            self.fade_group.addAnimation(scale_left)


        if self.anim_middle_valid:
            opacity_anim_middle = QPropertyAnimation(self.opacity_effect_middle, b"opacity", self)
            opacity_anim_middle.setDuration(fade_duration)
            opacity_anim_middle.setStartValue(1.0)
            opacity_anim_middle.setEndValue(0.0)
            opacity_anim_middle.setEasingCurve(QEasingCurve.InOutQuad)
            self.fade_group.addAnimation(opacity_anim_middle)

            start_geom_middle = self.anim_label_middle.geometry()
            center_middle = start_geom_middle.center()
            end_geom_middle = QRect(center_middle.x(), center_middle.y(), 1, 1)
            scale_middle = QPropertyAnimation(self.anim_label_middle, b"geometry", self)
            scale_middle.setDuration(fade_duration)
            scale_middle.setStartValue(start_geom_middle)
            scale_middle.setEndValue(end_geom_middle)
            scale_middle.setEasingCurve(QEasingCurve.InOutQuad)
            self.fade_group.addAnimation(scale_middle)

        if self.fade_group.animationCount() == 0:
            logging.warning("Nenhuma animação válida para iniciar o fade out.")
            self._post_animation_actions()
            return

        self.fade_group.finished.connect(self._post_animation_actions)
        self.fade_group.start(QAbstractAnimation.DeleteWhenStopped)

    def _post_animation_actions(self):
        self.anim_label_left.setVisible(False)
        self.anim_label_middle.setVisible(False)
        if self.anim_middle_valid:
            self.anim_movie_middle.stop()
        self._execute_upload_logic()

    def _execute_upload_logic(self):
        self.status_label_value.setText("Autenticando...")
        try:
             if not self.uploader._get_drive_service():
                  raise ConnectionError("Falha ao obter o serviço do Google Drive.")

             self.worker = UploadWorker(self.uploader, self.source_path, self.destination_id)
             self.worker.scan_complete.connect(self._handle_scan_complete)
             self.worker.update_status.connect(self._handle_status_update)
             self.worker.update_current_folder.connect(self._handle_folder_update)
             self.worker.update_overall_progress.connect(self._handle_overall_progress)
             self.worker.finished.connect(self._handle_upload_finished)
             self.worker.error_occurred.connect(self._handle_critical_error)
             self.worker.start()
             logging.info("UploadWorker iniciado após animação.")

        except (FileNotFoundError, ConnectionAbortedError, ConnectionError, RuntimeError, Exception) as e:
             logging.error(f"Erro ao iniciar worker de upload: {e}", exc_info=True)
             QMessageBox.critical(self, "Erro ao Iniciar Upload", f"Não foi possível iniciar o processo de upload:\n{e}")
             self._set_ui_state_running(False)

    def _cancel_upload(self):
        logging.info("Botão Cancelar pressionado.")
        if hasattr(self, 'fade_group') and self.fade_group and self.fade_group.state() == QAbstractAnimation.Running:
            self.fade_group.stop()
            self.anim_label_left.setVisible(False)
            self.anim_label_middle.setVisible(False)
            if self.anim_middle_valid: self.anim_movie_middle.stop()
            logging.info("Animação de fade interrompida.")

        for timer in self.findChildren(QTimer):
            if timer is not self.elapsed_timer:
                timer.stop()

        if self.worker and self.worker.isRunning():
            self.status_label_value.setText("Cancelando Upload...")
            self.worker.stop()
            self.cancel_button.setEnabled(False)
        else:
            logging.warning("Cancelamento clicado sem upload ativo ou worker não encontrado.")
            self.status_label_value.setText("Cancelado.")
            self._set_ui_state_running(False)


    def _handle_scan_complete(self, total_files: int, total_bytes: int):
        logging.info(f"Slot _handle_scan_complete: Recebido Files={total_files}, Bytes={total_bytes} ({format_size(total_bytes)})")
        if total_files == -1:
            self.status_label_value.setText("Escaneamento cancelado.")
            self._set_ui_state_running(False)
            return

        self.total_files = total_files
        self.total_size_bytes = total_bytes
        self.files_label_value.setText(f"0 / {self.total_files}")
        self.size_label_value.setText(f"0 B / {format_size(self.total_size_bytes)}")

        if self.total_files > 0:
             self.progress_bar.setFormat("%p%")
             self.progress_bar.setValue(0)
             self.upload_start_time = time.time()
             self.elapsed_timer.start()
             if self.eta_image_valid:
                  self.eta_image_label.setVisible(True)
        else:
             self.progress_bar.setFormat("Nenhum arquivo para enviar")
             self._set_ui_state_running(False)


    def _handle_status_update(self, message: str):
        self.status_label_value.setText(message)


    def _handle_folder_update(self, folder_name: str):
        self.folder_label_value.setText(folder_name)


    def _handle_overall_progress(self, files_done: int, bytes_done: int):
        self.files_label_value.setText(f"{files_done} / {self.total_files}")
        self.size_label_value.setText(f"{format_size(bytes_done)} / {format_size(self.total_size_bytes)}")

        percentage = 0
        if self.total_size_bytes > 0:
            percentage = int((bytes_done / self.total_size_bytes) * 100)
        elif self.total_files > 0:
            percentage = int((files_done / self.total_files) * 100)
        self.progress_bar.setValue(percentage)

        eta_seconds = None
        if self.upload_start_time and bytes_done > 0:
            elapsed_time = time.time() - self.upload_start_time
            if elapsed_time > 2:
                try:
                    bytes_per_second = bytes_done / elapsed_time
                except ZeroDivisionError:
                    bytes_per_second = 0

                remaining_bytes = self.total_size_bytes - bytes_done

                if bytes_per_second > 0 and remaining_bytes > 0:
                    eta_seconds = remaining_bytes / bytes_per_second
                elif remaining_bytes <= 0:
                     eta_seconds = 0

        formatted_eta = format_time(eta_seconds)
        self.eta_label_value.setText(formatted_eta)


    def _handle_upload_finished(self, success: bool, files_uploaded: int, bytes_uploaded: int):
        logging.info(f"Slot _handle_upload_finished: Sucesso={success}, Arquivos={files_uploaded}, Bytes={bytes_uploaded}")
        self.elapsed_timer.stop()
        final_elapsed = time.time() - self.upload_start_time if self.upload_start_time else 0
        self.elapsed_label_value.setText(format_time(final_elapsed))

        if success:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("Completo!")
            self.eta_label_value.setText("00:00:00")
            self.files_label_value.setText(f"{files_uploaded} / {self.total_files}")
            self.size_label_value.setText(f"{format_size(bytes_uploaded)} / {format_size(self.total_size_bytes)}")
            self.status_label_value.setText("Upload concluído com sucesso!")
            notification = ElegantNotification("Upload concluído com sucesso!", self.central_widget)
            notification.show_notification()
        else:
            was_cancelled = self.worker is not None and not self.worker._is_running
            if was_cancelled:
                 self.status_label_value.setText("Upload cancelado pelo usuário.")
                 self.progress_bar.setFormat("Cancelado")
            else:
                 if self.status_label_value.text() != "Erro crítico.":
                     self.status_label_value.setText("Upload finalizado com erros.")
                     self.progress_bar.setFormat("Erro")
                     notification = ElegantNotification("Upload finalizado com erros. Verifique os logs.", self.central_widget)
                     notification.show_notification()

        self._set_ui_state_running(False)


    def _handle_critical_error(self, error_message: str):
         logging.error(f"Erro crítico recebido pela GUI: {error_message}")
         if self.elapsed_timer.isActive():
             self.elapsed_timer.stop()
         QMessageBox.critical(self, "Erro Crítico", f"Ocorreu um erro que impediu a continuação:\n\n{error_message}\n\nVerifique o arquivo '{LOG_FILENAME}' para mais detalhes.")
         self.status_label_value.setText("Erro crítico.")
         self.progress_bar.setFormat("Erro")
         self._set_ui_state_running(False)

    def _set_ui_state_running(self, is_running: bool):
        self.start_button.setEnabled(not is_running)
        self.cancel_button.setEnabled(is_running)
        self.source_button.setEnabled(not is_running)
        self.dest_lineedit.setEnabled(not is_running)

        self.anim_label_left.setVisible(False)
        self.anim_label_middle.setVisible(False)
        if self.anim_middle_valid:
            self.anim_movie_middle.stop()

        if not is_running:
            self.worker = None
            self.upload_start_time = None
            self.folder_label_value.setText("-")
            self.eta_label_value.setText("--:--:--")
            self.elapsed_label_value.setText("00:00:00")
            self.eta_image_label.setVisible(False)
            final_status_texts = ["Erro crítico.", "Upload cancelado pelo usuário.", "Upload finalizado com erros.", "Upload concluído com sucesso!", "Cancelado."]
            final_progress_formats = ["Erro", "Cancelado", "Completo!", "Nenhum arquivo para enviar"]
            if self.status_label_value.text() not in final_status_texts:
                 self.status_label_value.setText("Pronto")
            if self.progress_bar.format() not in final_progress_formats:
                 self.progress_bar.setFormat("Pronto")
                 self.progress_bar.setValue(0)


    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
             reply = QMessageBox.question(self, 'Upload em Progresso',
                                          "Um upload está em andamento. Deseja cancelar e sair?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
             if reply == QMessageBox.StandardButton.Yes:
                 logging.info("Usuário confirmou saída durante upload. Cancelando...")
                 self._cancel_upload()
                 event.accept()
             else:
                 logging.info("Usuário cancelou a saída.")
                 event.ignore()
        else:
             if hasattr(self, 'fade_group') and self.fade_group and self.fade_group.state() == QAbstractAnimation.Running:
                self.fade_group.stop()
             for timer in self.findChildren(QTimer):
                 timer.stop()
             if self.anim_middle_valid: self.anim_movie_middle.stop()

             logging.info("Fechando aplicação.")
             event.accept()

if __name__ == '__main__':
    if not GUI_AVAILABLE:
        print("ERRO CRÍTICO: PySide6 não está instalado ou não pôde ser importado.")
        sys.exit(1)

    if not os.path.exists(CLIENT_SECRETS_FILE):
         logging.critical(f"Arquivo de credenciais '{CLIENT_SECRETS_FILE}' não encontrado.")
         temp_app_check = QApplication.instance()
         if temp_app_check is None: temp_app_check = QApplication(sys.argv)
         QMessageBox.critical(None, "Erro de Configuração",
                              f"Arquivo de credenciais '{CLIENT_SECRETS_FILE}' não encontrado.\n\n"
                              "Faça o download do arquivo JSON de credenciais OAuth 2.0 (para Aplicação Desktop) "
                              "do seu projeto no Google Cloud Console e salve-o como "
                              f"'{CLIENT_SECRETS_FILE}' no mesmo diretório deste aplicativo.\n\n"
                              "A aplicação será encerrada.")
         sys.exit(1)

    app = QApplication(sys.argv)

    main_window = UploadWindow()
    main_window.show()

    sys.exit(app.exec())