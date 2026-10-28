"""Private support conversations and unverified public access-help requests."""
import hashlib
import hmac
from uuid import uuid4

from fastapi import HTTPException

SCHEMA = '''
CREATE TABLE IF NOT EXISTS support_tickets (
 ticket_id TEXT PRIMARY KEY,
 user_id TEXT REFERENCES auth_users(user_id),
 warehouse_id TEXT REFERENCES auth_warehouses(warehouse_id),
 guest_key_hash TEXT UNIQUE,
 claimed_username TEXT,
 contact TEXT,
 request_id TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 subject TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('camera','inventory','account','other')),
 state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','in_progress','resolved')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(user_id,request_id),
 CHECK((user_id IS NOT NULL AND warehouse_id IS NOT NULL AND guest_key_hash IS NULL)
       OR (user_id IS NULL AND warehouse_id IS NULL AND guest_key_hash IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS support_messages (
 message_id TEXT PRIMARY KEY,
 sequence BIGSERIAL UNIQUE NOT NULL,
 ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id),
 author_id TEXT REFERENCES auth_users(user_id),
 author_role TEXT NOT NULL CHECK(author_role IN ('user','admin','guest')),
 body TEXT NOT NULL,
 client_nonce TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(ticket_id,client_nonce)
);
ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS recovery_email TEXT;
ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS password_reset_at TIMESTAMPTZ;
ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS reset_user_id TEXT REFERENCES auth_users(user_id);
CREATE INDEX IF NOT EXISTS support_ticket_owner ON support_tickets(user_id,updated_at);
CREATE INDEX IF NOT EXISTS support_ticket_state ON support_tickets(state,updated_at);
CREATE INDEX IF NOT EXISTS support_message_ticket ON support_messages(ticket_id,sequence);
'''

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def fingerprint(values):
    import json
    return digest(json.dumps(values,sort_keys=True,ensure_ascii=False))

