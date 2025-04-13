# drive_uploader.py
import os
import sys
import datetime
import json
import logging
import time
import math
import mimetypes
import io
from typing import List, Dict, Optional

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
        self.current_creds = None
        self.folder_cache = {}
        self._stop_requested = False
        self.folder_summary_cache = {}

    def request_stop(self):
        log.info("Solicitação de parada recebida pelo uploader.")
        self._stop_requested = True

    def reset_stop_request(self):
         self._stop_requested = False

    def clear_folder_summary_cache(self):
        log.info("Limpando cache de sumário de pastas.")
        self.folder_summary_cache.clear()

    def _get_drive_service(self):
        creds = None
        valid_creds_obtained = False

        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'r') as token:
                    creds_data = json.load(token)
                    if all(k in creds_data for k in ('token', 'refresh_token', 'token_uri', 'client_id', 'client_secret', 'scopes')):
                        creds = Credentials(**creds_data)
                        log.info("Credenciais carregadas do arquivo.")
                    else:
                        log.warning(f"Arquivo {self.token_file} incompleto.")
            except (json.JSONDecodeError, FileNotFoundError, TypeError) as e:
                log.warning(f"Erro ao ler {self.token_file} ({e}).")

        if creds:
            required_scopes = set(self.scopes)
            saved_scopes = set(creds.scopes)
            if not required_scopes.issubset(saved_scopes):
                 log.warning(f"Escopos salvos insuficientes. Requer: {required_scopes}, Salvo: {saved_scopes}. Reautenticação.")
                 creds = None
            elif creds.expired and creds.refresh_token:
                log.info("Credenciais expiradas, tentando atualizar...")
                try:
                    creds.refresh(Request())
                    log.info("Token atualizado com sucesso.")
                    refreshed_scopes = set(creds.scopes)
                    if not required_scopes.issubset(refreshed_scopes):
                         log.warning(f"Token atualizado, mas escopos ainda insuficientes. Reautenticação manual.")
                         creds = None
                    else:
                         valid_creds_obtained = True
                except Exception as e:
                    log.error(f"Erro ao atualizar token: {e}. Reautenticação manual.")
                    creds = None
            elif creds.valid:
                log.info("Credenciais carregadas válidas.")
                valid_creds_obtained = True
            else:
                log.warning("Credenciais carregadas inválidas.")
                creds = None

        if not valid_creds_obtained:
             log.info("Iniciando fluxo de autenticação via web...")
             if not os.path.exists(self.client_secrets_file):
                 log.fatal(f"Arquivo '{self.client_secrets_file}' não encontrado.")
                 raise FileNotFoundError(f"'{self.client_secrets_file}' não encontrado.")
             flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, self.scopes)
             try:
                 creds = flow.run_local_server(port=0)
                 valid_creds_obtained = True
             except Exception as e:
                 log.fatal(f"Falha no fluxo de autenticação: {e}")
                 raise ConnectionAbortedError(f"Falha no fluxo: {e}") from e

             if creds:
                 log.info(f"Salvando novas credenciais em {self.token_file}")
                 creds_data_to_save = {'token': creds.token,'refresh_token': creds.refresh_token,'token_uri': creds.token_uri,'client_id': creds.client_id,'client_secret': creds.client_secret,'scopes': creds.scopes}
                 try:
                     with open(self.token_file, 'w') as token_file_handle: json.dump(creds_data_to_save, token_file_handle)
                 except IOError as e:
                     log.error(f"Erro ao salvar token em {self.token_file}: {e}")

        if valid_creds_obtained and creds:
            try:
                if self.service is None or self.current_creds != creds:
                     log.info("Construindo/Reconstruindo o serviço do Google Drive...")
                     self.service = build('drive', 'v3', credentials=creds)
                     self.current_creds = creds
                     log.info("Serviço do Google Drive construído.")
                return self.service
            except HttpError as error:
                log.fatal(f'Erro ao construir/conectar serviço: {error}')
                self.service = None; self.current_creds = None
                raise ConnectionError(f'Erro ao construir/conectar serviço: {error}') from error
            except Exception as e:
                log.fatal(f'Erro inesperado ao construir serviço: {e}')
                self.service = None; self.current_creds = None
                raise RuntimeError(f'Erro inesperado ao construir serviço: {e}') from e
        else:
             log.fatal("Não foi possível obter credenciais válidas.")
             raise ConnectionError("Não foi possível obter credenciais válidas.")

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
                      log.info("Scan cancelado pelo usuário durante walk.")
                      raise InterruptedError("Scan cancelled")

                 for f in filenames:
                    try:
                        fp = os.path.join(root, f)
                        if os.path.isfile(fp):
                            total_files += 1
                            total_size += os.path.getsize(fp)
                    except OSError as e:
                        log.warning(f"Não foi possível processar/obter tamanho de {fp}: {e}")
                    except Exception as e_inner:
                        log.warning(f"Erro inesperado ao processar arquivo {f}: {e_inner}")

            log.info(f"Scan completo: {total_files} arquivos, {total_size} bytes.")
            return total_files, total_size

        except InterruptedError: 
             log.info("Scan cancelado.")
             return -1, -1
        except Exception as e_outer: 
            log.error(f"Erro durante scan do diretório {local_path}: {e_outer}", exc_info=True)
            return 0, 0
        
    def get_or_create_drive_folder(self, parent_id, folder_name):
        if not self._get_drive_service(): raise ConnectionError("Serviço Drive não disponível")
        cache_key = f"{parent_id}///{folder_name}"
        if cache_key in self.folder_cache: return self.folder_cache[cache_key]
        log.info(f"Procurando/Criando pasta '{folder_name}' em '{parent_id}'")
        try:
            safe_folder_name = folder_name.replace("'", "\\'"); query = f"'{parent_id}' in parents and name='{safe_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            response = self.service.files().list(q=query, spaces='drive', fields='files(id)', pageSize=1).execute(); files = response.get('files', [])
            if files: folder_id = files[0].get('id'); log.info(f"Pasta '{folder_name}' encontrada: {folder_id}"); self.folder_cache[cache_key] = folder_id; return folder_id
            else:
                log.info(f"Pasta '{folder_name}' não encontrada. Criando..."); file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
                folder = self.service.files().create(body=file_metadata, fields='id').execute(); folder_id = folder.get('id'); log.info(f"Pasta '{folder_name}' criada: {folder_id}"); self.folder_cache[cache_key] = folder_id; return folder_id
        except HttpError as error: log.error(f"Erro Http ao buscar/criar pasta '{folder_name}': {error}"); raise error
        except Exception as e: log.error(f"Erro inesperado get/create '{folder_name}': {e}", exc_info=True); raise e

    def _get_mimetype(self, filepath):
        mimetype, _ = mimetypes.guess_type(filepath); return mimetype if mimetype else 'application/octet-stream'

    def upload_file(self, local_path, parent_id, progress_callback=None, status_callback=None):
        if self._stop_requested: raise InterruptedError("Upload cancelado")
        if not self._get_drive_service(): raise ConnectionError("Serviço Drive não disponível")
        file_size = 0; file_name = os.path.basename(local_path)
        try:
            if os.path.isfile(local_path): file_size = os.path.getsize(local_path)
            else: raise FileNotFoundError(f"Arquivo local não encontrado: {local_path}")
            if status_callback: status_callback(f"Enviando: {file_name} ({self.format_size(file_size)})")
            log.info(f"Upload: {file_name} ({self.format_size(file_size)}) para ID: {parent_id}")
            stat_info = os.stat(local_path); mod_time_dt = datetime.datetime.fromtimestamp(stat_info.st_mtime, tz=datetime.timezone.utc)
            try: create_time_ts = getattr(stat_info, 'st_birthtime', stat_info.st_ctime); create_time_dt = datetime.datetime.fromtimestamp(create_time_ts, tz=datetime.timezone.utc)
            except AttributeError: create_time_dt = mod_time_dt
            create_time_iso = create_time_dt.isoformat(); mod_time_iso = mod_time_dt.isoformat()
            file_metadata = {'name': file_name, 'parents': [parent_id], 'modifiedTime': mod_time_iso, 'createdTime': create_time_iso }
            media = MediaFileUpload(local_path, mimetype=self._get_mimetype(local_path), resumable=True)
            request = self.service.files().create(body=file_metadata, media_body=media, fields='id, name')
            response = None; last_progress = -1
            while response is None:
                if self._stop_requested: raise InterruptedError("Cancelado durante chunk")
                try:
                    status, response = request.next_chunk()
                    if status:
                        current_progress = int(status.progress() * 100)
                        if current_progress > last_progress :
                           if progress_callback: progress_callback(file_name, current_progress, file_size)
                           last_progress = current_progress
                except HttpError as e: log.error(f"Erro Http chunk '{file_name}': {e}"); raise e
                except Exception as e: log.error(f"Erro chunk '{file_name}': {e}"); raise e
            log.info(f"OK: '{response.get('name')}' ID: {response.get('id')}")
            if progress_callback and last_progress < 100: progress_callback(file_name, 100, file_size)
            return True, file_size
        except InterruptedError: log.info(f"Cancelado para {file_name}"); raise InterruptedError(f"Cancelado para {file_name}")
        except HttpError as error:
            if error.resp.status == 409:
                log.warning(f"Conflito 409 - '{file_name}' já existe.")
                if progress_callback:
                    progress_callback(file_name, 100, file_size)
                return True, file_size
            else: log.error(f"Erro Http {error.resp.status} upload '{file_name}': {error}"); raise error
        except FileNotFoundError as e: log.error(f"Erro: Arquivo não encontrado: {e}"); raise e
        except Exception as e: log.error(f"Erro upload '{local_path}': {e}", exc_info=True); raise e

    def upload_directory(self, local_source_path, dest_folder_id, progress_callback=None, status_callback=None, current_folder_callback=None):
        total_files_uploaded = 0; total_bytes_uploaded = 0
        if not self._get_drive_service(): raise ConnectionError("Serviço Drive não disponível")
        drive_folder_ids = {os.path.abspath(local_source_path): dest_folder_id}; self.folder_cache.clear()
        try:
            log.info(f"Iniciando percurso upload em: {local_source_path}")
            for local_root, dir_names, file_names in os.walk(local_source_path):
                if self._stop_requested: raise InterruptedError("Upload cancelado")
                abs_local_root = os.path.abspath(local_root); relative_path = os.path.relpath(local_root, local_source_path); display_path = os.path.basename(local_source_path) if relative_path == '.' else relative_path
                if current_folder_callback: current_folder_callback(display_path)
                log.info(f"Processando UPLOAD: {display_path}")
                parent_drive_id = drive_folder_ids.get(abs_local_root)
                if not parent_drive_id: log.error(f"Erro Lógico: ID pai não encontrado para {abs_local_root}"); dir_names[:] = []; continue
                dir_names_copy = sorted(list(dir_names))
                for dir_name in dir_names_copy:
                     if self._stop_requested: raise InterruptedError("Cancelado subdir")
                     local_dir_path = os.path.join(local_root, dir_name); abs_local_dir_path = os.path.abspath(local_dir_path)
                     drive_folder_id = self.get_or_create_drive_folder(parent_drive_id, dir_name)
                     if drive_folder_id: drive_folder_ids[abs_local_dir_path] = drive_folder_id
                     else:
                         log.warning(f"Falha get/create ID para '{dir_name}'. Pulando.")
                         try:
                             dir_names.remove(dir_name)
                         except ValueError:
                             pass
                for file_name in sorted(file_names):
                    if self._stop_requested: raise InterruptedError("Cancelado file")
                    local_file_path = os.path.join(local_root, file_name)
                    try: file_success, file_bytes = self.upload_file(local_file_path, parent_drive_id, progress_callback, status_callback)
                    except Exception as upload_err: log.error(f"Falha no upload de {local_file_path}: {upload_err}"); file_success, file_bytes = False, 0
                    if file_success: total_files_uploaded += 1; total_bytes_uploaded += file_bytes
            log.info("Percurso upload completo.")
            return True, total_files_uploaded, total_bytes_uploaded
        except InterruptedError:
            log.info("Processo de upload interrompido.")
            if status_callback:
                status_callback("Upload cancelado.")
            return False, total_files_uploaded, total_bytes_uploaded
        except Exception as e: log.error(f"Erro inesperado upload_directory: {e}", exc_info=True); 
        if status_callback: status_callback(f"Erro fatal: {e}"); return False, total_files_uploaded, total_bytes_uploaded

    def list_folders(self, parent_id: str = "root") -> list[dict]:
        if not self._get_drive_service(): raise ConnectionError("Serviço Drive não disponível")
        all_folders = []; page_token = None
        try:
            while True:
                if self._stop_requested: raise InterruptedError("Listagem cancelada.")
                q = f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                results = self.service.files().list(q=q, pageSize=200, spaces='drive', fields='nextPageToken, files(id, name)', pageToken=page_token, orderBy='name').execute()
                items = results.get('files', []); all_folders.extend({'id': item['id'], 'name': item['name']} for item in items)
                page_token = results.get('nextPageToken', None)
                if page_token is None: break
            log.info(f"Encontradas {len(all_folders)} pastas em '{parent_id}'")
            return all_folders
        except HttpError as error: log.error(f"Erro Http listar pastas '{parent_id}': {error}"); raise error
        except InterruptedError as e: log.info(f"Listagem interrompida '{parent_id}'"); raise e
        except Exception as e: log.error(f"Erro inesperado listar pastas: {e}", exc_info=True); raise e

    def get_folder_contents_summary(self, folder_id: str) -> dict:
        if folder_id in self.folder_summary_cache: log.info(f"Cache HIT sumário '{folder_id}'"); return self.folder_summary_cache[folder_id]
        if not self._get_drive_service(): raise ConnectionError("Serviço Drive não disponível")
        file_count = 0; folder_count = 0; direct_size = 0; page_token = None
        try:
            log.debug(f"Buscando sumário para folder_id='{folder_id}'")
            while True:
                if self._stop_requested: raise InterruptedError("Sumário cancelado.")
                q = f"'{folder_id}' in parents and trashed = false"
                results = self.service.files().list(q=q, pageSize=1000, spaces='drive', fields='nextPageToken, files(id, mimeType, size)', pageToken=page_token).execute()
                items = results.get('files', [])
                for item in items:
                    mime_type = item.get('mimeType')
                    if mime_type == 'application/vnd.google-apps.folder': folder_count += 1
                    else:
                        file_count += 1
                        try:
                            size = int(item.get('size', 0))
                            direct_size += size
                        except (ValueError, TypeError): log.warning(f"Tamanho inválido {item.get('id')}: {item.get('size')}")
                page_token = results.get('nextPageToken', None)
                if page_token is None: break
            summary = {'folder_count': folder_count, 'file_count': file_count, 'direct_size': direct_size}
            log.info(f"Sumário calculado para '{folder_id}': {summary}")
            self.folder_summary_cache[folder_id] = summary
            return summary
        except HttpError as error: log.error(f"Erro Http obter sumário '{folder_id}': {error}"); raise error
        except InterruptedError as e: log.info(f"Busca sumário interrompida '{folder_id}'"); raise e
        except Exception as e: log.error(f"Erro inesperado obter sumário: {e}", exc_info=True); raise e

    def create_folder(self, parent_id: str, folder_name: str) -> Optional[Dict[str, str]]:
        if not self._get_drive_service(): raise ConnectionError("Serviço Drive não disponível")
        log.info(f"Criando pasta '{folder_name}' dentro de '{parent_id}'")
        file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
        try:
            folder = self.service.files().create(body=file_metadata, fields='id, name').execute()
            new_id = folder.get('id'); new_name = folder.get('name')
            log.info(f"Pasta '{new_name}' criada com ID: {new_id}")
            if parent_id in self.folder_summary_cache: del self.folder_summary_cache[parent_id]
            return {'id': new_id, 'name': new_name} if new_id and new_name else None
        except HttpError as error: log.error(f"Erro Http ao criar pasta '{folder_name}': {error}"); raise error
        except Exception as e: log.error(f"Erro inesperado ao criar pasta: {e}", exc_info=True); raise e

    def delete_folder(self, folder_id: str) -> bool:
        if not self._get_drive_service(): raise ConnectionError("Serviço Drive não disponível")
        log.info(f"Deletando (lixeira) pasta ID: {folder_id}")
        try:
            self.service.files().delete(fileId=folder_id).execute()
            if folder_id in self.folder_summary_cache: del self.folder_summary_cache[folder_id]
            # Remover do cache de get_or_create também? Percorrer valores...
            keys_to_delete = [key for key, value in self.folder_cache.items() if value == folder_id]
            for key in keys_to_delete: del self.folder_cache[key]
            log.info(f"Pasta ID: {folder_id} movida para lixeira.")
            return True
        except HttpError as error: log.error(f"Erro Http ao deletar pasta ID '{folder_id}': {error}"); raise error
        except Exception as e: log.error(f"Erro inesperado ao deletar pasta: {e}", exc_info=True); raise e

    def format_size(self, size_bytes):
        if size_bytes < 0: return "0 B"
        if size_bytes == 0: return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        if size_bytes < 1: return "0 B"
        try: i = int(math.floor(math.log(size_bytes, 1024)))
        except ValueError: i = 0
        i = min(i, len(size_name) - 1)
        p = math.pow(1024, i)
        try: s = round(size_bytes / p, 2)
        except ZeroDivisionError: s = 0
        return f"{s} {size_name[i]}"