"""NFR-13: URL policy tests never open a network connection or decoder."""
import io
import unittest
from types import SimpleNamespace
from fastapi import HTTPException
from backend.app.warehouse_camera import WarehouseCamera, validate_camera_address
from backend.app.warehouse_scope import warehouse_scope


class CameraNetworkTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(origins=('http://127.0.0.1:5173',), camera_ports=(80,443,8080,8081), max_cameras=4)

    def test_lan_addresses_remain_allowed(self):
        for url in ['http://192.168.1.24:8081/live.flv','https://10.1.2.3/live','http://172.16.1.1:8080/video']:
            with self.subTest(url=url): validate_camera_address(url,self.settings)

    def test_explicit_zero_port_is_rejected(self):
        with self.assertRaises(HTTPException): validate_camera_address('http://192.168.1.24:0/live.flv',self.settings)

    def test_forbidden_networks_and_protocols(self):
        for url in ['http://127.0.0.1:8081/live.flv','http://169.254.169.254/live','http://100.100.100.200/live',
                    'http://8.8.8.8/live','http://0.0.0.0/live','http://224.0.0.1/live',
                    'http://localhost/live','http://camera.example/live','http://[::1]/live',
                    'file:///tmp/secret','rtsp://192.168.1.24:8081/live','http://192.168.1.24:9999/live',
                    'http://192.168.1.24:65536/live','http://192.168.1.24:notaport/live']:
            with self.subTest(url=url), self.assertRaises(HTTPException):validate_camera_address(url,self.settings)

    def test_url_credentials_queries_and_fragments_rejected_without_echo(self):
        for url in ['http://fixture-user:test-password-123@192.168.1.24/live',
                    'http://192.168.1.24/live?token=test-secret','http://192.168.1.24/live#test-secret']:
            with self.subTest(url=url),self.assertRaises(HTTPException) as caught:validate_camera_address(url,self.settings)
            self.assertNotIn('test-password',str(caught.exception.detail));self.assertNotIn('test-secret',str(caught.exception.detail))

    def test_public_origin_does_not_enable_direct_lan_proxy(self):
        self.settings.origins=('https://warehouse.example',)
        with self.assertRaises(HTTPException) as caught:validate_camera_address('http://192.168.1.24:8081/live',self.settings)
        self.assertEqual(caught.exception.status_code,403)

    def test_invalid_input_never_starts_reader(self):
        calls=[]
        def factory(): calls.append('created');raise AssertionError('Should not create reader')
        camera=WarehouseCamera(factory);camera.settings=self.settings
        with warehouse_scope('a'*32),self.assertRaises(HTTPException):camera.configure('http://127.0.0.1/live','fixture')
        self.assertEqual(calls,[])