class SupportService:
    def __init__(self,store):
        self.store=store

    def initialize(self):
        with self.store.pool.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(21456006)')
            c.execute(SCHEMA)

    def authorize(self,c,identity,token_hash):
        row=c.execute('''SELECT u.user_id FROM auth_users u JOIN auth_sessions s ON s.user_id=u.user_id
            WHERE u.user_id=%s AND u.account_version=%s AND u.role=%s AND u.is_active=TRUE
            AND u.must_change_password=FALSE AND s.account_version=u.account_version
            AND s.token_hash=%s AND s.expires_at>now()
            AND (u.temporary_password_expires_at IS NULL OR u.temporary_password_expires_at>now())
            FOR SHARE OF u''',(identity['user_id'],identity['account_version'],identity['role'],token_hash)).fetchone()
        if not row:
            raise HTTPException(401,'Phiên đăng nhập không còn hiệu lực.')

    def create(self,values,identity=None,token_hash=None):
        guest=identity is None
        key_hash=digest(values['guest_secret']) if guest else None
        request_hash=fingerprint({k:v for k,v in values.items() if k!='guest_secret'})
        with self.store.pool.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(21456007)')
            if not guest:self.authorize(c,identity,token_hash)
            previous=c.execute('SELECT * FROM support_tickets WHERE guest_key_hash=%s' if guest else
                               'SELECT * FROM support_tickets WHERE user_id=%s AND request_id=%s',
                               (key_hash,) if guest else (identity['user_id'],values['request_id'])).fetchone()
            if previous:
                if not hmac.compare_digest(previous['request_hash'],request_hash):
                    raise HTTPException(409,'Mã gửi đã dùng cho nội dung khác.')
                return self.public(previous)
            if not guest:
                count=c.execute("SELECT COUNT(*) n FROM support_tickets WHERE user_id=%s AND state<>'resolved'",
                                (identity['user_id'],)).fetchone()['n']
                if count>=10:raise HTTPException(429,'Bạn đang có 10 yêu cầu chưa hoàn tất. Hãy tiếp tục trong yêu cầu đã có.')
            tid=uuid4().hex
            row=c.execute('''INSERT INTO support_tickets(ticket_id,user_id,warehouse_id,guest_key_hash,
                claimed_username,contact,request_id,request_hash,subject,category,recovery_email)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (tid,None if guest else identity['user_id'],None if guest else identity['warehouse_id'],key_hash,
                 values.get('username') if guest else None,values.get('contact') if guest else None,
                 values['request_id'],request_hash,values['subject'],values['category'],values.get('email') if guest else None)).fetchone()
            c.execute('''INSERT INTO support_messages(message_id,ticket_id,author_id,author_role,body,client_nonce)
                VALUES (%s,%s,%s,%s,%s,%s)''',
                (uuid4().hex,tid,None if guest else identity['user_id'],'guest' if guest else 'user',values['body'],values['request_id']))
            self.store.audit(c,None if guest else identity['user_id'],row['warehouse_id'],'support_create:'+tid)
        return self.public(row)

    @staticmethod
    def public(row):
        fields=('ticket_id','user_id','warehouse_id','subject','category','state','created_at','updated_at')
        return {k:row[k] for k in fields}|{'requester_type':'user' if row['user_id'] else 'guest',
            'username':row.get('username') or row.get('claimed_username'),'contact':row.get('contact'),
            'email':row.get('recovery_email')}

    def ticket(self,c,tid,identity=None,guest_secret=None,lock=False):
        tail=' FOR UPDATE OF t' if lock else ''
        row=c.execute('''SELECT t.*,u.username FROM support_tickets t LEFT JOIN auth_users u ON u.user_id=t.user_id
            WHERE t.ticket_id=%s'''+tail,(tid,)).fetchone()
        allowed=row is not None
        if allowed and guest_secret is not None:
            allowed=row['user_id'] is None and hmac.compare_digest(row['guest_key_hash'] or '',digest(guest_secret))
        elif allowed and identity is not None:
            allowed=identity['role']=='admin' or (
                row['user_id']==identity['user_id'] and row['warehouse_id']==identity['warehouse_id'])
        else:allowed=False
        if not allowed:raise HTTPException(404,'Không tìm thấy yêu cầu hỗ trợ.')
        return row

    def listing(self,identity,state,limit,offset):
        with self.store.pool.connection() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            clauses=[];params=[]
            if identity['role']!='admin':
                clauses.append('t.user_id=%s AND t.warehouse_id=%s')
                params += [identity['user_id'],identity['warehouse_id']]
            if state!='all':
                clauses.append('t.state=%s');params.append(state)
            where=' WHERE '+' AND '.join(clauses) if clauses else ''
            base=' FROM support_tickets t LEFT JOIN auth_users u ON u.user_id=t.user_id'
            total=c.execute('SELECT COUNT(*) n'+base+where,params).fetchone()['n']
            rows=c.execute('SELECT t.*,u.username'+base+where+' ORDER BY t.updated_at DESC,t.ticket_id LIMIT %s OFFSET %s',
                           (*params,limit,offset)).fetchall()
        return {'tickets':[self.public(r) for r in rows],'total':total,'limit':limit,'offset':offset}

    def read(self,tid,identity=None,guest_secret=None,limit=50,offset=0):
        with self.store.pool.connection() as c:
            row=self.ticket(c,tid,identity,guest_secret)
            if identity and identity['role']=='admin':
                self.store.audit(c,identity['user_id'],row['warehouse_id'],'support_read:'+tid)
            total=c.execute('SELECT COUNT(*) n FROM support_messages WHERE ticket_id=%s',(tid,)).fetchone()['n']
            ticket=self.public(row)
            if identity and identity['role']=='admin' and row['user_id'] is None:
                from backend.app.recovery_email import recovery_status
                ticket['recovery']=recovery_status(c,row)
            messages=c.execute('''SELECT message_id,sequence,author_role,body,created_at FROM support_messages
                WHERE ticket_id=%s ORDER BY sequence DESC LIMIT %s OFFSET %s''',(tid,limit,offset)).fetchall()
        return {'ticket':ticket,'messages':list(reversed(messages)),'total':total,'limit':limit,'offset':offset}

    def message(self,tid,body,nonce,identity=None,token_hash=None,guest_secret=None):
        with self.store.pool.connection() as c:
            if identity:self.authorize(c,identity,token_hash)
            row=self.ticket(c,tid,identity,guest_secret,lock=True)
            role='guest' if identity is None else identity['role']
            previous=c.execute('SELECT body,author_id,author_role FROM support_messages WHERE ticket_id=%s AND client_nonce=%s',
                               (tid,nonce)).fetchone()
            if previous:
                if previous['body']!=body or previous['author_role']!=role or previous['author_id']!=(identity['user_id'] if identity else None):
                    raise HTTPException(409,'Mã gửi đã dùng cho nội dung khác.')
                return {'ticket_id':tid,'already_sent':True}
            count=c.execute('SELECT COUNT(*) n FROM support_messages WHERE ticket_id=%s',(tid,)).fetchone()['n']
            if count>=200:raise HTTPException(409,'Hội thoại đã đạt giới hạn 200 tin. Hãy tạo yêu cầu mới nếu cần.')
            c.execute('''INSERT INTO support_messages(message_id,ticket_id,author_id,author_role,body,client_nonce)
                VALUES (%s,%s,%s,%s,%s,%s)''',(uuid4().hex,tid,identity['user_id'] if identity else None,role,body,nonce))
            state='in_progress' if role=='admin' else 'open'
            c.execute('UPDATE support_tickets SET state=%s,updated_at=clock_timestamp() WHERE ticket_id=%s',(state,tid))
            self.store.audit(c,identity['user_id'] if identity else None,row['warehouse_id'],'support_message:'+tid)
        return {'ticket_id':tid,'already_sent':False}

    def change_state(self,tid,state,identity,token_hash):
        if identity['role']!='admin':raise HTTPException(403,'Chỉ admin được cập nhật trạng thái hỗ trợ.')
        with self.store.pool.connection() as c:
            self.authorize(c,identity,token_hash)
            row=self.ticket(c,tid,identity,lock=True)
            c.execute('UPDATE support_tickets SET state=%s,updated_at=clock_timestamp() WHERE ticket_id=%s',(state,tid))
            self.store.audit(c,identity['user_id'],row['warehouse_id'],'support_state:'+state+':'+tid)
        return {'ticket_id':tid,'state':state}

    def dashboard(self):
        with self.store.pool.connection() as c:
            users=c.execute("""SELECT COUNT(*) FILTER(WHERE role='user') AS user_count,
                COUNT(*) FILTER(WHERE role='user' AND is_active) AS active_users,
                COUNT(*) FILTER(WHERE role='user' AND NOT is_active) AS locked_users
                FROM auth_users""").fetchone()
            warehouses=c.execute('SELECT COUNT(*) n FROM auth_warehouses').fetchone()['n']
            tickets=c.execute("""SELECT COUNT(*) n,COUNT(*) FILTER(WHERE state='open') AS waiting,
                COUNT(*) FILTER(WHERE state='in_progress') AS in_progress,
                COUNT(*) FILTER(WHERE state='resolved') AS resolved FROM support_tickets""").fetchone()
        return {**dict(users),'warehouse_count':warehouses,'support':dict(tickets)}


    def recovery_requests(self,user_id,actor_id):
        from backend.app.recovery_email import recovery_status
        with self.store.pool.connection() as c:
            user=c.execute("SELECT * FROM auth_users WHERE user_id=%s AND role='user'",(user_id,)).fetchone()
            if not user:raise HTTPException(404,'Không tìm thấy tài khoản user.')
            rows=c.execute("""SELECT * FROM support_tickets WHERE user_id IS NULL AND category='account'
                AND lower(trim(claimed_username))=%s AND recovery_email=%s AND password_reset_at IS NULL
                AND state<>'resolved' ORDER BY created_at DESC,ticket_id LIMIT 20""",(user['username'],user['email'])).fetchall()
            eligible=[self.public(row) for row in rows if recovery_status(c,row,user)['can_reset']]
            self.store.audit(c,actor_id,None,'admin_read_recovery_requests:'+user_id)
        return {'requests':eligible,'email':user['email']}
