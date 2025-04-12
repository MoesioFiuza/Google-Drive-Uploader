# drive_uploader.py
import os
import sys
import datetime
import json
import logging
import time
import math
import mimetypes

# --- Google API Imports ---
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

CLIENT_SECRETS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'
SCOPES = ['https://www.googleapis.com/auth/drive']

log = logging.getLogger(__name__)

class DriveUploader:
    def __init__(self, client_secrets_file=CLIENT_SECRETS_FILE, token_file=TOKEN_FILE, scopes=SCOPES):
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self.scopes = scopes
        self.service = None
        self.folder_cache = {}
        self._stop_requested = False

    def request_stop(self):
        log.info("Solicitação de parada recebida pelo uploader.")
        self._stop_requested = True

    def reset_stop_request(self):
         self._stop_requested = False

    def _get_drive_service(self):
        if self.service and self.service.credentials and not self.service.credentials.expired:
           return self.service

        creds = None
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'r') as token:
                    creds_data = json.load(token)
                    if all(k in creds_data for k in ('token', 'refresh_token', 'token_uri', 'client_id', 'client_secret', 'scopes')):
                         creds = Credentials(
                             token=creds_data.get('token'),
                             refresh_token=creds_data.get('refresh_token'),
                             token_uri=creds_data.get('token_uri'),
                             client_id=creds_data.get('client_id'),
                             client_secret=creds_data.get('client_secret'),
                             scopes=creds_data.get('scopes')
                         )
                         log.info("Credenciais carregadas do arquivo.")
                    else:
                         log.warning(f"Arquivo {self.token_file} incompleto. Reautenticação necessária.")
            except (json.JSONDecodeError, KeyError, FileNotFoundError, TypeError) as e:
                log.warning(f"Erro ao carregar {self.token_file} ({e}), será necessário reautenticar.")
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                log.info("Credenciais expiradas, tentando atualizar...")
                try:
                    creds.refresh(Request())
                except Exception as e:
                    log.error(f"Erro ao atualizar token: {e}. Requer autenticação manual.")
                    creds = None
                else:
                    log.info("Token atualizado com sucesso.")
            else: 
                 log.info("Necessário fluxo de autenticação.")
                 creds = None 

            if not creds or not creds.valid:
                 log.info("Iniciando fluxo de autenticação...")
                 if not os.path.exists(self.client_secrets_file):
                     log.fatal(f"Arquivo de credenciais '{self.client_secrets_file}' não encontrado.")
                     raise FileNotFoundError(f"Arquivo de credenciais '{self.client_secrets_file}' não encontrado.")

                 flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, self.scopes)
                 try:
                     creds = flow.run_local_server(port=0)
                 except Exception as e:
                     log.fatal(f"Falha ao executar fluxo de autenticação: {e}")
                     raise ConnectionAbortedError(f"Falha ao executar fluxo de autenticação: {e}") from e

            if creds:
                 log.info(f"Salvando credenciais em {self.token_file}")
                 creds_data_to_save = {
                     'token': creds.token,
                     'refresh_token': creds.refresh_token,
                     'token_uri': creds.token_uri,
                     'client_id': creds.client_id,
                     'client_secret': creds.client_secret,
                     'scopes': creds.scopes
                 }
                 try:
                     with open(self.token_file, 'w') as token_file_handle:
                         json.dump(creds_data_to_save, token_file_handle)
                 except IOError as e:
                     log.error(f"Erro ao salvar token em {self.token_file}: {e}")

        try:
            self.service = build('drive', 'v3', credentials=creds)
            log.info("Serviço do Google Drive criado com sucesso.")
            return self.service
        except HttpError as error:
            log.fatal(f'Ocorreu um erro ao conectar ao Google Drive: {error}')
            raise ConnectionError(f'Ocorreu um erro ao conectar ao Google Drive: {error}') from error
        except Exception as e:
            log.fatal(f'Ocorreu um erro inesperado ao criar o serviço: {e}')
            raise RuntimeError(f'Ocorreu um erro inesperado ao criar o serviço: {e}') from e

    def scan_local_directory(self, local_path):
        total_files = 0
        total_size = 0
        if not os.path.isdir(local_path):
            log.error(f"Scan error: '{local_path}' não é um diretório válido.")
            return 0, 0

        log.info(f"Scanning directory: {local_path}...")
        try:
            for root, _, filenames in os.walk(local_path):
                 if self._stop_requested:
                      log.info("Scan cancelado pelo usuário.")
                      raise InterruptedError("Scan cancelled")
                 for f in filenames:
                    try:
                        fp = os.path.join(root, f)
                        if os.path.isfile(fp):
                            total_files += 1
                            total_size += os.path.getsize(fp)
                    except OSError as e:
                        log.warning(f"Não foi possível obter o tamanho de {fp}: {e}")
            log.info(f"Scan completo: {total_files} arquivos, {total_size} bytes.")
            return total_files, total_size
        except InterruptedError:
             return -1, -1
        except Exception as e:
            log.error(f"Erro durante scan do diretório {local_path}: {e}", exc_info=True)
            return 0, 0 
    def get_or_create_drive_folder(self, parent_id, folder_name):
        if not self.service:
            if not self._get_drive_service():
                 log.error("Serviço do Drive não disponível para get_or_create_drive_folder.")
                 return None 

        cache_key = f"{parent_id}///{folder_name}"
        if cache_key in self.folder_cache:
            log.debug(f"Cache HIT para pasta: '{folder_name}' -> ID: {self.folder_cache[cache_key]}")
            return self.folder_cache[cache_key]

        log.info(f"Procurando/Criando pasta '{folder_name}' em Drive ID '{parent_id}'")
        try:
            safe_folder_name = folder_name.replace("'", "\\'")
            query = f"'{parent_id}' in parents and name='{safe_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            response = self.service.files().list(q=query, spaces='drive', fields='files(id)', pageSize=1).execute()
            files = response.get('files', [])

            if files:
                folder_id = files[0].get('id')
                log.info(f"Pasta '{folder_name}' encontrada com ID: {folder_id}")
                self.folder_cache[cache_key] = folder_id
                return folder_id
            else:
                log.info(f"Pasta '{folder_name}' não encontrada. Criando...")
                file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
                folder = self.service.files().create(body=file_metadata, fields='id').execute()
                folder_id = folder.get('id')
                log.info(f"Pasta '{folder_name}' criada com ID: {folder_id}")
                self.folder_cache[cache_key] = folder_id
                return folder_id
        except HttpError as error:
            log.error(f"Erro (HttpError) ao buscar/criar pasta '{folder_name}': {error}")
            return None
        except Exception as e:
            log.error(f"Erro inesperado em get_or_create_drive_folder para '{folder_name}': {e}", exc_info=True)
            return None

    def _get_mimetype(self, filepath):
        mimetype, _ = mimetypes.guess_type(filepath)
        return mimetype if mimetype else 'application/octet-stream'

    def upload_file(self, local_path, parent_id, progress_callback=None, status_callback=None):
        if self._stop_requested: raise InterruptedError("Upload cancelled by user")
        if not self.service:
            if not self._get_drive_service():
                 log.error("Serviço do Drive não disponível para upload_file.")
                 return False, 0

        file_size = 0
        file_name = os.path.basename(local_path)
        try:
            if os.path.isfile(local_path):
                file_size = os.path.getsize(local_path)
            else:
                 raise FileNotFoundError(f"Arquivo local não encontrado ou não é um arquivo: {local_path}")
            if status_callback: status_callback(f"Enviando: {file_name} ({self.format_size(file_size)})")
            log.info(f"Iniciando upload de: {file_name} ({self.format_size(file_size)}) para Drive ID: {parent_id}")

            stat_info = os.stat(local_path)
            mod_time_dt = datetime.datetime.fromtimestamp(stat_info.st_mtime, tz=datetime.timezone.utc)
            try:
                create_time_ts = getattr(stat_info, 'st_birthtime', stat_info.st_ctime)
                create_time_dt = datetime.datetime.fromtimestamp(create_time_ts, tz=datetime.timezone.utc)
            except AttributeError:
                 create_time_dt = mod_time_dt 
            create_time_iso = create_time_dt.isoformat()
            mod_time_iso = mod_time_dt.isoformat()


            file_metadata = {
                'name': file_name,
                'parents': [parent_id],
                'modifiedTime': mod_time_iso,
                'createdTime': create_time_iso
            }
            media = MediaFileUpload(local_path, mimetype=self._get_mimetype(local_path), resumable=True)

            request = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name'
            )

            response = None
            last_progress = -1
            while response is None:
                if self._stop_requested: raise InterruptedError("Upload cancelled by user during chunk upload")
                try:
                    status, response = request.next_chunk()
                    if status:
                        current_progress = int(status.progress() * 100)
                        if current_progress > last_progress :
                           log.debug(f"Progresso '{file_name}': {current_progress}%")
                           if progress_callback:
                               progress_callback(file_name, current_progress, file_size)
                           last_progress = current_progress
                except HttpError as e:
                    log.error(f"Erro (HttpError) durante upload de chunk para '{file_name}': {e}")
                    if status_callback: status_callback(f"Erro no upload de '{file_name}': {e}")
                    return False, file_size
                except Exception as e:
                    log.error(f"Erro inesperado durante upload de chunk para '{file_name}': {e}", exc_info=True)
                    return False, file_size

            log.info(f"Arquivo '{response.get('name')}' enviado com sucesso! Drive ID: {response.get('id')}")
            if progress_callback and last_progress < 100:
                 progress_callback(file_name, 100, file_size)
            return True, file_size

        except InterruptedError:
             log.info(f"Upload explicitamente cancelado para {file_name}")
             if status_callback: status_callback(f"Upload cancelado para {file_name}")
             return False, file_size
        except HttpError as error:
            if error.resp.status == 409:
                warn_msg = f"Aviso: Conflito (409) - '{file_name}' já existe no destino."
                log.warning(warn_msg)
                if status_callback: status_callback(warn_msg)
                if progress_callback: progress_callback(file_name, 100, file_size)
                return True, file_size 
            else:
                error_msg = f"Erro HTTP {error.resp.status} no upload de '{file_name}': {error}"
                log.error(error_msg)
                if status_callback: status_callback(error_msg)
                return False, file_size
        except FileNotFoundError as e:
             error_msg = f"Erro: Arquivo local não encontrado: {e}"
             log.error(error_msg)
             if status_callback: status_callback(error_msg)
             return False, 0 
        except Exception as e:
            error_msg = f"Erro inesperado em upload_file para '{local_path}': {e}"
            log.error(error_msg, exc_info=True)
            if status_callback: status_callback(error_msg)
            return False, file_size if file_size > 0 else 0

    def upload_directory(self, local_source_path, dest_folder_id, progress_callback=None, status_callback=None, current_folder_callback=None):
        total_files_uploaded = 0
        total_bytes_uploaded = 0

        if not self.service:
            if not self._get_drive_service():
                log.error("Serviço do Drive não disponível para upload_directory.")
                return False, 0, 0 

        drive_folder_ids = {os.path.abspath(local_source_path): dest_folder_id}
        self.folder_cache.clear() 

        try:
            log.info(f"Iniciando percurso em: {local_source_path}")
            for local_root, dir_names, file_names in os.walk(local_source_path):
                if self._stop_requested: raise InterruptedError("Upload cancelled during directory walk")

                abs_local_root = os.path.abspath(local_root)
                relative_path = os.path.relpath(local_root, local_source_path)
                display_path = os.path.basename(local_source_path) if relative_path == '.' else relative_path

                if current_folder_callback: current_folder_callback(display_path)
                log.info(f"Processando pasta local: {local_root} (Relativo: {display_path})")

                parent_drive_id = drive_folder_ids.get(abs_local_root)
                if not parent_drive_id:
                    log.error(f"ID do Drive não encontrado para o diretório pai local: {abs_local_root}. Pulando conteúdo.")
                    dir_names[:] = []
                    continue
                for dir_name in sorted(dir_names):
                     if self._stop_requested: raise InterruptedError("Upload cancelled during subdirectory processing")
                     local_dir_path = os.path.join(local_root, dir_name)
                     abs_local_dir_path = os.path.abspath(local_dir_path)
                     drive_folder_id = self.get_or_create_drive_folder(parent_drive_id, dir_name)
                     if drive_folder_id:
                         drive_folder_ids[abs_local_dir_path] = drive_folder_id
                     else:
                         log.warning(f"Falha ao obter/criar ID do Drive para subpasta '{dir_name}'. Seu conteúdo será pulado.")
                         try:
                             dir_names.remove(dir_name)
                         except ValueError:
                              pass 

                for file_name in sorted(file_names):
                    if self._stop_requested: raise InterruptedError("Upload Cancelado por deu Fumo")
                    local_file_path = os.path.join(local_root, file_name)
                    file_success, file_bytes = self.upload_file(
                        local_file_path,
                        parent_drive_id,
                        progress_callback, 
                        status_callback    
                    )
                    if file_success:
                         total_files_uploaded += 1
                         total_bytes_uploaded += file_bytes
            log.info("Percurso completo do diretório local.")
            return True, total_files_uploaded, total_bytes_uploaded

        except InterruptedError:
             log.info("Processo de upload interrompido por solicitação.")
             if status_callback: status_callback("Upload cancelado.")
             return False, total_files_uploaded, total_bytes_uploaded
        except Exception as e:
             log.error(f"Erro inesperado durante upload_directory: {e}", exc_info=True)
             if status_callback: status_callback(f"Erro fatal no upload: {e}")
             return False, total_files_uploaded, total_bytes_uploaded

    def format_size(self, size_bytes):
        if size_bytes < 0: return "0 B"
        if size_bytes == 0: return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        if size_bytes < 1: return "0 B"
        i = int(math.floor(math.log(size_bytes, 1024)))
        i = min(i, len(size_name) - 1)
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"