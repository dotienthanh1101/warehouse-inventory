"""One-process authorization barrier: finish an accepted write before revocation.

Application is intentionally run with one worker (camera/GPU state is in memory).
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import HTTPException

@asynccontextmanager
async def account_gate(app, user_id):
    gates=getattr(app.state,'account_gates',None)
    if gates is None:
        gates={}
        app.state.account_gates=gates
    lock=gates.setdefault(user_id,asyncio.Lock())
    try:
        await asyncio.wait_for(lock.acquire(),timeout=30)
    except TimeoutError:
        raise HTTPException(409,'Tài khoản đang có thao tác xử lý. Chờ hoàn tất rồi thử lại.')
    try:yield
    finally:lock.release()
