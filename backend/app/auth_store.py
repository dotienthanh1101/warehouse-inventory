"""PostgreSQL identity store. Inventory remains in one SQLite per warehouse."""
import hashlib
import hmac
import secrets
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException
from psycopg import IntegrityError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.app.warehouse_scope import STORAGE_ROOT, warehouse_scope

COOKIE = 'warehouse_session'
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
HASH_SLOTS = threading.BoundedSemaphore(4)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS auth_users (
 user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
 display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('user','admin')),
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth_warehouses (
 warehouse_id TEXT PRIMARY KEY,
 owner_id TEXT NOT NULL UNIQUE REFERENCES auth_users(user_id),
 name TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth_camera_permissions (
 warehouse_id TEXT NOT NULL REFERENCES auth_warehouses(warehouse_id),
 host TEXT NOT NULL UNIQUE, PRIMARY KEY(warehouse_id,host)
);
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_audit_logs (
 event_id TEXT PRIMARY KEY, actor_id TEXT REFERENCES auth_users(user_id),
 warehouse_id TEXT, action TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth_rate_limits (
 rate_key TEXT NOT NULL, bucket BIGINT NOT NULL, count INTEGER NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(rate_key,bucket)
);
CREATE INDEX IF NOT EXISTS auth_rate_expiry ON auth_rate_limits(expires_at);
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS account_version BIGINT NOT NULL DEFAULT 1;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS temporary_password_expires_at TIMESTAMPTZ;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS email_updated_at TIMESTAMPTZ;
ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS account_version BIGINT NOT NULL DEFAULT 1;
'''

def token_digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def hash_password(password):
    if not HASH_SLOTS.acquire(timeout=10):
        raise HTTPException(429, 'Đăng nhập đang bận. Thử lại sau ít giây.', headers={'Retry-After': '10'})
    try:
        return PASSWORD_HASHER.hash(password)
    finally:
        HASH_SLOTS.release()

def verify_password(stored_hash, password):
    if not HASH_SLOTS.acquire(timeout=10):
        raise HTTPException(429, 'Đăng nhập đang bận. Thử lại sau ít giây.', headers={'Retry-After': '10'})
    try:
        try:
            return PASSWORD_HASHER.verify(stored_hash, password)
        except (VerificationError, InvalidHashError):
            return False
    finally:
        HASH_SLOTS.release()

class AuthStore:
    def __init__(self, settings):
        self.settings = settings.validate()
        self.pool = ConnectionPool(settings.database_url, min_size=min(2, settings.pool_size), max_size=settings.pool_size,
                                   timeout=10, open=False, kwargs={'row_factory': dict_row, 'prepare_threshold': None})
        self.dummy_hash = hash_password(secrets.token_urlsafe(32))

    def open(self):
        self.pool.open(wait=True, timeout=15)

    def initialize(self):
        with self.pool.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(21456001)")
            c.execute(SCHEMA)
            c.execute('DELETE FROM auth_sessions WHERE expires_at <= now()')
            c.execute('DELETE FROM auth_rate_limits WHERE expires_at <= now()')

    def close(self):
        self.pool.close()

    def csrf_token(self, raw_token):
        return hmac.new(self.settings.secret.encode(), ('csrf:' + raw_token).encode(), hashlib.sha256).hexdigest()

    def rate_limit(self, namespace, subject, maximum, seconds):
        key = hmac.new(self.settings.secret.encode(), (namespace + ':' + subject).encode(), hashlib.sha256).hexdigest()
        bucket = int(time.time()) // seconds
        with self.pool.connection() as c:
            row = c.execute('''INSERT INTO auth_rate_limits VALUES (%s,%s,1,%s)
                ON CONFLICT(rate_key,bucket) DO UPDATE SET count=auth_rate_limits.count+1
                RETURNING count''', (key, bucket, datetime.now(timezone.utc) + timedelta(seconds=seconds * 2))).fetchone()
        if row['count'] > maximum:
            raise HTTPException(429, 'Quá nhiều lần thử. Vui lòng thử lại sau.', headers={'Retry-After': str(seconds)})

    @staticmethod
    def audit(c, actor, warehouse, action):
        c.execute('INSERT INTO auth_audit_logs(event_id,actor_id,warehouse_id,action) VALUES (%s,%s,%s,%s)',
                  (uuid4().hex, actor, warehouse, action))

    def register(self, username, display_name, password, warehouse_name, role='user', initialize=None, email=None):
        from backend.app.server_capacity import capacity_lock, require_new_warehouse
        with capacity_lock:
            if role == 'user':
                require_new_warehouse(self)
            return self._register(username, display_name, password, warehouse_name, role, initialize, email)

    def _register(self, username, display_name, password, warehouse_name, role='user', initialize=None, email=None):
        """Public request schema never exposes role. CLI alone can bootstrap admin."""
        from backend.app.database import initialize_database
        initialize = initialize or initialize_database
        if role not in ('user', 'admin'):
            raise ValueError('Vai trò không hợp lệ.')
        if email is not None:
            from backend.app.recovery_email import normalize_email
            email = normalize_email(email)
        user_id = uuid4().hex
        warehouse_id = uuid4().hex if role == 'user' else None
        folder = STORAGE_ROOT / warehouse_id if warehouse_id else None
        password_hash = hash_password(password)
        created_folder = False
        try:
            with self.pool.connection() as c:
                if role == 'admin':
                    c.execute('SELECT pg_advisory_xact_lock(21456002)')
                    if c.execute("SELECT 1 FROM auth_users WHERE role='admin'").fetchone():
                        raise ValueError('Đã có admin. Không thể khởi tạo admin lần nữa.')
                c.execute('''INSERT INTO auth_users(user_id,username,display_name,password_hash,role,email,email_updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,CASE WHEN %s::text IS NULL THEN NULL ELSE clock_timestamp() END)''',
                          (user_id, username.lower().strip(), display_name, password_hash, role, email, email))
                if warehouse_id:
                    c.execute('INSERT INTO auth_warehouses(warehouse_id,owner_id,name) VALUES (%s,%s,%s)',
                              (warehouse_id, user_id, warehouse_name))
                    folder.mkdir(parents=True, exist_ok=False)
                    created_folder = True
                    with warehouse_scope(warehouse_id):
                        initialize()
                        (folder / 'captures').mkdir()
                        (folder / 'data' / 'reviews').mkdir(parents=True, exist_ok=True)
                self.audit(c, user_id, warehouse_id, 'admin_created' if role == 'admin' else 'registered')
        except Exception:
            if created_folder:
                # A connection may fail after the server committed. Preserve files
                # unless a fresh transaction proves that no account was committed.
                committed_or_unknown = True
                try:
                    with self.pool.connection() as check:
                        committed_or_unknown = check.execute('SELECT 1 FROM auth_users WHERE user_id=%s', (user_id,)).fetchone() is not None
                except Exception:
                    pass
                if not committed_or_unknown:
                    shutil.rmtree(folder, ignore_errors=True)
            raise
        return {'user': {'user_id': user_id, 'username': username.lower().strip(), 'display_name': display_name,
                         'role': role, 'email': email}, 'warehouse': {'warehouse_id': warehouse_id, 'name': warehouse_name} if warehouse_id else None}

    def login(self, username, password):
        with self.pool.connection() as c:
            row = c.execute('SELECT * FROM auth_users WHERE username=%s', (username.lower().strip(),)).fetchone()
        valid = verify_password(row['password_hash'] if row else self.dummy_hash, password)
        if not row or not valid or not row['is_active']:
            raise HTTPException(401, 'Tên đăng nhập hoặc mật khẩu không đúng.')
        token = secrets.token_urlsafe(48)
        expires = datetime.now(timezone.utc) + timedelta(hours=self.settings.session_hours)
        with self.pool.connection() as c:
            # Check again in the write transaction, including password replacement/disable races.
            current = c.execute('SELECT * FROM auth_users WHERE user_id=%s FOR UPDATE', (row['user_id'],)).fetchone()
            if (not current['is_active'] or current['password_hash'] != row['password_hash']
                    or current['account_version'] != row['account_version']):
                raise HTTPException(401, 'Tài khoản đã thay đổi. Hãy đăng nhập lại.')
            if current['temporary_password_expires_at'] and current['temporary_password_expires_at'] <= datetime.now(timezone.utc):
                raise HTTPException(401, 'Mật khẩu tạm đã hết hạn. Liên hệ admin để cấp lại.')
            c.execute('INSERT INTO auth_sessions(token_hash,user_id,expires_at,account_version) VALUES (%s,%s,%s,%s)',
                      (token_digest(token), row['user_id'], expires, current['account_version']))
            self.audit(c, row['user_id'], None, 'login')
        return token

    def authenticate(self, token):
        if not token or not 40 <= len(token) <= 128:
            raise HTTPException(401, 'Vui lòng đăng nhập.')
        with self.pool.connection() as c:
            row = c.execute('''SELECT u.user_id,u.username,u.display_name,u.role,u.email,u.must_change_password,
                u.account_version,
                w.warehouse_id,w.name AS warehouse_name
                FROM auth_sessions s JOIN auth_users u ON u.user_id=s.user_id
                LEFT JOIN auth_warehouses w ON w.owner_id=u.user_id AND u.role='user'
                WHERE s.token_hash=%s AND s.expires_at>now() AND u.is_active=TRUE
                AND s.account_version=u.account_version
                AND (u.temporary_password_expires_at IS NULL OR u.temporary_password_expires_at>now())''', (token_digest(token),)).fetchone()
        if not row:
            raise HTTPException(401, 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.')
        if row['role'] == 'user' and not row['warehouse_id']:
            raise HTTPException(403, 'Tài khoản chưa có kho hợp lệ. Liên hệ quản trị viên.')
        return row

    def logout(self, token):
        with self.pool.connection() as c:
            c.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (token_digest(token),))

    def profile(self, identity, raw_token):
        return {'user': {k: identity[k] for k in ('user_id', 'username', 'display_name', 'role', 'must_change_password', 'email')},
                'warehouse': {'warehouse_id': identity['warehouse_id'], 'name': identity['warehouse_name']} if identity['warehouse_id'] else None,
                'csrf_token': self.csrf_token(raw_token)}

    def list_users(self, search='', status='all', offset=0, limit=20):
        terms = search.strip().lower()
        where = """(%s='' OR strpos(lower(u.username),%s)>0 OR strpos(lower(u.display_name),%s)>0
                    OR strpos(lower(COALESCE(w.name,'')),%s)>0)
                    AND (%s='all' OR u.is_active=(%s='active'))"""
        values = (terms,terms,terms,terms,status,status)
        with self.pool.connection() as c:
            total=c.execute('SELECT COUNT(*) n FROM auth_users u LEFT JOIN auth_warehouses w ON w.owner_id=u.user_id WHERE '+where,values).fetchone()['n']
            rows=c.execute('''SELECT u.user_id,u.username,u.display_name,u.role,u.email,u.is_active,u.created_at,
                u.account_version,u.must_change_password,u.temporary_password_expires_at,
                w.warehouse_id,w.name AS warehouse_name,
                (SELECT COUNT(*) FROM auth_sessions s WHERE s.user_id=u.user_id AND s.expires_at>now()
                  AND s.account_version=u.account_version AND u.is_active=TRUE
                  AND (u.temporary_password_expires_at IS NULL OR u.temporary_password_expires_at>now())) AS active_sessions
                FROM auth_users u LEFT JOIN auth_warehouses w ON w.owner_id=u.user_id WHERE '''+where+
                ' ORDER BY u.created_at,u.user_id LIMIT %s OFFSET %s',values+(limit,offset)).fetchall()
        for row in rows:row['legacy_warehouse']=row['role']=='admin' and row['warehouse_id'] is not None
        return {'total':total,'users':rows,'offset':offset,'limit':limit}

    def user_detail(self, user_id, actor_id):
        with self.pool.connection() as c:
            row=c.execute('''SELECT u.user_id,u.username,u.display_name,u.role,u.email,u.is_active,u.created_at,
                u.account_version,u.must_change_password,u.temporary_password_expires_at,
                w.warehouse_id,w.name AS warehouse_name FROM auth_users u
                LEFT JOIN auth_warehouses w ON w.owner_id=u.user_id WHERE u.user_id=%s''',(user_id,)).fetchone()
            if not row:raise HTTPException(404,'Không tìm thấy tài khoản.')
            row['active_sessions']=c.execute('SELECT COUNT(*) n FROM auth_sessions WHERE user_id=%s AND expires_at>now() AND account_version=%s',
                (user_id,row['account_version'])).fetchone()['n']
            if not row['is_active'] or (row['temporary_password_expires_at'] and row['temporary_password_expires_at']<=datetime.now(timezone.utc)):
                row['active_sessions']=0
            row['recent_actions']=c.execute('''SELECT action,created_at FROM auth_audit_logs
                WHERE actor_id=%s ORDER BY created_at DESC,event_id DESC LIMIT 10''',(user_id,)).fetchall()
            self.audit(c,actor_id,row['warehouse_id'],'admin_view_account:'+user_id)
        return row

    def manage_user(self, actor_id, user_id, action, version, reason, admin_password='', actor_version=None, recovery_ticket_id=None, confirm_identity=False):
        if action not in ('lock','unlock','revoke','reset'):raise HTTPException(422,'Thao tác không hợp lệ.')
        temporary = secrets.token_urlsafe(18) if action=='reset' else None
        new_hash=hash_password(temporary) if temporary else None
        with self.pool.connection() as c:
            # Admin and target locks also protect direct service callers and role changes.
            actor=c.execute('SELECT * FROM auth_users WHERE user_id=%s FOR UPDATE',(actor_id,)).fetchone()
            if not actor or actor['role']!='admin' or not actor['is_active']:raise HTTPException(403,'Cần tài khoản admin đang hoạt động.')
            if actor_version is not None and actor['account_version']!=actor_version:raise HTTPException(401,'Phiên admin đã thay đổi. Đăng nhập lại.')
            if action=='reset' and not verify_password(actor['password_hash'],admin_password):
                raise HTTPException(403,'Mật khẩu xác nhận của admin không đúng.')
            row=c.execute('SELECT * FROM auth_users WHERE user_id=%s FOR UPDATE',(user_id,)).fetchone()
            if not row:raise HTTPException(404,'Không tìm thấy tài khoản.')
            if row['role']!='user' or row['user_id']==actor_id:raise HTTPException(409,'Chỉ quản lý tài khoản user tại đây; tài khoản admin được giữ nguyên.')
            if row['account_version']!=version:raise HTTPException(409,'Tài khoản đã thay đổi. Tải lại thông tin trước khi thao tác.')
            if action=='reset' and not row['is_active']:raise HTTPException(409,'Mở khóa tài khoản trước khi cấp mật khẩu tạm.')
            warehouse=c.execute('SELECT warehouse_id FROM auth_warehouses WHERE owner_id=%s',(user_id,)).fetchone()
            expires=datetime.now(timezone.utc)+timedelta(hours=24) if temporary else None
            if action=='reset':
                from backend.app.recovery_email import recovery_status
                if not recovery_ticket_id or confirm_identity is not True:
                    raise HTTPException(409,'Chọn yêu cầu khôi phục và xác nhận đã xác minh chủ email.')
                ticket=c.execute('SELECT * FROM support_tickets WHERE ticket_id=%s FOR UPDATE',(recovery_ticket_id,)).fetchone()
                if not ticket or not recovery_status(c,ticket,row)['can_reset']:
                    raise HTTPException(409,'Yêu cầu không đủ điều kiện: email phải khớp tài khoản, chưa dùng và chưa giải quyết.')
                c.execute('''UPDATE support_tickets SET password_reset_at=clock_timestamp(),reset_user_id=%s,
                    state='resolved',updated_at=clock_timestamp() WHERE ticket_id=%s''',(user_id,recovery_ticket_id))
                c.execute('''UPDATE auth_users SET password_hash=%s,must_change_password=TRUE,
                    temporary_password_expires_at=%s,account_version=account_version+1 WHERE user_id=%s''',(new_hash,expires,user_id))
            else:
                active=False if action=='lock' else True if action=='unlock' else row['is_active']
                c.execute('UPDATE auth_users SET is_active=%s,account_version=account_version+1 WHERE user_id=%s',(active,user_id))
            c.execute('DELETE FROM auth_sessions WHERE user_id=%s',(user_id,))
            # JSON action stores reason/target, never credentials.
            import json
            self.audit(c,actor_id,warehouse['warehouse_id'] if warehouse else None,
                json.dumps({'event':'admin_'+action,'target_user_id':user_id,'reason':reason,**({'recovery_ticket_id':recovery_ticket_id,'identity_confirmed':True} if action=='reset' else {})},ensure_ascii=False))
        return {'user_id':user_id,'account_version':version+1,'warehouse_id':warehouse['warehouse_id'] if warehouse else None,
                'temporary_password':temporary,'temporary_password_expires_at':expires}

    def change_email(self, identity, email, current_password):
        from backend.app.recovery_email import normalize_email
        email = normalize_email(email)
        with self.pool.connection() as c:
            row = c.execute('SELECT * FROM auth_users WHERE user_id=%s FOR UPDATE',
                            (identity['user_id'],)).fetchone()
            if (not row or row['role'] != 'user' or not row['is_active']
                    or row['must_change_password'] or row['account_version'] != identity['account_version']):
                raise HTTPException(409, 'Tài khoản đã thay đổi. Đăng nhập lại trước khi lưu email.')
            if not verify_password(row['password_hash'], current_password):
                raise HTTPException(403, 'Mật khẩu hiện tại không đúng.')
            if row['email'] == email:
                raise HTTPException(409, 'Email này đã được lưu. Không cần cập nhật lại.')
            c.execute('''UPDATE auth_users SET email=%s,email_updated_at=clock_timestamp(),
                account_version=account_version+1 WHERE user_id=%s''', (email, identity['user_id']))
            c.execute('DELETE FROM auth_sessions WHERE user_id=%s', (identity['user_id'],))
            self.audit(c, identity['user_id'], identity['warehouse_id'], 'recovery_email_changed')

    def change_password(self, identity, current_password, new_password):
        with self.pool.connection() as c:
            before=c.execute('SELECT * FROM auth_users WHERE user_id=%s',(identity['user_id'],)).fetchone()
        if not before or not verify_password(before['password_hash'],current_password):raise HTTPException(403,'Mật khẩu hiện tại không đúng.')
        if new_password==current_password:raise HTTPException(422,'Mật khẩu mới phải khác mật khẩu hiện tại.')
        replacement=hash_password(new_password)
        with self.pool.connection() as c:
            row=c.execute('SELECT * FROM auth_users WHERE user_id=%s FOR UPDATE',(identity['user_id'],)).fetchone()
            if (not row['is_active'] or row['password_hash']!=before['password_hash']
                    or row['account_version']!=identity['account_version']):
                raise HTTPException(409,'Tài khoản đã thay đổi. Đăng nhập lại trước khi đổi mật khẩu.')
            c.execute('''UPDATE auth_users SET password_hash=%s,must_change_password=FALSE,
                temporary_password_expires_at=NULL,account_version=account_version+1 WHERE user_id=%s''',(replacement,identity['user_id']))
            c.execute('DELETE FROM auth_sessions WHERE user_id=%s',(identity['user_id'],))
            self.audit(c,identity['user_id'],identity['warehouse_id'],'password_changed')


    # auth_camera_permissions is retained as historical data for source rollback.
    # It no longer grants access or reserves an IP address across users.
