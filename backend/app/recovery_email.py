"""Email normalization and admin-only password recovery eligibility.

An email match is a prerequisite, never proof of mailbox ownership.
Public support responses must not include the results from recovery_status.
"""
import re


def normalize_email(value):
    value = value.strip()
    if len(value) > 254 or value.count('@') != 1:
        raise ValueError('Nhập địa chỉ email hợp lệ.')
    local, domain = value.rsplit('@', 1)
    try:
        domain = domain.encode('idna').decode('ascii').lower()
    except UnicodeError as error:
        raise ValueError('Nhập địa chỉ email hợp lệ.') from error
    if (not 1 <= len(local) <= 64 or local.startswith('.') or local.endswith('.')
            or '..' in local or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)):
        raise ValueError('Nhập địa chỉ email hợp lệ.')
    labels = domain.split('.')
    if len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in labels):
        raise ValueError('Nhập địa chỉ email hợp lệ.')
    result = local.lower() + '@' + domain
    if len(result) > 254:
        raise ValueError('Nhập địa chỉ email hợp lệ.')
    return result


def recovery_status(c, ticket, user=None):
    """Call only inside an authenticated admin read or a locked reset write."""
    if user is None:
        user = c.execute("""SELECT user_id,username,email,email_updated_at,account_version,is_active
            FROM auth_users WHERE username=%s AND role='user'""",
            ((ticket.get('claimed_username') or '').strip().lower(),)).fetchone()
    status = 'matched'
    if ticket.get('user_id') is not None or ticket.get('category') != 'account':
        status = 'not_recovery_request'
    elif user is None or user['username'] != (ticket.get('claimed_username') or '').strip().lower():
        status = 'account_not_found'
    elif not user.get('email'):
        status = 'account_email_missing'
    elif not ticket.get('recovery_email'):
        status = 'request_email_missing'
    elif ticket['recovery_email'] != user['email']:
        status = 'email_mismatch'
    elif user.get('email_updated_at') and ticket['created_at'] < user['email_updated_at']:
        status = 'email_changed'
    elif ticket.get('password_reset_at'):
        status = 'already_used'
    elif ticket['state'] == 'resolved':
        status = 'resolved'
    elif not user['is_active']:
        status = 'account_locked'
    return {
        'status': status, 'can_reset': status == 'matched',
        'request_email': ticket.get('recovery_email'),
        'registered_email': user['email'] if user else None,
        'user_id': user['user_id'] if user else None,
        'account_version': user['account_version'] if user else None,
    }
