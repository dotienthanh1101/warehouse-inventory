"""In-app support. Public access-help requests never grant account access."""
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Query, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.auth_api import admin_identity
from backend.app.auth_store import COOKIE, token_digest
from backend.app.recovery_email import normalize_email

router=APIRouter(prefix='/api',tags=['Support'])
Id=Annotated[str,Path(pattern='^[0-9a-f]{32}$')]

class Content(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(pattern='^[0-9a-f]{32}$')
    body:str=Field(min_length=1,max_length=4000)
    @field_validator('body')
    @classmethod
    def meaningful(cls,value):
        if not value.strip():raise ValueError('Nhập nội dung hỗ trợ.')
        return value.strip()

class CreateTicket(Content):
    subject:str=Field(min_length=3,max_length=120)
    category:str=Field(pattern='^(camera|inventory|account|other)$')
    @field_validator('subject')
    @classmethod
    def title(cls,value):
        if len(value.strip())<3:raise ValueError('Tiêu đề cần ít nhất 3 ký tự.')
        return value.strip()

class GuestTicket(CreateTicket):
    username:str=Field(min_length=1,max_length=64)
    contact:str=Field(default='',max_length=160)
    email:str=Field(min_length=3,max_length=254)
    @field_validator('email')
    @classmethod
    def valid_email(cls,value):return normalize_email(value)
    guest_secret:str=Field(pattern='^[0-9a-f]{64}$')
    @field_validator('username')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():raise ValueError('Không để trống thông tin liên hệ.')
        return value.strip()

class GuestAccess(BaseModel):
    model_config=ConfigDict(extra='forbid')
    access_code:str=Field(min_length=97,max_length=97)

class GuestMessage(Content):
    access_code:str=Field(min_length=97,max_length=97)

class StateChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    state:str=Field(pattern='^(open|in_progress|resolved)$')

def service(request):return request.app.state.support_service
def token(request):return token_digest(request.cookies.get(COOKIE,''))
def ip(request):return request.client.host if request.client else 'unknown'
def decode(code):
    if not re.fullmatch('[0-9a-f]{32}\\.[0-9a-f]{64}',code):
        raise HTTPException(404,'Không tìm thấy yêu cầu hỗ trợ.')
    return code.split('.',1)

@router.post('/support/guest',status_code=201)
def create_guest(body:GuestTicket,request:Request):
    store=request.app.state.auth_store
    store.rate_limit('support-guest-create',ip(request),5,3600)
    store.rate_limit('support-guest-global','all',100,3600)
    result=service(request).create(body.model_dump())
    return {'ticket':result,'access_code':result['ticket_id']+'.'+body.guest_secret,
            'message':'Đã gửi yêu cầu. Giữ mã hỗ trợ để xem và trả lời admin.'}

@router.post('/support/guest/read')
def read_guest(body:GuestAccess,request:Request,limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0)):
    request.app.state.auth_store.rate_limit('support-guest-read',ip(request),120,60)
    tid,key=decode(body.access_code)
    return service(request).read(tid,guest_secret=key,limit=limit,offset=offset)

@router.post('/support/guest/reply')
def reply_guest(body:GuestMessage,request:Request):
    store=request.app.state.auth_store
    store.rate_limit('support-guest-reply',ip(request),20,300)
    tid,key=decode(body.access_code)
    return service(request).message(tid,body.body,body.request_id,guest_secret=key)

@router.get('/support/tickets')
def list_own(request:Request,state:str=Query(default='all',pattern='^(all|open|in_progress|resolved)$'),
             limit:int=Query(default=20,ge=1,le=100),offset:int=Query(default=0,ge=0)):
    return service(request).listing(request.state.identity,state,limit,offset)

@router.post('/support/tickets',status_code=201)
def create_own(body:CreateTicket,request:Request):
    actor=request.state.identity
    request.app.state.auth_store.rate_limit('support-user-create',actor['user_id'],10,3600)
    return {'ticket':service(request).create(body.model_dump(),actor,token(request))}

@router.get('/support/tickets/{ticket_id}')
def read_own(ticket_id:Id,request:Request,limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0)):
    return service(request).read(ticket_id,request.state.identity,limit=limit,offset=offset)

@router.post('/support/tickets/{ticket_id}/messages')
def reply_own(ticket_id:Id,body:Content,request:Request):
    actor=request.state.identity
    request.app.state.auth_store.rate_limit('support-user-reply',actor['user_id'],30,300)
    return service(request).message(ticket_id,body.body,body.request_id,actor,token(request))

@router.get('/admin/dashboard')
def admin_dashboard(request:Request):
    admin_identity(request)
    return service(request).dashboard()

@router.get('/admin/support/tickets')
def list_admin(request:Request,state:str=Query(default='all',pattern='^(all|open|in_progress|resolved)$'),
               limit:int=Query(default=20,ge=1,le=100),offset:int=Query(default=0,ge=0)):
    actor=admin_identity(request)
    return service(request).listing(actor,state,limit,offset)

@router.get('/admin/support/tickets/{ticket_id}')
def read_admin(ticket_id:Id,request:Request,limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0)):
    actor=admin_identity(request)
    return service(request).read(ticket_id,actor,limit=limit,offset=offset)

@router.post('/admin/support/tickets/{ticket_id}/messages')
def reply_admin(ticket_id:Id,body:Content,request:Request):
    actor=admin_identity(request)
    request.app.state.auth_store.rate_limit('support-admin-reply',actor['user_id'],60,300)
    return service(request).message(ticket_id,body.body,body.request_id,actor,token(request))

@router.patch('/admin/support/tickets/{ticket_id}')
def update_state(ticket_id:Id,body:StateChange,request:Request):
    actor=admin_identity(request)
    return service(request).change_state(ticket_id,body.state,actor,token(request))

