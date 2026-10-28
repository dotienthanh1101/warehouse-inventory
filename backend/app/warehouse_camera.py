"""Per-warehouse camera state. AI/capture concurrency remains bounded separately."""
import ipaddress
import threading
import time
from datetime import datetime,timezone
from urllib.parse import urlsplit
from fastapi import HTTPException
from backend.app.warehouse_scope import current_warehouse_id

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))

def validate_camera_address(public_url, settings):
    """Direct LAN prototype, not an Internet camera proxy or DNS resolver."""
    if settings is None:
        raise HTTPException(503, 'Chưa khởi tạo cấu hình camera.')
    if any(urlsplit(origin).hostname not in ('127.0.0.1', 'localhost', '::1') for origin in settings.origins):
        raise HTTPException(403, 'Camera mạng nội bộ hiện dùng với backend local. Bản website công khai cần cổng kết nối camera riêng.')
    try:
        parts = urlsplit(public_url)
        ip = ipaddress.IPv4Address(parts.hostname)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if parts.scheme not in ('http', 'https') or parts.username is not None or parts.query or parts.fragment:
            raise ValueError()
        if not any(ip in network for network in PRIVATE_NETWORKS):
            raise ValueError()
        if port not in settings.camera_ports:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, 'Nhập IP camera trong mạng nội bộ; cổng mặc định hỗ trợ 80, 443, 8080, 8081.')

class WarehouseCamera:
    def __init__(self, factory):
        self.factory = factory
        self.readers = {}
        self.registry_lock = threading.RLock()
        self.settings = None
        self.store = None
        self.gateway = None

    def _reader(self):
        key = current_warehouse_id()
        with self.registry_lock:
            if key not in self.readers:
                self.readers[key] = self.factory()
            return self.readers[key]

    def configure(self, public_url, private_url):
        validate_camera_address(public_url, self.settings)
        with self.registry_lock:
            reader = self._reader()
            if getattr(reader,'_gateway_id',None) and reader.private_url:
                raise HTTPException(409,'Ngắt cầu nối hiện tại trước khi đổi sang camera LAN.')
            active = sum(bool(r.private_url) for r in self.readers.values())
            if not reader.private_url and active >= self.settings.max_cameras:
                raise HTTPException(503, 'Đã đủ số camera đồng thời của máy thử nghiệm.')
            reader._gateway_id=None
            reader.start()
            reader.configure(public_url, private_url)

    def disconnect(self):
        if self.gateway is not None:self.gateway.revoke(current_warehouse_id())
        reader = self._reader()
        reader.disconnect()
        reader.close()

    def close_all(self):
        with self.registry_lock:
            readers = list(self.readers.values())
            self.readers.clear()
        for reader in readers:
            reader.close()

    def disconnect_warehouse(self, warehouse_id):
        """Administrative revocation touches only an existing target reader."""
        if self.gateway is not None:self.gateway.revoke(warehouse_id)
        with self.registry_lock:
            reader=self.readers.get(warehouse_id)
            if reader is not None:reader.disconnect()
        if reader is not None:reader.close()

    def ensure_gateway_available(self,warehouse_id):
        with self.registry_lock:
            reader=self.readers.get(warehouse_id)
            if reader is not None and reader.private_url:
                raise HTTPException(409,'Ngắt camera hoặc cầu nối hiện tại trước khi tạo mã mới.')
            if sum(bool(r.private_url) for r in self.readers.values())>=self.settings.max_cameras:
                raise HTTPException(503,'Đã đủ số nguồn camera đồng thời của máy chủ.')

    def enable_gateway(self,warehouse_id,channel_id):
        with self.registry_lock:
            self.ensure_gateway_available(warehouse_id)
            reader=self.readers.get(warehouse_id)
            if reader is None:
                reader=self.factory();self.readers[warehouse_id]=reader
            # No native decoder/encoder thread is started for push input.
            reader.close()
            if any(worker is not None and worker.is_alive() for worker in (reader.thread,reader.encoder_thread)):
                raise HTTPException(503,'Nguồn camera cũ chưa đóng xong. Đợi ít giây rồi ghép lại.')
            with reader.updated:
                reader.revision+=1;reader.stop_event.clear();reader.clear_frame()
                reader._gateway_id=channel_id;reader._gateway_sequence=0
                reader.private_url='gateway:'+channel_id;reader.public_url='Camera qua cầu nối'
                reader.state='connecting';reader.message='Đang chờ ảnh từ cầu nối tại kho.'
                reader.updated.notify_all()

    def publish_gateway(self,warehouse_id,channel_id,sequence,jpeg,frame,source_age=0):
        with self.registry_lock:
            reader=self.readers.get(warehouse_id)
            if reader is None:raise HTTPException(410,'Nguồn camera đã dừng; ghép lại cầu nối.')
            with reader.updated:
                if (getattr(reader,'_gateway_id',None)!=channel_id or reader.private_url!='gateway:'+channel_id
                        or reader.stop_event.is_set()):raise HTTPException(410,'Nguồn camera đã đổi hoặc đã ngắt.')
                if sequence<=reader._gateway_sequence:raise HTTPException(409,'Ảnh lặp hoặc đến sai thứ tự; chưa cập nhật nguồn.')
                now=time.monotonic();reader._gateway_sequence=sequence;reader.sequence+=1
                reader.frame=frame;reader.frame_time=now-source_age
                reader.received_at=datetime.now(timezone.utc).isoformat()
                reader.preview_jpeg=jpeg;reader.preview_sequence=reader.sequence
                reader.preview_time=reader.frame_time;reader.preview_height,reader.preview_width=frame.shape[:2]
                reader.encode_ms=0.0;reader.preview_error='';reader.source_fps=3.0
                reader.read_times.append(now);reader.encode_times.append(now)
                reader.state='online';reader.message='Đang nhận ảnh qua cầu nối HTTPS.'
                reader.updated.notify_all()

    def disable_gateway(self,warehouse_id,channel_id):
        with self.registry_lock:
            reader=self.readers.get(warehouse_id)
            if reader is not None and getattr(reader,'_gateway_id',None)==channel_id and reader.private_url=='gateway:'+channel_id:
                reader.disconnect();reader.close()

    def status(self):
        reader=self._reader();result=reader.status()
        result['source']='gateway' if reader.private_url and reader.private_url=='gateway:'+str(getattr(reader,'_gateway_id',None)) else 'lan'
        return result

    def __getattr__(self, name):
        return getattr(self._reader(), name)
